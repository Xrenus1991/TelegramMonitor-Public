"""Dashboard v8 isolated tests; SQLite only, never the production database."""
import json
import base64
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert

from telegram_monitor.cloud import api
from telegram_monitor.cloud.db import Store, messages


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "dashboard_test.db"
    s = Store("sqlite:///" + str(db_path))
    at = datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc)
    inputs = [
        ("1:1", "Новости Армении", "Граница Армении: официальное сообщение", "Граница", "high", "Сводка: граница"),
        ("1:2", "Новости Армении", "Политика и выборы", "Политика", "medium", "Краткая сводка"),
        ("2:3", "Новости Еревана", "Работа 100% скидка", "Реклама", "low", "Объявление о работе"),
        ("2:4", "Новости Еревана", "Погода", "Общество", "low", "Погода"),
        ("2:5", "Новости Еревана", "literal %_ term", "Общество", "low", "Тест"),
    ]
    with s.engine.begin() as conn:
        for ix, (key, channel, body, topic, priority, summary) in enumerate(inputs):
            conn.execute(insert(messages).values(key=key, channel_id=1 if ix<2 else 2,
                message_id=ix+1, channel_name=channel, timestamp=at+timedelta(minutes=ix),
                text=body, state="done", tries=0, deep_tries=0, deep_state="skip",
                created_at=at, classification=json.dumps({"topic": topic,
                "priority": priority, "summary_ru": summary, "source_uncertainty": "Проверить"},ensure_ascii=False)))
        conn.execute(insert(messages).values(key="1:99", channel_id=1, message_id=99,
            channel_name="Новости Армении", timestamp=at, text="Секрет из очереди",
            state="pending", tries=0, deep_tries=0, deep_state="skip",created_at=at))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(db_path))
    monkeypatch.setenv("DASHBOARD_USER", "admin")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "Long_Test_Password_123456789")
    api.store.cache_clear()
    test_client = TestClient(api.app)
    test_client.headers.update({"Authorization": "Basic " + base64.b64encode(b"admin:Long_Test_Password_123456789").decode()})
    yield test_client
    api.store.cache_clear()
    s.engine.dispose()


def test_authentication_and_html(client):
    assert client.get("/api/events", auth=("admin", "wrong_password")).status_code == 401
    assert client.get("/api/stats", auth=("admin", "wrong_password")).status_code == 401
    r=client.get("/")
    assert r.status_code == 200
    assert "Telegram Monitor" in r.text
    assert 'no-store' in r.headers['cache-control']
    assert "Content-Security-Policy" in r.headers
    assert "textContent" in r.text
    assert "innerHTML" not in r.text


def test_pagination_and_only_processed(client):
    p=client.get('/api/events?limit=2&offset=0').json()
    assert p['total']==5 and p['has_more'] is True and len(p['items'])==2
    assert p['items'][0]['key']=='2:5'
    p2=client.get('/api/events?limit=2&offset=4').json()
    assert p2['total']==5 and p2['has_more'] is False and len(p2['items'])==1
    assert all(e['key']!='1:99' for e in p['items']+p2['items'])


def test_search_literals_filters_options(client):
    r=client.get('/api/events',params={'q':'Граница','topic':'Граница','priority':'high'}).json()
    assert r['total']==1 and r['items'][0]['source']=='Новости Армении'
    assert client.get('/api/events?q=100%25').json()['total']==1
    assert client.get('/api/events?q=%25_').json()['total']==1
    assert client.get('/api/events?q=очереди').json()['total']==0
    assert client.get('/api/events',params={'source':'Новости Еревана','priority':'medium'}).json()['total']==0
    opts=client.get('/api/filters').json()
    assert opts['sources']==['Новости Армении','Новости Еревана']
    assert 'Граница' in opts['topics'] and 'Реклама' in opts['topics']


def test_date_range_and_validation(client):
    first=client.get('/api/events',params={'since':'2026-11-01T10:02:00+00:00',
                                          'until':'2026-11-01T10:03:00+00:00'}).json()
    assert first['total']==2
    assert client.get('/api/events?offset=-1').status_code==422
    assert client.get('/api/events?limit=300').status_code==422
    assert client.get('/api/events?since=2026-11-01T10:00:00').status_code==422
    assert client.get('/api/events',params={'since':'2026-11-02T10:00:00Z',
                                          'until':'2026-11-01T10:00:00Z'}).status_code==422


def test_stats_backward_compatible(client):
    r=client.get('/api/stats')
    assert r.status_code==200
    assert r.json()['messages']=={'done':5,'pending':1}
    assert client.get('/health').json()=={'service':'up'}


def test_unicode_username_does_not_crash(monkeypatch):
    from fastapi import HTTPException
    from fastapi.security import HTTPBasicCredentials
    monkeypatch.setenv("DASHBOARD_USER", "admin")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "Long_Test_Password_123456789")
    with pytest.raises(HTTPException) as exc:
        api.authorize(HTTPBasicCredentials(username="админ", password="неверно"))
    assert exc.value.status_code == 401


def test_postgres_json_filter_compiles_without_live_connection():
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy import select, cast
    expression=cast(messages.c.classification, JSONB)["topic"].astext
    compiled=str(select(messages.c.key).where(expression == 'Граница').compile(
        dialect=postgresql.dialect()))
    assert 'JSONB' in compiled and '->>' in compiled
