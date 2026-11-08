"""One-shot bounded Telegram history ingestion: no checkpoint reset or repeat AI spend."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as Obj

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from telegram_monitor.cloud import api
from telegram_monitor.cloud.collector import sync_recent_messages
from telegram_monitor.cloud.db import Store, messages
from telegram_monitor.cloud.source_registry import SourceRegistry, recent_requests


def source(pid):
    return {"peer_id": pid, "title": f"Group {pid}", "username": f"group{pid}",
            "kind": "megagroup", "visibility": "public"}


@pytest.fixture
def registry(tmp_path):
    url = "sqlite:///" + str(tmp_path / "recent.sqlite")
    store = Store(url)
    registry = SourceRegistry(url, initialize=True)
    registry.record_scan([source(111), source(222)], auto_public=False)
    registry.change_status(111, "approve")
    yield registry, store
    registry.dispose()
    store.engine.dispose()


def test_request_lifecycle_and_source_selection(registry):
    registry, _ = registry
    assert registry.list_sources()[0].get("recent_request") is None
    with pytest.raises(ValueError):
        registry.request_recent(111, 0)
    with pytest.raises(ValueError):
        registry.request_recent(111, 1001)
    with pytest.raises(ValueError):
        registry.request_recent(111, True)
    with pytest.raises(KeyError):
        registry.request_recent(999, 10)
    with pytest.raises(ValueError):
        registry.request_recent(222, 10)
    job = registry.request_recent(111, 1000)
    assert job["status"] == "pending" and job["count"] == 1000
    with pytest.raises(ValueError):
        registry.request_recent(111, 10)
    assert registry.claim_recent({222}) is None
    assert registry.claim_recent({111})["id"] == job["id"]
    registry.change_status(111, "disable")
    assert registry.claim_recent({}) is None
    registry.change_status(111, "approve")
    assert registry.finish_recent(job["id"], scanned=1000, saved=6) is True
    assert registry.finish_recent(job["id"], scanned=1000, saved=6) is False
    row = next(x for x in registry.list_sources(include_recent=True) if x["peer_id"] == 111)
    assert row["recent_request"]["status"] == "done"
    assert row["recent_request"]["saved_count"] == 6
    # A second manual request is allowed only after the first completed.
    assert registry.request_recent(111, 5)["count"] == 5
    assert registry.claim_recent({111})["count"] == 5


def test_recent_claim_retry_after_stale_worker(registry):
    registry, _ = registry
    job = registry.request_recent(111, 7)
    assert registry.claim_recent({111})["id"] == job["id"]
    assert registry.claim_recent({111}) is None
    with registry.engine.begin() as c:
        c.execute(update(recent_requests).where(recent_requests.c.id == job["id"])
                  .values(started_at=datetime.now(timezone.utc)-timedelta(minutes=21)))
    assert registry.claim_recent({111})["id"] == job["id"]
    assert registry.finish_recent(job["id"], error="SyntheticNetworkError") is True
    assert next(x for x in registry.list_sources(include_recent=True) if x["peer_id"] == 111)["recent_request"]["status"] == "failed"


def test_ingest_latest_n_without_resetting_checkpoint_or_classification(registry):
    _, db = registry
    now = datetime.now(timezone.utc)
    entity = Obj(id=111, title="Group 111")
    existing = dict(channel_id=111, message_id=8, channel_name="Group 111",
                    text="Already classified", timestamp=now)
    assert db.ingest(111, [existing]) == 1
    with db.engine.begin() as c:
        c.execute(update(messages).where(messages.c.message_id == 8)
                  .values(state="done", classification='{"summary_ru":"Old analysis"}'))
    class Telegram:
        async def iter_messages(self, requested_entity, *, limit):
            assert requested_entity is entity and limit == 3
            for msg_id in (9, 8, 7):
                yield Obj(id=msg_id, message=f"Message {msg_id}", media=None,
                          from_id=Obj(user_id=1234), date=now)
    scanned, added = asyncio.run(sync_recent_messages(Telegram(), db, entity, 3))
    assert (scanned, added) == (3, 2)
    assert db.checkpoint(111) == 8
    with db.engine.connect() as c:
        rows = {r["message_id"]: r for r in c.execute(select(messages)).mappings()}
    assert rows[8]["state"] == "done"
    assert rows[8]["classification"] == '{"summary_ru":"Old analysis"}'
    assert rows[7]["state"] == "pending"
    assert rows[9]["state"] == "pending"
    assert rows[8]["sender_user_id"] == 1234
    assert asyncio.run(sync_recent_messages(Telegram(), db, entity, 3)) == (3, 0)
    assert db.checkpoint(111) == 8


def test_1000_message_fetch_bounded_and_deduplicated(registry):
    _, db = registry
    now = datetime.now(timezone.utc)
    entity = Obj(id=111, title="Group 111")
    class Telegram:
        def __init__(self):
            self.requested = []
        async def iter_messages(self, requested_entity, *, limit):
            assert requested_entity is entity
            assert limit == 1000
            self.requested.append(limit)
            for msg_id in range(1000, 0, -1):
                yield Obj(id=msg_id, message=f"Recent {msg_id}", media=None,
                          from_id=None, date=now)
    tg = Telegram()
    assert asyncio.run(sync_recent_messages(tg, db, entity, 1000)) == (1000, 1000)
    assert db.checkpoint(111) == 0
    assert asyncio.run(sync_recent_messages(tg, db, entity, 1000)) == (1000, 0)
    assert tg.requested == [1000, 1000]
    with db.engine.connect() as conn:
        rows = conn.execute(select(messages)).mappings().all()
    assert len(rows) == 1000
    assert all(r["state"] == "pending" for r in rows)
    with pytest.raises(ValueError):
        asyncio.run(sync_recent_messages(tg, db, entity, 1001))


def test_http_manual_request_security_and_status(registry, monkeypatch):
    registry, db = registry
    monkeypatch.setenv("DASHBOARD_USER", "operator")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "synthetic-password-long-enough")
    monkeypatch.setenv("CLOUD_DISCOVERY_ENABLED", "true")
    monkeypatch.setenv("CLOUD_DISCOVERY_UI_WRITE", "true")
    monkeypatch.setenv("CLOUD_SOURCE_RECENT_ENABLED", "true")
    monkeypatch.setenv("CLOUD_SOURCE_CONTROL_DATABASE_URL", "sqlite://")
    monkeypatch.setattr(api, "source_decision_registry", lambda: registry)
    monkeypatch.setattr(api, "sources_registry", lambda: registry)
    monkeypatch.setattr(api, "store", lambda: db)
    http = TestClient(api.app)
    auth = ("operator", "synthetic-password-long-enough")
    good_headers = {"origin": "http://testserver", "x-tm-action": "1"}
    assert http.post("/api/sources/111/recent", json={"count": 3}, headers=good_headers).status_code == 401
    assert http.post("/api/sources/111/recent", auth=auth, json={"count": 3},
                     headers={**good_headers, "origin": "http://evil.test"}).status_code == 403
    for bad in ({"count": 0}, {"count": 1001}, {"count": True}, {"count": "30"}):
        assert http.post("/api/sources/111/recent", auth=auth, json=bad,
                         headers=good_headers).status_code == 422
    assert http.post("/api/sources/222/recent", auth=auth, json={"count": 3},
                     headers=good_headers).status_code == 409
    ok = http.post("/api/sources/111/recent", auth=auth, json={"count": 1000},
                   headers=good_headers)
    assert ok.status_code == 202 and ok.json()["count"] == 1000
    assert http.post("/api/sources/111/recent", auth=auth, json={"count": 3},
                     headers=good_headers).status_code == 409
    data = http.get("/api/sources", auth=auth).json()
    assert data["recent_enabled"] is True
    record = next(x for x in data["items"] if x["peer_id"] == 111)
    assert record["recent_request"]["status"] == "pending"
    monkeypatch.setenv("CLOUD_SOURCE_RECENT_ENABLED", "false")
    assert http.post("/api/sources/111/recent", auth=auth, json={"count": 3},
                     headers=good_headers).status_code == 403
    assert http.get("/api/sources", auth=auth).json()["recent_enabled"] is False


def test_frontend_has_source_specific_count_and_no_automatic_reprocess():
    html = api.PAGE
    assert "data.recent_enabled && item.status==='approved'" in html
    assert "count.max='1000'" in html
    assert "/recent" in html
    assert "Уже обработанные повторно не анализируются" in html
