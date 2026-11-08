"""Exercise the actual collector run-loop with a synthetic client and no network."""
import asyncio
from datetime import datetime,timezone
from types import SimpleNamespace
import pytest
from telegram_monitor.cloud import collector
from telegram_monitor.cloud.db import Store
class StopLoop(BaseException):pass
@pytest.mark.parametrize("kind,seconds,expected",[
    ("flood",7,[7,90,90]),("flood",0,[1,90,90]),("disconnect",0,[90,90])])
def test_outer_loop_cooldown_resume_and_disconnect(tmp_path,monkeypatch,kind,seconds,expected):
    import importlib.metadata
    try:importlib.metadata.version("Telethon")
    except importlib.metadata.PackageNotFoundError:pytest.skip("actual Telethon required")
    import telethon
    from telethon.errors import FloodWaitError
    from telegram_monitor.cloud.settings import Settings
    session=tmp_path/"synthetic-marker"
    session.with_suffix(".session").write_bytes(b"synthetic test marker; never opened by a Telegram client")
    url="sqlite:///"+str(tmp_path/"loop.db")
    store=Store(url)
    cfg=SimpleNamespace(telegram_api_id=1,telegram_api_hash="synthetic",
        telegram_session_path=str(session),database_url=url,channels=("fixture",),
        initial_history=100,sync_seconds=90)
    monkeypatch.setattr(Settings,"from_env",classmethod(lambda cls:cfg))
    for name in ("CLOUD_DISCOVERY_ENABLED","CLOUD_SOURCE_SELECTION_STRICT","CLOUD_SOURCE_RECENT_ENABLED","CLOUD_COMMENTS_ENABLED"):
        monkeypatch.setenv(name,"false")
    monkeypatch.setattr(collector,"Store",lambda unused:store)
    calls={"connect":0,"disconnect":0,"iterations":0}
    class Client:
        async def connect(self):calls["connect"]+=1
        async def disconnect(self):calls["disconnect"]+=1
        async def is_user_authorized(self):return True
        async def get_entity(self,username):return SimpleNamespace(id=111,title="fixture")
        def on(self,event):return lambda handler:handler
        async def iter_messages(self,entity,**kwargs):
            calls["iterations"]+=1
            if calls["iterations"]==1:
                if kind=="flood":raise FloodWaitError(request=None,capture=seconds)
                raise ConnectionError("synthetic disconnect")
            yield SimpleNamespace(id=1,message="synthetic message",date=datetime.now(timezone.utc),media=None)
    monkeypatch.setattr(telethon,"TelegramClient",lambda *args,**kwargs:Client())
    waits=[]
    async def fake_sleep(delay):
        waits.append(delay)
        if waits.count(90)==2:raise StopLoop()
    monkeypatch.setattr(collector.asyncio,"sleep",fake_sleep)
    try:
        with pytest.raises(StopLoop):asyncio.run(collector.run())
        assert waits==expected
        assert calls=={"connect":1,"disconnect":1,"iterations":2}
        assert store.checkpoint(111)==1
        assert store.stats()["messages"]=={"pending":1}
    finally:store.engine.dispose()
