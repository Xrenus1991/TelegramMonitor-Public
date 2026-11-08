"""Additional offline regression coverage for interrupted collection/processing.

No Telegram credentials, network services, or paid OpenAI requests.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import select, update

from telegram_monitor.cloud import db
from telegram_monitor.cloud.collector import sync_entity
from telegram_monitor.cloud.api import app


def msg(mid, text="text"):
    return dict(channel_id=111, message_id=mid, channel_name="public",
                timestamp=datetime.now(timezone.utc), text=text)


def store():
    return db.Store("sqlite+pysqlite:///:memory:")


def result(key, priority="high"):
    return dict(key=key, relevant=True, priority=priority, topic="Событие",
                summary_ru="Сообщение источника", source_uncertainty="Требует проверки")


def expire_last_attempt(s, stage):
    col = db.messages.c.state if stage == "classify" else db.messages.c.deep_state
    count = db.messages.c.tries if stage == "classify" else db.messages.c.deep_tries
    with s.engine.begin() as conn:
        conn.execute(update(db.messages).values(**{
            col.key: "processing", count.key: 6,
            "lease_until": db.now() - timedelta(seconds=30),
            "claim_id": "last-attempt"}))


def test_exhausted_classification_does_not_stick_processing():
    s = store()
    s.ingest(111, [msg(1)])
    expire_last_attempt(s, "classify")
    assert not s.claim_classify(10)[0]
    with s.engine.connect() as conn:
        row = conn.execute(select(db.messages.c.state, db.messages.c.claim_id)).one()
    assert row == ("failed", None)


def test_deep_retries_separate_from_classification_retries():
    s = store()
    s.ingest(111, [msg(1)])
    with s.engine.begin() as conn:
        conn.execute(update(db.messages).values(tries=5))
    rows, claim = s.claim_classify(1)
    assert rows
    s.finish_classify(claim, {"111:1": result("111:1")})
    rows, claim = s.claim_deep()
    assert len(rows) == 1
    assert rows[0]["tries"] == 6
    assert rows[0]["deep_tries"] == 0
    s.retry(rows, claim, "deep")
    with s.engine.connect() as conn:
        row = conn.execute(select(db.messages.c.deep_state, db.messages.c.deep_tries, db.messages.c.tries)).one()
    assert row == ("pending", 1, 6)


def test_deep_last_attempt_expires_into_failed():
    s = store()
    s.ingest(111, [msg(1)])
    rows, claim = s.claim_classify(1)
    s.finish_classify(claim, {"111:1": result("111:1")})
    expire_last_attempt(s, "deep")
    assert s.claim_deep()[0] == []
    with s.engine.connect() as conn:
        assert conn.execute(select(db.messages.c.deep_state)).scalar_one() == "failed"
        assert conn.execute(select(db.messages.c.state)).scalar_one() == "done"


class FakeTelegram:
    def __init__(self, messages):
        self.messages = messages
        self.calls = []

    async def iter_messages(self, entity, **kwargs):
        self.calls.append(kwargs)
        latest = [m for m in self.messages if m.id > kwargs.get("min_id", 0)]
        if not kwargs.get("reverse"):
            latest = list(reversed(latest))[:kwargs["limit"]]
        for m in latest:
            yield m


def telegram_obj(mid, text=None):
    return SimpleNamespace(id=mid, message=text, date=datetime.now(timezone.utc), media=None)


def test_service_only_history_advances_checkpoint_without_storing_empty_text():
    s = store()
    c = FakeTelegram([telegram_obj(1), telegram_obj(2)])
    entity = SimpleNamespace(id=111, title="public")
    assert asyncio.run(sync_entity(c, s, entity, 100)) == 0
    assert s.checkpoint(111) == 2
    assert asyncio.run(sync_entity(c, s, entity, 100)) == 0
    assert c.calls[-1]["min_id"] == 2


def test_skipped_tail_and_older_message_do_not_leave_repeated_scan():
    s = store()
    c = FakeTelegram([telegram_obj(1, "text"), telegram_obj(2), telegram_obj(3)])
    entity = SimpleNamespace(id=111, title="public")
    assert asyncio.run(sync_entity(c, s, entity, 100)) == 1
    assert s.checkpoint(111) == 3
    c.messages += [telegram_obj(4, "second text"), telegram_obj(5)]
    assert asyncio.run(sync_entity(c, s, entity, 100)) == 1
    assert s.checkpoint(111) == 5
    assert s.stats()["messages"]["pending"] == 2


def test_checkpoint_never_moves_backwards_after_duplicate_live_message():
    s = store()
    s.ingest(111, [msg(10)])
    s.advance_to(111, 5)
    assert s.checkpoint(111) == 10
    s.ingest(111, [msg(13)], advance_checkpoint=False)
    s.advance_to(111, 11)
    assert s.checkpoint(111) == 11
    s.advance_to(111, 13)
    assert s.checkpoint(111) == 13


def test_sensitive_api_responses_are_not_browser_cacheable():
    from unittest.mock import patch
    with patch.dict("os.environ", {"DASHBOARD_USER": "admin", "DASHBOARD_PASSWORD": "a"*25}):
        client = TestClient(app)
        for route in ("/", "/api/stats", "/api/events"):
            response = client.get(route, auth=("admin", "incorrect"))
            assert response.status_code == 401
            assert "no-store" in response.headers["cache-control"]
        assert "no-store" in client.get("/health").headers["cache-control"]


def test_rc1_database_is_upgraded_without_losing_existing_messages(tmp_path):
    """Existing RC1 DB lacks deep_tries; opening RC2 must migrate it in place."""
    from sqlalchemy import create_engine
    dbfile = tmp_path / "rc1.sqlite3"
    url = f"sqlite+pysqlite:///{dbfile}"
    eng = create_engine(url)
    with eng.begin() as conn:
        conn.exec_driver_sql('''CREATE TABLE messages (
            key VARCHAR(100) PRIMARY KEY, channel_id BIGINT NOT NULL, message_id BIGINT NOT NULL,
            channel_name TEXT NOT NULL, timestamp DATETIME NOT NULL, text TEXT NOT NULL,
            state VARCHAR(20) NOT NULL, tries INTEGER NOT NULL, lease_until DATETIME,
            retry_after DATETIME, claim_id VARCHAR(40), classification TEXT,
            deep_state VARCHAR(20) NOT NULL, deep_analysis TEXT, deep_completed_at DATETIME,
            created_at DATETIME NOT NULL)''')
        conn.exec_driver_sql('''INSERT INTO messages
            (key, channel_id, message_id, channel_name, timestamp, text,
             state, tries, deep_state, created_at)
            VALUES ('111:8', 111, 8, 'public', '2026-10-31', 'old text',
            'pending', 0, 'skip', '2026-10-31')''')
    eng.dispose()
    s = db.Store(url)
    with s.engine.connect() as conn:
        row = conn.execute(select(db.messages.c.text, db.messages.c.deep_tries)).one()
    assert row == ("old text", 0)
    assert s.claim_classify(1)[0][0]["key"] == "111:8"


def test_openai_backend_request_shape_without_api_key_or_network():
    """Responses API wrapper passes strict schema and does not ask to store data."""
    from telegram_monitor.cloud.worker import OpenAIBackend, CLASSIFY_SCHEMA
    calls = []
    class FakeResponses:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(status="completed", output_text='{"items": []}',
                                   usage=SimpleNamespace(input_tokens=50, output_tokens=7))
    fake = OpenAIBackend.__new__(OpenAIBackend)
    fake.client = SimpleNamespace(responses=FakeResponses())
    payload, in_tokens, out_tokens = asyncio.run(fake.call(
        "gpt-6-luna", "instructions", '[]', CLASSIFY_SCHEMA, 300))
    assert payload == {"items": []}
    assert in_tokens == 50 and out_tokens == 7
    assert calls[0]["store"] is False
    assert calls[0]["text"]["format"]["strict"] is True
    assert calls[0]["max_output_tokens"] == 300


def test_model_receives_provenance_and_explicit_truncation_notice():
    import json
    from telegram_monitor.cloud.worker import pack
    row = {**msg(2, "А" * 100), "key": "111:2"}
    data = json.loads(pack([row], max_chars=30))[0]
    assert data["source"] == "public"
    assert "2026" not in data["timestamp"] or data["timestamp"].endswith("+00:00")
    assert data["key"] == "111:2"
    assert data["text"] == "А" * 30
    assert data["truncated"] is True
    assert json.loads(pack([{**row, "text": "коротко"}], max_chars=30))[0]["truncated"] is False


def test_5000_synthetic_messages_ingested_and_deduplicated_without_api_calls():
    s = store()
    c = FakeTelegram([telegram_obj(i, f"Публичное сообщение №{i}") for i in range(1, 5001)])
    entity = SimpleNamespace(id=111, title="public")
    # The configured initial-history cap should be applied only at bootstrap.
    assert asyncio.run(sync_entity(c, s, entity, 5000)) == 5000
    assert s.checkpoint(111) == 5000
    assert s.stats()["messages"] == {"pending": 5000}
    assert asyncio.run(sync_entity(c, s, entity, 5000)) == 0
    assert s.stats()["messages"] == {"pending": 5000}
    # After reconnect, every new message must be ingested (not capped at 100).
    c.messages += [telegram_obj(i, f"Новый текст №{i}") for i in range(5001, 5101)]
    assert asyncio.run(sync_entity(c, s, entity, 100)) == 100
    assert s.checkpoint(111) == 5100
    assert s.stats()["messages"] == {"pending": 5100}


def test_transient_telegram_source_resolution_is_retried_without_restart():
    from telegram_monitor.cloud.collector import resolve_sources
    class FlakyClient:
        def __init__(self):
            self.calls = []
            self.tries = 0
        async def get_entity(self, name):
            self.calls.append(name)
            if name == 'second' and self.tries == 0:
                self.tries += 1
                raise OSError('temporary Telegram failure')
            return SimpleNamespace(id=111 if name == 'first' else 222, title=name)
    client, known = FlakyClient(), {}
    available = asyncio.run(resolve_sources(client, ('first','second'), known))
    assert list(available) == [111]
    available = asyncio.run(resolve_sources(client, ('first','second'), known))
    assert set(available) == {111,222}
    assert client.calls.count('first') == 1
    assert client.calls.count('second') == 2
