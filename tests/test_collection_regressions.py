"""Offline regressions for Telegram collection. No Telegram API network calls."""
import asyncio
import logging

import pytest
from collections import deque
from datetime import datetime, timezone
from types import SimpleNamespace

from telegram_monitor.client import TelegramMonitor, TelegramMessage


@pytest.fixture(autouse=True)
def never_create_real_telethon_session(monkeypatch):
    class StubClient:
        def __init__(self, *args, **kwargs):
            pass
    monkeypatch.setattr('telegram_monitor.client.TelegramClient', StubClient)


def create_monitor():
    m = TelegramMonitor(123, "fake-api-hash", ["some_channel"], download_media=False)
    m.resolved_channels = {42: "Public channel"}
    entity = SimpleNamespace(id=42, title="Public channel", access_hash=111)
    m._resolved_entities = {42: entity}
    m.messages = {"Public channel": deque(maxlen=200)}
    return m, entity


def fake_msg(i):
    return SimpleNamespace(
        id=i, date=datetime(2026, 10, 31, tzinfo=timezone.utc),
        text=f"Информация #{i}", media=None,
    )


def test_history_fetch_uses_resolved_entity_not_bare_id():
    monitor, entity = create_monitor()

    class FakeClient:
        def iter_messages(self, actual_entity, limit):
            assert actual_entity is entity  # Telethon entity with access hash
            assert limit == 12
            async def source():
                for i in range(12):
                    yield fake_msg(i)
            return source()
    monitor.client = FakeClient()
    history = asyncio.run(monitor.fetch_history(per_channel=12))
    assert len(history) == 12
    assert monitor.status['history_counts']['Public channel'] == 12
    assert monitor.status['total_messages'] == 12


def test_duplicate_history_fetch_not_duplicated():
    monitor, entity = create_monitor()

    class FakeClient:
        def iter_messages(self, actual_entity, limit):
            async def source():
                for i in range(3):
                    yield fake_msg(i)
            return source()
    monitor.client = FakeClient()
    assert len(asyncio.run(monitor.fetch_history(per_channel=10))) == 3
    assert len(asyncio.run(monitor.fetch_history(per_channel=10))) == 0
    assert monitor.status['total_messages'] == 3


def test_same_id_different_channel_not_duplicate():
    monitor, _ = create_monitor()
    msg = TelegramMessage('A', 42, 'x', datetime.now(timezone.utc), 7)
    other = TelegramMessage('B', 43, 'x', datetime.now(timezone.utc), 7)
    assert monitor._mark_seen(msg)
    assert not monitor._mark_seen(msg)
    assert monitor._mark_seen(other)


def test_dedup_bounded():
    monitor, _ = create_monitor()
    for i in range(10005):
        assert monitor._mark_seen(TelegramMessage('A', 42, 'x', datetime.now(timezone.utc), i))
    assert len(monitor._seen_ids) == len(monitor._seen_order) == 10000
    assert monitor._mark_seen(TelegramMessage('A', 42, 'x', datetime.now(timezone.utc), 0))


def test_flood_logger_handler_is_removed():
    monitor, _ = create_monitor()
    assert monitor._flood_handler in logging.getLogger('telethon').handlers

    class FakeClient:
        async def disconnect(self):
            return None
    monitor.client = FakeClient()
    asyncio.run(monitor.stop())
    assert monitor._flood_handler is None


def test_history_capped_at_200():
    monitor, _ = create_monitor()
    class FakeClient:
        def iter_messages(self, entity, limit):
            assert limit == 200
            async def source():
                return
                yield
            return source()
    monitor.client = FakeClient()
    assert asyncio.run(monitor.fetch_history(per_channel=999)) == []


def test_live_message_uses_same_positive_id_as_history(monkeypatch):
    """Telethon event.chat_id is often -100... while entity.id is positive."""
    monitor, entity = create_monitor()
    monkeypatch.setattr('telegram_monitor.client.Channel', SimpleNamespace)
    event_handler = {}
    class FakeClient:
        async def start(self, phone): return None
        async def get_me(self): return SimpleNamespace(first_name='Test', phone='hidden')
        async def get_input_entity(self, ch): return entity
        async def get_entity(self, e): return entity
        def on(self, arg):
            def register(fn):
                event_handler['callback'] = fn
                return fn
            return register
    monitor.client = FakeClient()
    async def scenario():
        await monitor.start()
        class Event:
            chat_id = -1000000000042
            raw_text = 'Новое сообщение'
            message = SimpleNamespace(id=5, date=datetime.now(timezone.utc), media=None)
            async def get_chat(self): return entity
        await event_handler['callback'](Event())
        new = await monitor.get_new_messages(timeout=0.1)
        assert len(new) == 1 and new[0].channel_id == 42
        assert not monitor._mark_seen(TelegramMessage('Public channel', 42, 'duplicate',
                             datetime.now(timezone.utc), 5))
    asyncio.run(scenario())


def test_bounded_live_queue_drain():
    monitor, _ = create_monitor()
    async def scenario():
        for n in range(5):
            await monitor._message_queue.put(
                TelegramMessage('Test', 42, f'Text{n}', datetime.now(timezone.utc), n))
        first = await monitor.get_new_messages(timeout=0.01, max_items=2)
        second = await monitor.get_new_messages(timeout=0.01, max_items=2)
        third = await monitor.get_new_messages(timeout=0.01, max_items=2)
        assert [m.message_id for m in first] == [0, 1]
        assert [m.message_id for m in second] == [2, 3]
        assert [m.message_id for m in third] == [4]
    asyncio.run(scenario())


def test_media_returns_real_path_and_rejects_missing_file(tmp_path, monkeypatch):
    import telegram_monitor.client as client_module
    monkeypatch.setattr(client_module, 'MEDIA_DIR', tmp_path)
    monitor, _ = create_monitor()
    monitor.download_media = True
    class Msg:
        id = 9
        async def download_media(self, file):
            # Telethon is allowed to choose the file's extension.
            actual = file + '.other'
            from pathlib import Path
            Path(actual).write_bytes(b'not a real image')
            return actual
    event = SimpleNamespace(chat_id=-1000000042, message=Msg())
    result = asyncio.run(monitor._download_media(event, 'photo', 'Same name'))
    assert result.endswith('.other')
    assert (tmp_path / result.split('/')[-1]).is_file()
    assert '_1000000042_' in result
    assert monitor._media_count == 1

    class Missing:
        id = 10
        async def download_media(self, file): return None
    assert asyncio.run(monitor._download_media(
        SimpleNamespace(chat_id=-1000000042, message=Missing()), 'photo', 'Same name')) is None
    assert monitor._media_count == 1


def test_timestamps_aware_converted_correctly():
    from datetime import timedelta
    monitor, _ = create_monitor()
    local = datetime(2026, 10, 31, 14, 0, tzinfo=timezone(timedelta(hours=4)))
    converted = monitor._utc_timestamp(local)
    assert converted.hour == 10
    assert converted.tzinfo == timezone.utc
    assert monitor._utc_timestamp(datetime(2026, 10, 31, 10, 0)).tzinfo == timezone.utc


def test_duplicate_live_event_skips_media_download(monkeypatch):
    monitor, entity = create_monitor()
    monkeypatch.setattr('telegram_monitor.client.Channel', SimpleNamespace)
    callback = {}
    class FakeClient:
        async def start(self, phone): pass
        async def get_me(self): return SimpleNamespace(first_name='x', phone='secret')
        async def get_input_entity(self, ch): return entity
        async def get_entity(self, e): return entity
        def on(self, arg):
            def register(fn):
                callback['event'] = fn
                return fn
            return register
    monitor.client = FakeClient()
    calls = []
    async def fake_download(*args):
        calls.append(1)
        return 'fake'
    monitor._download_media = fake_download
    monitor.download_media = True
    monitor._classify_media = lambda media: ('photo', 0)
    async def scenario():
        await monitor.start()
        event = SimpleNamespace(chat_id=-1000000000042, raw_text='Hello',
                 message=SimpleNamespace(id=11, media=object(), date=datetime.now(timezone.utc)))
        async def get_chat(): return entity
        event.get_chat = get_chat
        await callback['event'](event)
        await callback['event'](event)
        assert len(calls) == 1
        msgs = await monitor.get_new_messages(timeout=0.01)
        assert len(msgs) == 1
    asyncio.run(scenario())
