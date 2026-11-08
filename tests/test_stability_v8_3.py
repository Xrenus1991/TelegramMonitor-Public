"""Local deterministic stability tests: Telegram and API calls are mocked."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
import pytest
from sqlalchemy import text
from telegram_monitor.cloud import api, collector
from telegram_monitor.cloud.db import Store, messages
from telegram_monitor.cloud.source_registry import SourceRegistry, group_info
from fastapi.testclient import TestClient


class FakeEntity:
    id = 1101
    title = 'Группа'


class FakeClient:
    def __init__(self, count=9, *, failure_after=None):
        self.all_messages = [SimpleNamespace(id=i, message=f'Текст {i}', media=None,
            date=datetime(2026, 11, 1, tzinfo=timezone.utc)) for i in range(1, count + 1)]
        self.calls = []
        self.failure_after = failure_after
    def iter_messages(self, entity, *, min_id=None, reverse=False, limit=None):
        self.calls.append((min_id, reverse, limit))
        rows = [m for m in self.all_messages if min_id is None or m.id > min_id]
        if not reverse: rows.reverse()
        if limit is not None: rows = rows[:limit]
        async def gen():
            for n, row in enumerate(rows):
                if self.failure_after is not None and n >= self.failure_after:
                    raise RuntimeError('Interruption')
                yield row
        return gen()


def test_bounded_catchup_advances_by_small_pages(tmp_path):
    s=Store('sqlite:///'+str(tmp_path/'x.db'))
    e=FakeEntity()
    c=FakeClient()
    # Bootstrap only two most recent messages, by existing design.
    assert asyncio.run(collector.sync_entity(c,s,e,2,max_catchup=3)) == 2
    assert s.checkpoint(e.id)==9
    # A backlog begins after the bootstrap checkpoint.
    c.all_messages += [SimpleNamespace(id=i,message=f'Текст {i}',media=None,
                        date=datetime(2026,11,1,tzinfo=timezone.utc)) for i in range(10,21)]
    assert asyncio.run(collector.sync_entity(c,s,e,2,max_catchup=3))==3
    assert s.checkpoint(e.id)==12
    assert asyncio.run(collector.sync_entity(c,s,e,2,max_catchup=3))==3
    assert s.checkpoint(e.id)==15
    assert c.calls[-1]==(12, True, 3)
    assert asyncio.run(collector.sync_entity(c,s,e,2,max_catchup=3))==3
    assert asyncio.run(collector.sync_entity(c,s,e,2,max_catchup=3))==2
    assert s.checkpoint(e.id)==20
    with s.engine.connect() as conn:
        assert conn.execute(text('select count(*) from messages')).scalar_one()==13
    s.engine.dispose()


def test_interruption_does_not_advance_checkpoint(tmp_path):
    s=Store('sqlite:///'+str(tmp_path/'z.db'))
    c=FakeClient(4);e=FakeEntity()
    asyncio.run(collector.sync_entity(c,s,e,1,max_catchup=3))
    assert s.checkpoint(e.id)==4
    c.all_messages += [SimpleNamespace(id=i,message=f'Текст {i}',media=None,
                      date=datetime(2026,11,1,tzinfo=timezone.utc)) for i in range(5,14)]
    c.failure_after=1
    with pytest.raises(RuntimeError, match='Interruption'):
        asyncio.run(collector.sync_entity(c,s,e,1,max_catchup=3))
    assert s.checkpoint(e.id)==4
    c.failure_after=None
    assert asyncio.run(collector.sync_entity(c,s,e,1,max_catchup=3))==3
    assert s.checkpoint(e.id)==7
    s.engine.dispose()


def test_bad_catchup_limit_rejected(tmp_path):
    s=Store('sqlite:///'+str(tmp_path/'y.db'))
    with pytest.raises(ValueError):
        asyncio.run(collector.sync_entity(FakeClient(), s, FakeEntity(), 2, max_catchup=0))
    s.engine.dispose()


def make_reg(tmp_path):
    return SourceRegistry('sqlite:///'+str(tmp_path/'registry.db'),initialize=True)


def group(i):
    return {'peer_id':i,'title':f'Группа {i}','username':f'group{i}',
            'kind':'megagroup','visibility':'public'}


def test_mass_disappearance_is_ignored(tmp_path):
    r=make_reg(tmp_path)
    r.record_scan([group(i) for i in range(1, 13)])
    r.record_scan([group(i) for i in range(1,13)])
    with pytest.raises(ValueError,match='mass disappearance'):
        r.record_scan([group(1)])
    assert len(r.list_sources())==12
    assert all(item['status']=='baseline' for item in r.list_sources())
    r.dispose()


def test_audit_has_no_redundant_entries(tmp_path):
    r=make_reg(tmp_path)
    r.record_scan([])
    r.record_scan([group(10)],auto_public=False)
    assert r.change_status(10,'approve')=='approved'
    assert r.change_status(10,'approve')=='approved'
    assert r.change_status(10,'disable')=='disabled'
    assert [(x['from_status'],x['to_status']) for x in reversed(r.recent_audit())]==[
        ('pending','approved'),('approved','disabled')]
    r.dispose()


def test_dashboard_audit_auth_and_api(tmp_path,monkeypatch):
    url='sqlite:///'+str(tmp_path/'registry.db')
    r=SourceRegistry(url,initialize=True)
    r.record_scan([]);r.record_scan([group(1)],auto_public=False)
    r.change_status(1,'approve')
    monkeypatch.setenv('DATABASE_URL',url)
    monkeypatch.setenv('DASHBOARD_USER','admin')
    monkeypatch.setenv('DASHBOARD_PASSWORD','long_long_password_12345')
    monkeypatch.setenv('CLOUD_DISCOVERY_ENABLED','true')
    api.sources_registry.cache_clear()
    c=TestClient(api.app)
    assert c.get('/api/sources/audit').status_code==401
    resp=c.get('/api/sources/audit',auth=('admin','long_long_password_12345'))
    assert resp.status_code==200
    assert resp.json()['items'][0]['peer_id']==1
    api.sources_registry.cache_clear();r.dispose()


def test_db_credentials_separated(tmp_path,monkeypatch):
    monkeypatch.delenv('DATABASE_URL',raising=False)
    monkeypatch.setenv('DB_USER','collector')
    monkeypatch.setenv('DB_PASSWORD','collector-secret')
    monkeypatch.setenv('DASHBOARD_DB_USER','viewer')
    monkeypatch.setenv('DASHBOARD_DB_PASSWORD','viewer-secret')
    from sqlalchemy.engine import make_url
    u=make_url(api._db_url())
    assert u.username=='viewer' and u.password=='viewer-secret'
    monkeypatch.delenv('DASHBOARD_DB_PASSWORD')
    with pytest.raises(RuntimeError,match='Both'):
        api._db_url()
