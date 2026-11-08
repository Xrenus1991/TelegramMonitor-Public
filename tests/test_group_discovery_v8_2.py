"""No Telegram account, no OpenAI and no network are used by these tests."""
import asyncio
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from telegram_monitor.cloud.source_registry import SourceRegistry, group_info
from telegram_monitor.cloud import collector, api


class Channel:
    def __init__(self, ident, username=None, title='Тестовая группа', megagroup=True):
        self.id=ident;self.username=username;self.title=title;self.megagroup=megagroup


class Chat:
    def __init__(self, ident, title='Закрытая группа'):
        self.id=ident;self.title=title;self.deactivated=False;self.migrated_to=None


def dialog(entity, is_group=True):
    return SimpleNamespace(entity=entity,is_group=is_group)


@pytest.fixture
def registry(tmp_path):
    x=SourceRegistry('sqlite:///'+str(tmp_path/'registry.db'),initialize=True)
    yield x
    x.dispose()


def test_only_telegram_groups_are_candidates():
    assert group_info(dialog(Channel(123, 'public_group')))['visibility']=='public'
    assert group_info(dialog(Channel(124, None)))['visibility']=='private'
    assert group_info(dialog(Chat(125)))['kind']=='group'
    assert group_info(dialog(Channel(99, 'channel', megagroup=False), False)) is None
    assert group_info(dialog(SimpleNamespace(id=45, username='someone'), False)) is None
    assert group_info(dialog(Channel(99, 'broadcast', megagroup=False))) is None
    assert group_info(dialog(SimpleNamespace(id=1, bot=True),False)) is None


def test_baseline_no_auto_collection(registry):
    old=group_info(dialog(Channel(1,'old_public')))
    result=registry.record_scan([old])
    assert result['first_scan'] and registry.initialized()
    assert registry.approved_ids()==set()
    assert registry.list_sources()[0]['status']=='baseline'
    registry.record_scan([old])
    assert registry.approved_ids()==set()


def test_new_public_auto_private_requires_approval(registry):
    registry.record_scan([])
    public=group_info(dialog(Channel(101,'new_public')))
    private=group_info(dialog(Channel(102,None)))
    legacy=group_info(dialog(Chat(103)))
    registry.record_scan([public, private, legacy])
    assert registry.approved_ids()=={101}
    assert {r['peer_id']:r['status'] for r in registry.list_sources()}=={
        101:'approved',102:'pending',103:'pending'}
    assert registry.change_status(102, 'approve')=='approved'
    assert registry.approved_ids()=={101,102}
    assert registry.change_status(102, 'disable')=='disabled'
    assert registry.approved_ids()=={101}
    registry.record_scan([public,private,legacy])
    assert registry.approved_ids()=={101}


def test_new_public_can_be_manual_only(registry):
    registry.record_scan([])
    registry.record_scan([group_info(dialog(Channel(11,'abc')))],auto_public=False)
    assert registry.approved_ids()==set()
    assert registry.list_sources()[0]['status']=='pending'


def test_leave_rejoin_requires_manual_reapproval(registry):
    registry.record_scan([])
    row=group_info(dialog(Channel(5,'abc')))
    registry.record_scan([row]);assert registry.approved_ids()=={5}
    registry.record_scan([])
    assert registry.list_sources()[0]['status']=='left'
    registry.record_scan([row]);assert registry.list_sources()[0]['status']=='pending'
    assert registry.approved_ids()==set()


def test_public_becomes_private_requires_approval(registry):
    registry.record_scan([])
    registry.record_scan([group_info(dialog(Channel(7,'public')))])
    assert registry.approved_ids()=={7}
    registry.record_scan([group_info(dialog(Channel(7,None)))])
    assert registry.list_sources()[0]['status']=='pending'


def test_failed_scan_does_not_change_existing_registry(registry):
    registry.record_scan([])
    row=group_info(dialog(Channel(9,'open')))
    registry.record_scan([row])
    with pytest.raises(KeyError):
        registry.record_scan([{'peer_id':10}])
    assert registry.approved_ids()=={9}


def test_bad_actions_no_arbitrary_db_mutation(registry):
    registry.record_scan([])
    with pytest.raises(KeyError):registry.change_status(999,'approve')
    with pytest.raises(ValueError):registry.change_status(1,'delete')
    with pytest.raises(ValueError):registry.change_status(-1,'approve')
    with registry.engine.connect() as conn:
        assert conn.execute(text('select count(*) from tm_discovered_sources')).scalar_one()==0


def test_active_map_only_approved_and_static():
    static={1:'configured'}
    seen={1:'new-discovery',2:'approved',3:'not-approved'}
    assert collector.active_entity_map(static,seen,{2})=={1:'configured',2:'approved'}


def test_scan_reads_metadata_only():
    class FakeClient:
        def iter_dialogs(self, limit=None):
            async def gen():
                yield dialog(Channel(1,'public'))
                yield dialog(Chat(2))
                yield dialog(Channel(3, 'news', megagroup=False), is_group=False)
            return gen()
        def iter_messages(self,*args,**kwargs):
            raise AssertionError('Must not read any messages during discovery')
    rows, entities=asyncio.run(collector.scan_group_dialogs(FakeClient()))
    assert [x['peer_id'] for x in rows]==[1,2]
    assert set(entities)=={1,2}


def test_scan_failure_does_not_record_partial_results(registry):
    class FakeClient:
        def iter_dialogs(self, limit=None):
            async def gen():
                yield dialog(Channel(1,'public'))
                raise RuntimeError('Telegram temporarily unavailable')
            return gen()
    with pytest.raises(RuntimeError):
        rows, entities=asyncio.run(collector.scan_group_dialogs(FakeClient()))
        registry.record_scan(rows)
    assert registry.initialized() is False


@pytest.fixture
def web(tmp_path,monkeypatch):
    url='sqlite:///'+str(tmp_path/'dashboard_registry.sqlite')
    writable=SourceRegistry(url, initialize=True)
    writable.record_scan([])
    writable.record_scan([group_info(dialog(Channel(41,'open'))),group_info(dialog(Chat(42)))])
    monkeypatch.setenv('DATABASE_URL',url)
    monkeypatch.setenv('DASHBOARD_USER','admin')
    monkeypatch.setenv('DASHBOARD_PASSWORD','strong_secure_password_333333')
    monkeypatch.setenv('CLOUD_DISCOVERY_ENABLED','true')
    monkeypatch.setenv('CLOUD_DISCOVERY_UI_WRITE','true')
    monkeypatch.setenv('CLOUD_SOURCE_CONTROL_DATABASE_URL',url)
    api.source_decision_registry.cache_clear()
    api.sources_registry.cache_clear()
    api.store.cache_clear()
    client=TestClient(api.app)
    client.auth=('admin','strong_secure_password_333333')
    yield writable,client
    api.source_decision_registry.cache_clear()
    api.sources_registry.cache_clear()
    api.store.cache_clear()
    writable.dispose()


def test_source_api_basic_auth(web):
    writable, client=web
    assert client.get('/api/sources',auth=('admin','bad')).status_code==401
    result=client.get('/api/sources')
    assert result.status_code==200
    assert result.json()['initialized']
    assert len(result.json()['items'])==2


def test_csrf_and_bad_payload_rejected(web):
    _,client=web
    assert client.post('/api/sources/42',json={'action':'approve'}).status_code==403
    assert client.post('/api/sources/42',json={'action':'delete'},headers={
        'Origin':'http://testserver','X-TM-Action':'1'}).status_code==422
    assert client.post('/api/sources/42',json={'action':'approve'},headers={
        'Origin':'http://attacker.test','X-TM-Action':'1'}).status_code==403


def test_approve_disable_via_web_and_ui_optout(web,monkeypatch):
    reg,client=web
    headers={'Origin':'http://testserver','X-TM-Action':'1'}
    assert client.post('/api/sources/42',json={'action':'approve'},headers=headers).status_code==200
    assert reg.approved_ids()=={41,42}
    assert client.post('/api/sources/42',json={'action':'disable'},headers=headers).status_code==200
    assert reg.approved_ids()=={41}
    monkeypatch.setenv('CLOUD_DISCOVERY_UI_WRITE','false')
    assert client.post('/api/sources/42',json={'action':'approve'},headers=headers).status_code==403
    assert client.get('/api/sources').json()['editable'] is False


def test_disabled_discovery_empty_api(web,monkeypatch):
    _,client=web
    monkeypatch.setenv('CLOUD_DISCOVERY_ENABLED','false')
    response=client.get('/api/sources').json()
    assert not response['enabled'] and response['items']==[]


def test_html_no_inline_unescaped_user_content(web):
    _,client=web
    response=client.get('/')
    assert response.status_code==200
    assert 'v8.3' in response.text
    assert 'innerHTML' not in response.text
    assert 'X-Frame-Options' in response.headers

