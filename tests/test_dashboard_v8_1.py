"""v8.1 regression tests; test databases are local temporary SQLite files."""
import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, text, select
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from telegram_monitor.cloud import api
from telegram_monitor.cloud.db import Store, messages


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    url = 'sqlite:///' + str(tmp_path / 'safe.db')
    writable = Store(url)
    at = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    with writable.engine.begin() as conn:
        for i in range(10):
            conn.execute(insert(messages).values(
                key=f'1:{i+1}', channel_id=1, message_id=i+1,
                channel_name='Новости', timestamp=at+timedelta(seconds=i),
                text=f'Тестовое сообщение {i}', state='done', tries=0,
                deep_tries=0, deep_state='skip', created_at=at,
                classification=json.dumps({'topic':'Новости','priority':'low',
                                           'summary_ru':f'Сводка {i}'}, ensure_ascii=False)))
    monkeypatch.setenv('DATABASE_URL', url)
    monkeypatch.setenv('DASHBOARD_USER','admin')
    monkeypatch.setenv('DASHBOARD_PASSWORD','secure_password_123456789')
    api.store.cache_clear()
    client = TestClient(api.app)
    client.headers.update({'Authorization': 'Basic ' + base64.b64encode(b'admin:secure_password_123456789').decode()})
    yield writable, client, at
    api.store.cache_clear()
    writable.engine.dispose()


def test_readonly_connection_forbids_mutation(runtime):
    writer, client, _ = runtime
    readonly = api.store()
    assert readonly.read_only is True
    # Read queries work but writes fail in database engine, not merely at UI layer.
    assert len(readonly.events(2)) == 2
    with pytest.raises(OperationalError):
        with readonly.engine.begin() as conn:
            conn.execute(text("DELETE FROM messages"))
    with writer.engine.connect() as conn:
        assert conn.execute(select(messages.c.key)).first() is not None
    readonly.engine.dispose()
    api.store.cache_clear()


def test_no_schema_creation_in_readonly_mode(tmp_path):
    path = tmp_path/'not-initialized.db'
    s = Store('sqlite:///'+str(path), read_only=True)
    # No schema auto-creation. Can open SQLite file, but tables aren't created.
    with s.engine.connect() as conn:
        found = conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'").all()
    assert found == []
    s.engine.dispose()


def test_corrupted_json_is_skipped_not_500(runtime):
    writer, client, at = runtime
    with writer.engine.begin() as conn:
        conn.execute(insert(messages).values(key='1:99', channel_id=1, message_id=99,
            channel_name='Новости', timestamp=at+timedelta(minutes=20),
            text='Плохой JSON',state='done',tries=0,deep_tries=0,
            deep_state='skip',created_at=at,classification='{not json',deep_analysis='["not object"]'))
    result=client.get('/api/events?limit=30')
    assert result.status_code == 200
    assert result.json()['items'][0]['classification'] is None
    assert result.json()['items'][0]['deep_analysis'] is None
    assert client.get('/api/filters').status_code == 200
    assert client.get('/api/events?topic=Новости').json()['total'] == 10
    assert client.get('/api/events?priority=low').json()['total'] == 10


def test_cursor_pagination_stable_with_new_messages(runtime):
    writer, client, at = runtime
    one=client.get('/api/events?limit=3').json()
    first_keys=[v['key'] for v in one['items']]
    assert first_keys == ['1:10','1:9','1:8']
    assert one['next_cursor'] and one['has_more']
    with writer.engine.begin() as conn:
        conn.execute(insert(messages).values(key='1:100', channel_id=1,message_id=100,
            channel_name='Новости',timestamp=at+timedelta(days=1),text='Свежая запись',
            state='done',tries=0,deep_tries=0,deep_state='skip',created_at=at))
    two=client.get('/api/events', params={'limit':3,'cursor':one['next_cursor']}).json()
    second_keys=[v['key'] for v in two['items']]
    assert second_keys == ['1:7','1:6','1:5']
    assert not set(first_keys) & set(second_keys)
    assert one['total'] == 10 and two['total'] == 11
    assert client.get('/api/events?limit=30').json()['items'][0]['key']=='1:100'


def test_cursor_validation(runtime):
    _, client, _ = runtime
    assert client.get('/api/events?cursor=broken').status_code == 422
    valid=client.get('/api/events?limit=1').json()['next_cursor']
    assert client.get('/api/events',params={'cursor':valid,'offset':1}).status_code==422
    assert client.get('/api/events',params={'cursor':'A'*600}).status_code==422
    assert client.get('/api/events',params={'cursor':'%%%'}).status_code==422


def test_diagnostics_and_sanitized_errors(runtime, monkeypatch):
    _, client, _ = runtime
    r=client.get('/api/diagnostics')
    assert r.status_code==200
    assert r.json()['last_stored_at'] is not None
    assert r.json()['expired_processing_leases']==0
    assert r.json()['failed_messages']==0
    assert 'docker' in r.json()['note'].lower()
    assert client.get('/api/diagnostics',auth=('admin','bad')).status_code==401
    class Broken:
        def stats(self):
            raise SQLAlchemyError('sensitive sql text SECRET')
    original_store = api.store
    monkeypatch.setattr(api,'store',lambda:Broken())
    try:
        r=client.get('/api/stats')
        assert r.status_code==503
        assert 'SECRET' not in r.text
        assert 'Database temporarily unavailable' in r.text
    finally:
        monkeypatch.setattr(api, 'store', original_store)


def test_postgres_safe_json_compiles_without_live_connection():
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy import cast, func, case
    expr = case((func.pg_input_is_valid(messages.c.classification, 'jsonb'),
                 cast(messages.c.classification, JSONB)['topic'].astext), else_=None)
    sql=str(select(expr).compile(dialect=postgresql.dialect()))
    assert 'pg_input_is_valid' in sql and 'CASE WHEN' in sql and 'JSONB' in sql


def test_html_headers_and_no_script_injection(runtime):
    _, client, _ = runtime
    r=client.get('/')
    assert r.status_code==200
    assert 'v8.3' in r.text
    assert 'next_cursor' in r.text
    assert 'innerHTML' not in r.text
    assert 'no-store' in r.headers['cache-control']
    assert 'no-referrer' == r.headers['referrer-policy']
    assert 'Content-Security-Policy' in r.headers
