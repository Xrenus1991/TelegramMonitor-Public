"""Dedicated disposable PostgreSQL contract; never uses production credentials."""
import os
from datetime import datetime,timezone
from unittest.mock import patch
import base64
import pytest
from sqlalchemy import insert,select,text
from fastapi.testclient import TestClient
from telegram_monitor.cloud import api
from telegram_monitor.cloud.db import Store,messages

@pytest.mark.skipif(not os.getenv("TM_VARIANT_TEST_PG"),reason="isolated PostgreSQL job only")
def test_postgres_real_normalizer_and_readonly():
    url=os.environ["TM_VARIANT_TEST_PG"]
    # Only the dedicated CI fixture URL is accepted.
    assert url=="postgresql+psycopg://postgres:fixture@127.0.0.1:5432/variant_fixture"
    writer=Store(url)
    now=datetime.now(timezone.utc)
    originals=["пoжар","пожар","https://example.com/пoжар","Հայաստան hello","x"*4001+"пожар"]
    with writer.engine.begin() as conn:
        conn.execute(insert(messages),[
            dict(key=f"{source}:{i}",channel_id=source,message_id=i,channel_name="fixture",
                 timestamp=now,created_at=now,text=value)
            for source in (11,22) for i,value in enumerate(originals)])
    writer.engine.dispose()
    reader=Store(url,read_only=True)
    env={"DASHBOARD_USER":"fixture","DASHBOARD_PASSWORD":"fixture-password-not-production",
         "CLOUD_VARIANT_SEARCH_ENABLED":"true"}
    auth={"Authorization":"Basic "+base64.b64encode(b"fixture:fixture-password-not-production").decode()}
    try:
        with reader.engine.connect() as conn:
            assert conn.execute(text("SHOW transaction_read_only")).scalar()=="on"
        with patch.dict(os.environ,env),patch.object(api,"store",return_value=reader):
            client=TestClient(api.app)
            response=client.get("/api/text/search-variants",params={"q":"пожар","source_id":11},headers=auth)
            assert response.status_code==200
            body=response.json()
            assert {m["key"] for m in body["matches"]}=={"11:0","11:1"}
            assert body["scanned"]==5 and body["text_truncated"]
            assert body["persisted"] is False
        with reader.engine.connect() as conn:
            values=conn.execute(select(messages.c.text).where(messages.c.channel_id==11).order_by(messages.c.message_id)).scalars().all()
        assert values==originals
    finally:
        reader.engine.dispose()
