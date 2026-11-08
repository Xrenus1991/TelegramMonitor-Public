"""API wiring checks: authorization, bounds and explicit feature gate."""
from fastapi.testclient import TestClient
from unittest.mock import patch
import base64
from telegram_monitor.cloud import api

def test_variant_search_auth_and_disabled(monkeypatch):
    monkeypatch.setenv("DASHBOARD_USER","test")
    monkeypatch.setenv("DASHBOARD_PASSWORD","long-test-password-12345")
    monkeypatch.setenv("CLOUD_VARIANT_SEARCH_ENABLED","false")
    # Legacy on-demand inspection must not implicitly enable the new search.
    monkeypatch.setenv("CLOUD_TEXT_VARIANTS_ENABLED","true")
    client=TestClient(api.app)
    assert client.get("/api/text/search-variants?q=пожар").status_code==401
    auth={"Authorization":"Basic "+base64.b64encode(b"test:long-test-password-12345").decode()}
    with patch.object(api,"store") as db:
        assert client.get("/api/text/search-variants?q=пожар",headers=auth).status_code==503
        db.assert_not_called()

def test_variant_search_rejects_invalid_bounds(monkeypatch):
    monkeypatch.setenv("DASHBOARD_USER","test")
    monkeypatch.setenv("DASHBOARD_PASSWORD","long-test-password-12345")
    monkeypatch.setenv("CLOUD_VARIANT_SEARCH_ENABLED","true")
    client=TestClient(api.app)
    auth={"Authorization":"Basic "+base64.b64encode(b"test:long-test-password-12345").decode()}
    with patch.object(api,"store") as db:
        for params in ({"q":"  "},{"q":"пожар","source_id":-1},
                       {"q":"пожар","before":"2026-11-05T00:00:00"}):
            assert client.get("/api/text/search-variants",params=params,headers=auth).status_code==422
        db.assert_not_called()


def test_search_sql_source_window_and_original_preservation(tmp_path, monkeypatch):
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import insert, select
    from telegram_monitor.cloud.db import Store, messages
    monkeypatch.setenv("DASHBOARD_USER","test")
    monkeypatch.setenv("DASHBOARD_PASSWORD","long-test-password-12345")
    monkeypatch.setenv("CLOUD_VARIANT_SEARCH_ENABLED","true")
    url="sqlite:///"+str(tmp_path/"search.sqlite")
    writer=Store(url)
    when=datetime(2026,11,5,12,tzinfo=timezone.utc)
    with writer.engine.begin() as conn:
        conn.execute(insert(messages),[
            dict(key=f"{source}:{i}",channel_id=source,message_id=i,channel_name="fixture",
                 timestamp=when+timedelta(seconds=i),created_at=when,text="пoжар")
            for source in (11,22) for i in range(105)])
    writer.engine.dispose()
    reader=Store(url,read_only=True)
    def normalize(text, **kwargs):
        return {"available":True,"normalized":text.replace("пoжар","пожар")}
    auth={"Authorization":"Basic "+base64.b64encode(b"test:long-test-password-12345").decode()}
    try:
        with patch.object(api,"store",return_value=reader), \
             patch("telegram_monitor.cloud.variant_search.inspect_unicode",side_effect=normalize):
            client=TestClient(api.app)
            response=client.get("/api/text/search-variants",
                params={"q":"пожар","source_id":11},headers=auth)
            assert response.status_code==200
            body=response.json()
            assert body["scanned"]==100 and body["candidate_truncated"]
            assert len(body["matches"])==100
            assert all(row["key"].startswith("11:") for row in body["matches"])
            assert body["matches"][0]["key"]=="11:104"
            assert body["scope"]=="latest_candidate_window"
            empty=client.get("/api/text/search-variants",
                params={"q":"пожар","source_id":33},headers=auth).json()
            assert empty["matches"]==[] and empty["scanned"]==0
            earlier=client.get("/api/text/search-variants",
                params={"q":"пожар","source_id":11,"before":(when+timedelta(seconds=2)).isoformat()},
                headers=auth).json()
            assert {row["key"] for row in earlier["matches"]}=={"11:0","11:1"}
        with reader.engine.connect() as conn:
            texts=conn.execute(select(messages.c.text)).scalars().all()
        assert len(texts)==210 and set(texts)=={"пoжар"}
    finally:
        reader.engine.dispose()


def test_same_timestamp_pages_have_no_gaps_even_without_matches(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from sqlalchemy import insert
    from telegram_monitor.cloud.db import Store, messages
    monkeypatch.setenv("DASHBOARD_USER","test")
    monkeypatch.setenv("DASHBOARD_PASSWORD","long-test-password-12345")
    monkeypatch.setenv("CLOUD_VARIANT_SEARCH_ENABLED","true")
    url="sqlite:///"+str(tmp_path/"pages.sqlite")
    writer=Store(url)
    at=datetime(2026,11,5,12,tzinfo=timezone.utc)
    keys={f"11:{i}" for i in range(205)}
    with writer.engine.begin() as conn:
        conn.execute(insert(messages),[dict(key=k,channel_id=11,message_id=int(k.split(":")[1]),
            channel_name="fixture",timestamp=at,created_at=at,text="ordinary") for k in keys])
    writer.engine.dispose()
    reader=Store(url,read_only=True)
    auth={"Authorization":"Basic "+base64.b64encode(b"test:long-test-password-12345").decode()}
    def normalize(value,**kwargs):return {"available":True,"normalized":value}
    try:
        with patch.object(api,"store",return_value=reader),patch("telegram_monitor.cloud.variant_search.inspect_unicode",side_effect=normalize):
            client=TestClient(api.app)
            params={"q":"ordinary","source_id":11};seen=[]
            for _ in range(3):
                response=client.get("/api/text/search-variants",params=params,headers=auth)
                assert response.status_code==200
                data=response.json();seen.extend(x["key"] for x in data["matches"])
                if data["next_before"] is None:break
                params.update(before=data["next_before"],before_key=data["next_before_key"])
            assert len(seen)==205 and set(seen)==keys
            assert data["next_before"] is None
            empty=client.get("/api/text/search-variants",params={"q":"missing"},headers=auth).json()
            assert empty["matches"]==[] and empty["next_before_key"]
            assert client.get("/api/text/search-variants",params={"q":"ordinary","before_key":"11:3"},headers=auth).status_code==422
    finally:reader.engine.dispose()
