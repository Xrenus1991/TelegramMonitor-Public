"""Author attribution is optional metadata, never a statement of culpability."""
import base64
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, update

from telegram_monitor.cloud import api
from telegram_monitor.cloud.collector import normalise, sender_user_id
from telegram_monitor.cloud.db import Store, messages, now


def message(mid=1, from_id=None, text="На улице произошло событие"):
    return SimpleNamespace(id=mid, from_id=from_id, message=text, media=None,
                           date=datetime.now(timezone.utc))


def test_collect_only_explicit_user_sender():
    group = SimpleNamespace(id=123, title="Публичная группа")
    author = SimpleNamespace(user_id=90807060)
    record = normalise(message(from_id=author), group)
    assert record["sender_user_id"] == 90807060
    assert sender_user_id(message(from_id=SimpleNamespace(channel_id=654321))) is None
    assert normalise(message(from_id=SimpleNamespace(channel_id=654321)), group)["sender_user_id"] is None
    assert normalise(message(), group)["sender_user_id"] is None
    # Avoid misleading authorship in unsigned/anonymous/channel-as-sender posts.
    assert sender_user_id(SimpleNamespace(from_id=None, sender_id=99999)) is None
    assert sender_user_id(message(from_id=SimpleNamespace(user_id=True))) is None
    assert sender_user_id(message(from_id=SimpleNamespace(user_id=-10))) is None


def row(mid, uid=None):
    return dict(channel_id=123, message_id=mid, channel_name="Публичная группа",
                text="В городе перекрыта улица и проводится проверка.",
                timestamp=now() - timedelta(minutes=5), sender_user_id=uid)


def test_database_save_and_metadata_backfill_does_not_reset_analysis(tmp_path):
    db = Store("sqlite:///" + str(tmp_path / "sender.sqlite"))
    assert db.ingest(123, [row(1)]) == 1
    with db.engine.begin() as conn:
        conn.execute(update(messages).where(messages.c.key == "123:1").values(
            state="done", classification=json.dumps({"summary_ru": "Упоминание происшествия"})))
    assert db.ingest(123, [row(1, 90807060)]) == 0
    assert db.ingest(123, [row(1, None)]) == 0
    with db.engine.connect() as conn:
        got = conn.execute(select(messages).where(messages.c.key == "123:1")).mappings().one()
    assert got["sender_user_id"] == 90807060
    assert got["state"] == "done"
    assert json.loads(got["classification"])["summary_ru"] == "Упоминание происшествия"
    assert db.checkpoint(123) == 1
    assert db.ingest(123, [row(2, None)]) == 1
    assert db.ingest(123, [row(2, 456)]) == 0
    assert db.events_page()["items"][0]["sender_user_id"] in {456, 90807060}
    db.engine.dispose()


@pytest.mark.parametrize("invalid", [True, -1, 0, 2**63, "123", 1.5])
def test_invalid_sender_ids_rejected_without_checkpoint(tmp_path, invalid):
    db = Store("sqlite:///" + str(tmp_path / "invalid.sqlite"))
    with pytest.raises(ValueError, match="sender user ID"):
        db.ingest(123, [row(1, invalid)])
    assert db.checkpoint(123) == 0
    db.engine.dispose()


def test_additive_migration_preserves_history_and_readonly_is_not_migrator(tmp_path):
    url = "sqlite:///" + str(tmp_path / "legacy.sqlite")
    first = Store(url)
    first.ingest(123, [row(5)])
    first.engine.dispose()
    # Emulate an existing installation that predates this migration.
    from sqlalchemy import create_engine
    eng = create_engine(url)
    with eng.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE messages DROP COLUMN sender_user_id")
    eng.dispose()
    viewer = Store(url, read_only=True)
    assert "sender_user_id" not in {col["name"] for col in inspect(viewer.engine).get_columns("messages")}
    viewer.engine.dispose()
    upgraded = Store(url)
    assert "sender_user_id" in {col["name"] for col in inspect(upgraded.engine).get_columns("messages")}
    assert upgraded.checkpoint(123) == 5
    assert upgraded.ingest(123, [row(5, 789)]) == 0
    with upgraded.engine.connect() as conn:
        assert conn.execute(select(messages.c.sender_user_id)).scalar_one() == 789
    upgraded.engine.dispose()


def test_author_id_shown_in_authenticated_api_and_daily_brief(tmp_path, monkeypatch):
    url = "sqlite:///" + str(tmp_path / "api.sqlite")
    db = Store(url)
    db.ingest(123, [row(7, 7900), row(8, None)])
    with db.engine.begin() as conn:
        conn.execute(update(messages).values(state="done", classification=json.dumps({
            "summary_ru": "Проверить сообщение о событии", "priority": "high",
            "topic": "Дорожная обстановка", "relevant": True,
        }, ensure_ascii=False)))
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("DASHBOARD_USER", "admin")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "long-test-password-123456789")
    api.store.cache_clear()
    client = TestClient(api.app)
    assert client.get("/api/events").status_code == 401
    headers = {"Authorization": "Basic " + base64.b64encode(
        b"admin:long-test-password-123456789").decode()}
    r = client.get("/api/events?limit=10", headers=headers)
    assert r.status_code == 200
    by_key = {x["key"]: x for x in r.json()["items"]}
    assert by_key["123:7"]["sender_user_id"] == 7900
    assert by_key["123:8"]["sender_user_id"] is None
    daily = client.get("/api/analytics/daily", headers=headers)
    assert daily.status_code == 200
    authors = [m["sender_user_id"] for p in daily.json()["top_publications"]
               for m in p["messages"]]
    assert 7900 in authors and None in authors
    signals = client.get("/api/analytics/signals?category=all", headers=headers)
    assert signals.status_code == 200
    assert {x["sender_user_id"] for x in signals.json()["items"]} == {7900, None}
    html = client.get("/", headers=headers)
    assert html.status_code == 200
    assert "ID автора:" in html.text
    assert "innerHTML" not in html.text
    api.store.cache_clear()
    db.engine.dispose()
