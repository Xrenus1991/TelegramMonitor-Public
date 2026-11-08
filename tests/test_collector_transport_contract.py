"""Synthetic interrupted Telegram iterators; no sessions, network or paid calls."""
import asyncio
import importlib.metadata
from datetime import datetime,timezone
from types import SimpleNamespace
import pytest
from sqlalchemy import select,func
from telegram_monitor.cloud.db import Store,messages
from telegram_monitor.cloud.collector import sync_entity,resolve_sources
def failure(kind):
    if kind=="flood":
        try:importlib.metadata.version("Telethon")
        except importlib.metadata.PackageNotFoundError:pytest.skip("actual Telethon required for FloodWait contract")
        from telethon.errors import FloodWaitError
        return FloodWaitError(request=None,capture=7)
    return OSError("synthetic transport interruption") if kind=="io" else ConnectionError("synthetic disconnect")
class Interrupted:
    def __init__(self,exception,*,count=251,stop=135,empty_tail=False):
        self.exception=exception;self.count=count;self.stop=stop;self.empty_tail=empty_tail
    async def iter_messages(self,entity,**kwargs):
        ids=list(range(kwargs.get("min_id",0)+1,self.count+1))
        if not kwargs.get("reverse"):ids=list(reversed(ids))[:kwargs["limit"]]
        for index,mid in enumerate(ids):
            if self.exception is not None and index==self.stop:raise self.exception
            yield SimpleNamespace(id=mid,message=None if self.empty_tail and mid>101 else "synthetic",
                                  date=datetime.now(timezone.utc),media=None)
def seed(s):
    s.ingest(111,[dict(channel_id=111,message_id=1,channel_name="fixture",
                   timestamp=datetime.now(timezone.utc),text="synthetic")])
@pytest.mark.parametrize("kind",["io","disconnect","flood"])
def test_catchup_restarts_from_last_committed_batch(tmp_path,kind):
    err=failure(kind);s=Store("sqlite:///"+str(tmp_path/"catchup.db"))
    entity=SimpleNamespace(id=111,title="fixture");seed(s)
    try:
        with pytest.raises(type(err)):asyncio.run(sync_entity(Interrupted(err),s,entity,300))
        assert s.checkpoint(111)==101
        assert asyncio.run(sync_entity(Interrupted(None),s,entity,300))==150
        assert s.checkpoint(111)==251
        assert asyncio.run(sync_entity(Interrupted(None),s,entity,300))==0
        with s.engine.connect() as conn:
            assert conn.execute(select(func.count()).select_from(messages)).scalar_one()==251
    finally:s.engine.dispose()
@pytest.mark.parametrize("kind",["io","disconnect","flood"])
def test_interrupted_bootstrap_does_not_advance_checkpoint(tmp_path,kind):
    err=failure(kind);s=Store("sqlite:///"+str(tmp_path/"bootstrap.db"));entity=SimpleNamespace(id=111,title="fixture")
    try:
        with pytest.raises(type(err)):asyncio.run(sync_entity(Interrupted(err,count=150,stop=75),s,entity,150))
        assert s.checkpoint(111)==0
        with s.engine.connect() as conn:
            assert conn.execute(select(func.count()).select_from(messages)).scalar_one()==0
        assert asyncio.run(sync_entity(Interrupted(None,count=150),s,entity,150))==150
        assert s.checkpoint(111)==150
    finally:s.engine.dispose()
def test_empty_tail_checkpoint_waits_for_successful_scan(tmp_path):
    s=Store("sqlite:///"+str(tmp_path/"empty.db"));entity=SimpleNamespace(id=111,title="fixture");seed(s)
    try:
        with pytest.raises(OSError):
            asyncio.run(sync_entity(Interrupted(OSError("synthetic"),count=120,stop=110,empty_tail=True),s,entity,150))
        assert s.checkpoint(111)==101
        assert asyncio.run(sync_entity(Interrupted(None,count=120,empty_tail=True),s,entity,150))==0
        assert s.checkpoint(111)==120
    finally:s.engine.dispose()
def test_source_resolution_retries_only_missing_source():
    known={};calls=[]
    class Client:
        async def get_entity(self,username):
            calls.append(username)
            if username=="second" and calls.count("second")==1:raise ConnectionError("synthetic")
            return SimpleNamespace(id=1 if username=="first" else 2)
    client=Client()
    assert set(asyncio.run(resolve_sources(client,["first","second"],known)))=={1}
    assert set(asyncio.run(resolve_sources(client,["first","second"],known)))=={1,2}
    assert calls==["first","second","second"]
