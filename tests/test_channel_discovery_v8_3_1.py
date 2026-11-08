"""Regression tests: broadcast news channels are opt-in, groups remain compatible."""
import asyncio
from types import SimpleNamespace
from pathlib import Path
import pytest
from sqlalchemy import text
from telegram_monitor.cloud import collector
from telegram_monitor.cloud.source_registry import SourceRegistry, group_info, source_info


def news(ident=101, username='armenia_news', *, broadcast=True, megagroup=False, left=False):
    return SimpleNamespace(id=ident, username=username, title='Новости Армении',
                           broadcast=broadcast, megagroup=megagroup, left=left)


class Chat:
    def __init__(self, ident):
        self.id = ident
        self.title = 'Чат'
        self.deactivated = False
        self.migrated_to = None


def dialog(entity, *, is_group=False, is_channel=True):
    return SimpleNamespace(entity=entity, is_group=is_group, is_channel=is_channel)


@pytest.fixture
def registry(tmp_path):
    reg = SourceRegistry('sqlite:///'+str(tmp_path/'sources.db'), initialize=True)
    yield reg
    reg.dispose()


def test_news_channel_included_only_when_explicitly_enabled():
    d = dialog(news())
    assert source_info(d) is None
    assert group_info(d) is None
    item = source_info(d, include_channels=True)
    assert item == {'peer_id':101, 'username':'armenia_news', 'title':'Новости Армении',
                    'kind':'channel', 'visibility':'public'}


def test_private_broadcast_requires_approval(registry):
    registry.record_scan([])  # existing v8.3 baseline is already complete
    private = source_info(dialog(news(102, None)), include_channels=True)
    assert private['visibility'] == 'private'
    result = registry.record_scan([private], auto_public=True, auto_public_channels=True)
    assert result['found'] == 1
    assert registry.list_sources()[0]['status'] == 'pending'
    assert registry.approved_ids() == set()
    assert registry.change_status(102, 'approve') == 'approved'
    assert registry.approved_ids() == {102}


def test_existing_news_channel_after_upgrade_pending_even_if_public(registry):
    original_group = {'peer_id':42, 'username':None, 'title':'Группа', 'kind':'group', 'visibility':'private'}
    registry.record_scan([original_group])
    public_news = source_info(dialog(news(101)), include_channels=True)
    registry.record_scan([original_group, public_news], auto_public=False,
                         auto_public_channels=False)
    rows = {r['peer_id']:r for r in registry.list_sources()}
    assert rows[42]['status'] == 'baseline'
    assert rows[101]['status'] == 'pending'
    assert rows[101]['kind'] == 'channel'
    assert registry.change_status(101, 'approve') == 'approved'
    registry.record_scan([original_group, public_news], auto_public=False)
    assert registry.approved_ids() == {101}
    assert registry.recent_audit()[0]['peer_id'] == 101


def test_autojoin_public_channels_is_independent_of_groups(registry):
    registry.record_scan([])
    public_news = source_info(dialog(news(25)), include_channels=True)
    public_group = {'peer_id':26,'title':'Группа','username':'news_chat',
                    'kind':'megagroup','visibility':'public'}
    registry.record_scan([public_news, public_group], auto_public=True,
                         auto_public_channels=False)
    assert registry.approved_ids() == {26}


def test_optin_auto_public_channels_is_possible(registry):
    registry.record_scan([])
    public_news = source_info(dialog(news(25)), include_channels=True)
    registry.record_scan([public_news], auto_public=False, auto_public_channels=True)
    assert registry.approved_ids() == {25}


def test_no_false_positives_users_bots_megagroups_and_left_channels():
    assert source_info(dialog(news(1), is_channel=False), include_channels=True) is None
    assert source_info(dialog(news(1, broadcast=False)), include_channels=True) is None
    assert source_info(dialog(news(1, megagroup=True)), include_channels=True) is None
    assert source_info(dialog(news(1, left=True)), include_channels=True) is None
    assert source_info(dialog(SimpleNamespace(id=1, bot=True)), include_channels=True) is None
    assert source_info(dialog(news(-2)), include_channels=True) is None
    assert source_info(dialog(news(True)), include_channels=True) is None


def test_channel_and_group_discovered_metadata_only():
    class Client:
        def iter_dialogs(self, limit=None):
            assert limit is None
            async def rows():
                yield dialog(news(17))
                yield dialog(Chat(18), is_channel=False, is_group=True)
                yield dialog(SimpleNamespace(id=99, username='alice'), is_channel=False)
            return rows()
        def iter_messages(self, *_a, **_k):
            raise AssertionError('Scanning must not read messages')
    sources, entities = asyncio.run(collector.scan_source_dialogs(Client(), include_channels=True))
    assert {r['peer_id'] for r in sources} == {17,18}
    assert {r['kind'] for r in sources} == {'channel','group'}
    assert set(entities) == {17,18}
    groups, _ = asyncio.run(collector.scan_group_dialogs(Client()))
    assert [r['peer_id'] for r in groups] == [18]


def test_scan_failure_does_not_change_registry(registry):
    class FailingClient:
        def iter_dialogs(self, limit=None):
            async def rows():
                yield dialog(news())
                raise ConnectionError('partial scan')
            return rows()
    with pytest.raises(ConnectionError):
        rows, _ = asyncio.run(collector.scan_source_dialogs(FailingClient(), include_channels=True))
        registry.record_scan(rows)
    assert registry.initialized() is False
    with registry.engine.connect() as conn:
        assert conn.execute(text('select count(*) from tm_discovered_sources')).scalar_one() == 0


def test_dashboard_labels_channels_and_groups():
    page = Path(__file__).parents[1] / 'telegram_monitor' / 'cloud' / 'dashboard_v8_3.html'
    s = page.read_text(encoding='utf-8')
    assert 'Группы и каналы Telegram' in s
    assert "channel:'Канал'" in s
    assert 'v8.3.1' in s
    assert 'innerHTML' not in s
