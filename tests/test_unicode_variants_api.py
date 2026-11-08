import json
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from telegram_monitor.cloud import api, unicode_context
from telegram_monitor.cloud.db import Store, messages


@pytest.fixture
def setup(tmp_path, monkeypatch):
    url = "sqlite:///" + str(tmp_path / "variants.sqlite")
    database = Store(url)
    database.ingest(123, [dict(channel_id=123, message_id=1, channel_name="Synthetic",
        text="Гpaницa https://example.org/Гpaницa", timestamp=datetime.now(timezone.utc))])
    with database.engine.begin() as con:
        con.execute(update(messages).values(state="done", classification=json.dumps({"summary_ru":"Original summary"})))
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("DASHBOARD_USER", "operator")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "synthetic-password-12345678")
    monkeypatch.setenv("CLOUD_TEXT_VARIANTS_ENABLED", "true")
    api.store.cache_clear()
    client = TestClient(api.app)
    yield database, client
    api.store.cache_clear()
    database.engine.dispose()


def test_auth_disabled_invalid_and_missing(setup, monkeypatch):
    database, client = setup
    assert client.get("/api/text/variants?key=123:1").status_code == 401
    client.auth=("operator", "synthetic-password-12345678")
    assert client.get("/api/text/variants?key=invalid").status_code == 422
    assert client.get("/api/text/variants?key=123:999").status_code == 404
    monkeypatch.setenv("CLOUD_TEXT_VARIANTS_ENABLED", "false")
    assert client.get("/api/text/variants?key=123:1").status_code == 503


def test_derived_text_does_not_write_analysis_or_original(setup):
    pytest.importorskip("confusable_homoglyphs")
    database, client = setup
    client.auth=("operator", "synthetic-password-12345678")
    with database.engine.connect() as con:
        before=dict(con.execute(select(messages)).mappings().one())
    result=client.get("/api/text/variants?key=123:1")
    assert result.status_code==200
    data=result.json()
    assert data["original_excerpt"]=="Гpaницa https://example.org/Гpaницa"
    assert data["normalized_excerpt"]=="граница https://example.org/Гpaницa"
    assert data["version"]=="ru-context-v2" and data["persisted"] is False
    assert "no-store" in result.headers["Cache-Control"]
    with database.engine.connect() as con:
        after=dict(con.execute(select(messages)).mappings().one())
    assert before==after


def test_missing_processor_is_explicit(setup, monkeypatch):
    _, client = setup
    client.auth=("operator", "synthetic-password-12345678")
    monkeypatch.setattr(unicode_context, "_backend", lambda: None)
    assert client.get("/api/text/variants?key=123:1").status_code==503


def test_long_excerpt_is_bounded_and_boundary_not_repaired(setup):
    pytest.importorskip("confusable_homoglyphs")
    database, client = setup
    value="Гpaницa " + " " * (3998-len("Гpaницa ")) + "Гpaницa"
    with database.engine.begin() as con:
        con.execute(update(messages).values(text=value))
    client.auth=("operator", "synthetic-password-12345678")
    data=client.get("/api/text/variants?key=123:1").json()
    assert data["truncated"] and data["source_length"]==len(value)
    assert len(data["original_excerpt"])==4000
    assert data["original_excerpt"].endswith("Гp")
    assert data["normalized_excerpt"].endswith("Гp")
    assert data["normalized_excerpt"].startswith("граница ")
