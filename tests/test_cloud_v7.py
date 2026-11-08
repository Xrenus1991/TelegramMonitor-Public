"""Offline regression tests: NO Telegram credentials or paid AI calls."""
import asyncio
import json
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import select, update

from telegram_monitor.cloud import db
from telegram_monitor.cloud.collector import sync_entity, normalise
from telegram_monitor.cloud.settings import Settings
from telegram_monitor.cloud.worker import (
    redact, request_price, validate_classification, classify_once,
    CLASSIFY_SCHEMA, deep_once, paid_call,
)


def settings():
    return Settings(database_url="sqlite+pysqlite:///:memory:", telegram_api_id=0,
        telegram_api_hash="", telegram_session_path="none", channels=(),
        provider="openai", api_key="not-a-real-secret", model="gpt-6-luna",
        deep_model="gpt-6.1-sol", monthly_budget_usd=27, max_output_tokens=1200,
        prompt_chars_per_message=500, batch_size=20, initial_history=150,
        sync_seconds=90, deep_max_per_day=50, dashboard_password="a"*32,
        dashboard_user="admin")


def make_row(mid, channel=123, text="Официальное сообщение о визовых правилах"):
    return dict(channel_id=channel, message_id=mid, channel_name="example",
                timestamp=datetime(2026, 10, 31, tzinfo=timezone.utc), text=text)


def make_store():
    return db.Store("sqlite+pysqlite:///:memory:")


def obj(mid, text="текст"):
    return SimpleNamespace(id=mid, message=text, date=datetime.now(timezone.utc), media=None)


class FakeClient:
    def __init__(self, count=150):
        self.rows = [obj(i) for i in range(1, count + 1)]
        self.calls = []

    async def iter_messages(self, entity, **kw):
        self.calls.append(kw)
        if kw.get("reverse"):
            ids = [r for r in self.rows if r.id > kw["min_id"]]
        else:
            ids = list(reversed(self.rows))[:kw["limit"]]
        for row in ids:
            yield row


def test_checkpoint_and_deduplication():
    s = make_store()
    assert s.ingest(123, [make_row(2), make_row(1)]) == 2
    assert s.checkpoint(123) == 2
    assert s.ingest(123, [make_row(2)]) == 0
    assert s.checkpoint(123) == 2


def test_live_delivery_does_not_advance_backfill_checkpoint():
    s = make_store()
    s.ingest(123, [make_row(100)])
    s.ingest(123, [make_row(103)], advance_checkpoint=False)
    assert s.checkpoint(123) == 100
    s.ingest(123, [make_row(101), make_row(102), make_row(103)])
    assert s.checkpoint(123) == 103
    assert s.stats()["messages"]["pending"] == 4


def test_invalid_messages_leave_checkpoint_unchanged():
    s = make_store()
    with pytest.raises(ValueError):
        s.ingest(123, [make_row(1), make_row(2, text=" ")])
    assert s.checkpoint(123) == 0
    assert s.stats()["messages"] == {}


def test_initial_bootstrap_commits_oldest_first():
    s, c = make_store(), FakeClient(150)
    entity = SimpleNamespace(id=123, title="public")
    assert asyncio.run(sync_entity(c, s, entity, 150)) == 150
    assert s.checkpoint(123) == 150
    assert asyncio.run(sync_entity(c, s, entity, 150)) == 0
    assert c.calls[-1]["min_id"] == 150


def test_bootstrap_crash_is_recoverable():
    class InterruptStore:
        def __init__(self, original):
            self.db = original
            self.calls = 0
        def checkpoint(self, id):
            return self.db.checkpoint(id)
        def ingest(self, *a):
            self.calls += 1
            if self.calls == 2:
                raise OSError("simulated power loss")
            return self.db.ingest(*a)
    original, client = make_store(), FakeClient(150)
    entity = SimpleNamespace(id=123, title="public")
    with pytest.raises(OSError):
        asyncio.run(sync_entity(client, InterruptStore(original), entity, 150))
    assert original.checkpoint(123) == 100
    assert asyncio.run(sync_entity(client, original, entity, 150)) == 50
    assert original.checkpoint(123) == 150


def test_claim_retry_and_lease():
    s = make_store()
    s.ingest(123, [make_row(1)])
    rows, token = s.claim_classify(5)
    assert len(rows) == 1
    assert s.claim_classify(5)[0] == []
    s.retry(rows, token, "classify")
    assert s.claim_classify(5)[0] == []
    with s.engine.begin() as conn:
        conn.execute(update(db.messages).values(retry_after=db.now() - timedelta(seconds=10)))
    new, new_token = s.claim_classify(5)
    assert len(new) == 1 and token != new_token


def test_expired_lease_recovery():
    s = make_store()
    s.ingest(123, [make_row(1)])
    s.claim_classify(1)
    with s.engine.begin() as conn:
        conn.execute(update(db.messages).values(lease_until=db.now() - timedelta(minutes=1)))
    assert len(s.claim_classify(1)[0]) == 1


def item(key, priority="normal"):
    return {"key": key, "relevant": True, "priority": priority, "topic": "Законодательство",
            "summary_ru": "Обновление правил", "source_uncertainty": "Один источник"}


def test_finish_classify_and_deep_stages():
    s = make_store()
    s.ingest(123, [make_row(1)])
    rows, claim = s.claim_classify(1)
    s.finish_classify(claim, {"123:1": item("123:1", "high")})
    assert s.stats()["messages"]["done"] == 1
    assert s.stats()["deep"]["pending"] == 1
    deep_rows, deep_claim = s.claim_deep()
    s.finish_deep("123:1", deep_claim, {"summary_ru": "Сводка", "reported_facts": [], "unverified": [], "limitations": []})
    assert s.stats()["deep"]["done"] == 1
    assert s.daily_deep_count() == 1
    with pytest.raises(RuntimeError):
        s.finish_deep("123:1", deep_claim, {"summary_ru": "again"})


def test_budget_reservation_and_settlement():
    s = make_store()
    a = s.reserve("gpt-6-luna", 500000, 1)
    assert a
    assert s.reserve("gpt-6-luna", 600000, 1) is None
    s.settle(a, 150000, 100, 70)
    assert s.reserve("gpt-6-luna", 800000, 1)
    with pytest.raises(ValueError):
        s.settle(a, 5)


def test_budget_unknown_charge_remains_reserved():
    s = make_store()
    a = s.reserve("gpt-6-luna", 800000, 1)
    s.settle(a, None)
    assert s.reserve("gpt-6-luna", 300000, 1) is None


def test_budget_exhaustion_does_not_mark_message_failed():
    s = make_store()
    s.ingest(123, [make_row(1)])
    cfg = replace(settings(), monthly_budget_usd=1)
    assert s.reserve("gpt-6-luna", 1_000_000, 1)
    class NoCalls:
        async def call(self, *args):
            raise AssertionError("Should not send an API call")
    assert asyncio.run(classify_once(s, NoCalls(), cfg)) is False
    assert s.stats()["messages"]["pending"] == 1
    with s.engine.connect() as conn:
        assert conn.execute(select(db.messages.c.tries)).scalar_one() == 0


def test_price_and_text_masking():
    assert request_price("gpt-6-luna", 1_000_000, 0) == 100000
    assert request_price("gpt-6.1-sol", 0, 1_000_000) == 10000000
    with pytest.raises(ValueError):
        request_price("unknown", 100, 100)
    text = redact("contact me at john@example.org @username123 +374 99 123 456")
    assert "john@example.org" not in text and "@username123" not in text
    assert "374 99" not in text


def test_strict_ids_enforced():
    assert validate_classification({"items": [item("1:1")]}, ["1:1"])["1:1"]["relevant"] is True
    for payload in ({"items": []}, {"items": [item("1:2")]}, {"items": [item("1:1"), item("1:1")]},
                    {"items": [dict(item("1:1"), extra=1)]}):
        with pytest.raises(ValueError):
            validate_classification(payload, ["1:1"])


def test_real_worker_logic_with_mock_api_no_network():
    s = make_store()
    s.ingest(123, [make_row(1), make_row(2)])
    class FakeBackend:
        async def call(self, model, instructions, prompt, schema, max_output):
            keys = [r["key"] for r in json.loads(prompt)]
            return {"items": [item(k) for k in keys]}, 900, 200
    assert asyncio.run(classify_once(s, FakeBackend(), settings()))
    assert s.stats()["messages"]["done"] == 2
    assert s.stats()["estimated_or_reserved_usd"] > 0
    assert len(s.events()) == 2


def test_deep_mock_call_and_limits():
    s = make_store()
    s.ingest(123, [make_row(1)])
    rows, claim = s.claim_classify(1)
    s.finish_classify(claim, {"123:1": item("123:1", "high")})
    class FakeDeep:
        async def call(self, *args):
            return {"summary_ru": "Сводка", "reported_facts": [], "unverified": [], "limitations": []}, 800, 200
    assert asyncio.run(deep_once(s, FakeDeep(), settings())) is True
    assert asyncio.run(deep_once(s, FakeDeep(), replace(settings(), deep_max_per_day=1))) is False


def test_incomplete_response_does_not_ack_messages():
    s = make_store()
    s.ingest(123, [make_row(1)])
    class BadBackend:
        async def call(self, *args):
            return {"items": []}, 200, 100
    assert not asyncio.run(classify_once(s, BadBackend(), settings()))
    assert s.stats()["messages"]["pending"] == 1


def test_dash_api_requires_auth():
    from fastapi.testclient import TestClient
    from telegram_monitor.cloud.api import app
    c = TestClient(app)
    assert c.get("/health").status_code == 200
    assert c.get("/api/events").status_code == 401
    with patch.dict("os.environ", {"DASHBOARD_USER": "admin", "DASHBOARD_PASSWORD": "1234567890123456"}):
        assert c.get("/", auth=("admin", "wrong")).status_code == 401
        assert c.get("/", auth=("admin", "1234567890123456")).status_code == 200
