"""Per-source progress and peer-id result filtering are read-only and auditable."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from telegram_monitor.cloud import api
from telegram_monitor.cloud.db import Store, messages
from telegram_monitor.cloud.source_registry import SourceRegistry


def _src(pid, title):
    return {"peer_id": pid, "title": title, "username": "test" + str(pid),
            "kind": "megagroup", "visibility": "public"}


@pytest.fixture
def dataset(tmp_path):
    url = "sqlite:///" + str(tmp_path / "progress.sqlite")
    db = Store(url)
    reg = SourceRegistry(url, initialize=True)
    reg.record_scan([_src(101, "Renamed group"), _src(102, "Same title"), _src(103, "Empty group")],
                    auto_public=False)
    when = datetime(2026, 11, 5, tzinfo=timezone.utc)
    db.ingest(101, [
        {"channel_id": 101, "message_id": i, "channel_name": "Old group title",
         "timestamp": when, "text": "Content " + str(i)}
        for i in range(1, 6)])
    db.ingest(102, [
        {"channel_id": 102, "message_id": i, "channel_name": "Old group title",
         "timestamp": when, "text": "Different " + str(i)}
        for i in range(1, 4)])
    with db.engine.begin() as conn:
        for id_, state in [(1,"done"),(2,"done"),(3,"processing"),(4,"failed"),(5,"pending")]:
            conn.execute(update(messages).where(
                (messages.c.channel_id == 101) & (messages.c.message_id == id_)
            ).values(state=state, classification='{"summary_ru":"Synthetic"}' if state == "done" else None))
        conn.execute(update(messages).where(
            (messages.c.channel_id == 102) & (messages.c.message_id == 1)
        ).values(state="done",classification='{"summary_ru":"Separate group"}'))
    yield db, reg
    db.engine.dispose()
    reg.dispose()


def test_grouped_progress_reports_all_states_and_zero_groups(dataset):
    db, _ = dataset
    data = db.source_progress({101,102,103})
    assert data[101] == {"total":5,"done":2,"pending":1,"processing":1,
                         "failed":1,"other":0,"percent":40.0}
    assert data[102] == {"total":3,"done":1,"pending":2,"processing":0,
                         "failed":0,"other":0,"percent":33.3}
    assert data[103] == {"total":0,"done":0,"pending":0,"processing":0,
                         "failed":0,"other":0,"percent":0}
    assert db.source_progress(set()) == {}
    for values in ({True}, {-1}, {0}, {2**63}):
        with pytest.raises(ValueError):
            db.source_progress(values)


def test_event_filter_by_numeric_id_not_title(dataset):
    db, _ = dataset
    data=db.events_page(source_id=101)
    assert data["total"]==2 and all(item["source"]=="Old group title" for item in data["items"])
    assert db.events_page(source_id=102)["total"]==1
    assert db.events_page(source_id=103)["total"]==0
    assert db.events_page(source_id=101,source="Same title")["total"]==0
    with pytest.raises(ValueError):
        db.events_page(source_id=True)
    with pytest.raises(ValueError):
        db.events_page(source_id=0)


def test_authenticated_sources_include_progress_and_filtered_results(dataset, monkeypatch):
    db, reg = dataset
    monkeypatch.setenv("DASHBOARD_USER","operator")
    monkeypatch.setenv("DASHBOARD_PASSWORD","synthetic-password-long-enough")
    monkeypatch.setenv("CLOUD_DISCOVERY_ENABLED","true")
    monkeypatch.setenv("CLOUD_DISCOVERY_UI_WRITE","false")
    monkeypatch.delenv("CLOUD_SOURCE_CONTROL_DATABASE_URL",raising=False)
    monkeypatch.setattr(api,"store",lambda:db)
    monkeypatch.setattr(api,"sources_registry",lambda:reg)
    client=TestClient(api.app)
    auth=("operator","synthetic-password-long-enough")
    assert client.get("/api/sources").status_code==401
    result=client.get("/api/sources",auth=auth)
    assert result.status_code==200
    body=result.json()
    assert body["progress_available"] is True
    items={item["peer_id"]:item for item in body["items"]}
    assert items[101]["analysis_progress"]["percent"]==40.0
    assert items[103]["analysis_progress"]["total"]==0
    assert client.get("/api/events?source_id=101",auth=auth).json()["total"]==2
    assert client.get("/api/events?source_id=102",auth=auth).json()["total"]==1
    for value in ("0","-2","abc",str(2**63)):
        assert client.get("/api/events?source_id="+value,auth=auth).status_code==422
