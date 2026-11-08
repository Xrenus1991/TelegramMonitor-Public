"""Regression tests for v8.4 read-only local analytical endpoints."""
from datetime import date, datetime, timedelta, timezone
import json
import pytest
from sqlalchemy import insert, select, func
from fastapi.testclient import TestClient
from telegram_monitor.cloud.analytics import (
    text_key, place_mentions, cluster_posts, duplicate_summary, daily_brief,
    fetch_processed, utc_day_bounds,
)
from telegram_monitor.cloud.db import Store, messages, now
from telegram_monitor.cloud import api

TIME = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
TEXT_A = 'В Ереване завершили проверку дорожного движения на центральной улице после вечернего ограничения.'
TEXT_B = 'В Ереване завершили проверку дорожного движения на центральной улице после вечернего ограничения!'
TEXT_C = 'В Гюмри завершили проверку дорожного движения на центральной улице после вечернего ограничения.'


def row(key='1:1', text=TEXT_A, stamp=TIME, cid=1, state='done', classification=None):
    return {
        'key': key, 'channel_id': cid, 'message_id': int(key.split(':')[-1]),
        'channel_name': 'Канал '+str(cid), 'timestamp': stamp, 'text': text,
        'classification': (json.dumps(classification, ensure_ascii=False) if isinstance(classification, dict) else classification),
        'state': state,
    }


def test_clean_text_preserves_numbers_and_names():
    assert text_key('В Ереване 12:00 https://t.me/item @admin_xxx') == 'в ереване 12 00'


def test_city_word_not_substring():
    assert place_mentions('Новости в Ереване и Гюмри') == ['Гюмри', 'Ереван']
    assert place_mentions('Про ереванскую дорогу') == []


def test_armenian_city_name():
    assert place_mentions('Նորություններ Երևան և Գյումրի') == ['Гюмри', 'Ереван']


def test_near_duplicate_merge():
    groups = cluster_posts([row('1:1', TEXT_A), row('2:2', TEXT_B, cid=2)])
    assert sorted(map(len, groups)) == [2]


def test_different_city_not_merged():
    groups = cluster_posts([row('1:1', TEXT_A), row('2:2', TEXT_C, cid=2)])
    assert sorted(map(len, groups)) == [1, 1]


def test_different_numbers_not_merged():
    groups = cluster_posts([row('1:1', TEXT_A+' 2025'), row('2:2', TEXT_A+' 2026', cid=2)])
    assert len(groups) == 2


def test_too_short_never_merged():
    groups = cluster_posts([row('1:1', 'Новость'), row('2:2', 'Новость', cid=2)])
    assert len(groups) == 2


def test_media_placeholder_not_clustered():
    text='[МЕДИА БЕЗ ТЕКСТОВОЙ ПОДПИСИ — содержимое не изучено]'
    assert len(cluster_posts([row('1:1', text), row('2:2', text, cid=2)])) == 2


def test_time_window_exceeded():
    groups = cluster_posts([row('1:1', TEXT_A), row('2:2', TEXT_A, TIME-timedelta(days=2), cid=2)])
    assert len(groups) == 2


def test_same_source_not_misrepresented_as_independent():
    out = duplicate_summary([row('1:1', TEXT_A), row('1:2', TEXT_A)], False, hours=72)
    assert out['possible_repeat_messages'] == 1
    assert out['items'][0]['distinct_sources'] == 1
    assert out['items'][0]['places_mentioned'] == ['Ереван']


def test_safe_invalid_json_and_long_summary():
    out = daily_brief([row(classification='bad json')], False, day=TIME.date())
    assert out['top_topics'] == []
    assert len(out['top_publications']) == 1


def test_daily_counts_only_seen_rows():
    rows = [row('1:1', TEXT_A, classification={'priority':'high','topic':'transport','relevant':True}),
            row('2:2', TEXT_B, cid=2, classification={'priority':'low','topic':'transport','relevant':True})]
    out = daily_brief(rows, True, day=TIME.date())
    assert out['priority_counts']['high'] == 1
    assert out['priority_counts']['low'] == 1
    assert out['possible_repeat_groups'] == 1
    assert out['sample_truncated'] is True


def test_utc_day_window():
    start, end = utc_day_bounds(date(2026, 11, 2))
    assert start.isoformat() == '2026-11-02T00:00:00+00:00'
    assert end - start == timedelta(days=1)


def test_db_query_is_read_only_and_bounded(tmp_path):
    dbpath=tmp_path/'a.db'
    writable=Store('sqlite:///'+str(dbpath))
    with writable.engine.begin() as c:
        for i in range(3):
            r=row('5:'+str(i+1), TEXT_A, stamp=TIME+timedelta(minutes=i), cid=5)
            c.execute(insert(messages).values(**r, tries=0, deep_tries=0, deep_state='skip', created_at=TIME))
    before_count = None
    with writable.engine.connect() as c:
        before_count=c.execute(select(func.count()).select_from(messages)).scalar_one()
    readonly=Store('sqlite:///'+str(dbpath), read_only=True)
    a,truncated=fetch_processed(readonly.engine,TIME-timedelta(days=1),TIME+timedelta(days=1),cap=2)
    assert len(a)==2 and truncated is True
    with readonly.engine.connect() as c:
        assert c.execute(select(func.count()).select_from(messages)).scalar_one()==before_count
    with pytest.raises(ValueError):
        fetch_processed(readonly.engine,TIME,TIME,cap=3)
    with pytest.raises(ValueError):
        fetch_processed(readonly.engine,TIME,TIME+timedelta(days=1),cap=2001)


def test_api_rejects_anonymous_requests():
    client=TestClient(api.app)
    assert client.get('/api/analytics/duplicates').status_code == 401
    assert client.get('/api/analytics/daily').status_code == 401


def test_api_authenticated_and_future_rejected(tmp_path, monkeypatch):
    db=Store('sqlite:///'+str(tmp_path/'test.db'))
    with db.engine.begin() as c:
        for k, val in enumerate((TEXT_A,TEXT_B)):
            r=row('5:'+str(k+1), val, stamp=now()-timedelta(hours=1), cid=5+k,
                  classification={'priority':'high','topic':'Новости','summary_ru':'Проверка движения'})
            c.execute(insert(messages).values(**r, tries=0, deep_tries=0, deep_state='skip', created_at=now()))
    api.store.cache_clear()
    monkeypatch.setattr(api,'store',lambda:Store('sqlite:///'+str(tmp_path/'test.db'),read_only=True))
    monkeypatch.setenv('DASHBOARD_PASSWORD','1234567890securepassword')
    cl=TestClient(api.app)
    r=cl.get('/api/analytics/duplicates',auth=('admin','1234567890securepassword'))
    assert r.status_code==200, r.text
    assert r.json()['possible_repeat_groups']==1
    # FastAPI receives query values as strings: all advertised hour options must work.
    for hours in (24, 72, 168):
        r = cl.get(f'/api/analytics/duplicates?hours={hours}',
                   auth=('admin','1234567890securepassword'))
        assert r.status_code == 200, (hours, r.text)
        assert r.json()['window_hours'] == hours
    r=cl.get('/api/analytics/daily',auth=('admin','1234567890securepassword'))
    assert r.status_code==200, r.text
    assert r.json()['processed_in_sample']==2
    r=cl.get('/api/analytics/daily?day=9999-12-31',auth=('admin','1234567890securepassword'))
    assert r.status_code==422
    r=cl.get('/api/analytics/duplicates?hours=99',auth=('admin','1234567890securepassword'))
    assert r.status_code==422


def test_ui_no_dynamic_innerhtml():
    from pathlib import Path
    markup=Path(api.__file__).with_name('dashboard_v8_3.html').read_text()
    assert 'Похожие публикации' in markup
    assert 'analyticsCard' in markup
    assert 'innerHTML' not in markup
