import asyncio
from datetime import datetime, timezone
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import urllib.error
import pytest
import httpx
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import insert
from telegram_monitor.cloud.db import Store, messages
from telegram_monitor.cloud.language_translation import TranslationCache
from telegram_monitor.cloud.translation_http import create_translation_app
from telegram_monitor.cloud import translation_proxy

@pytest.fixture
def context():
    from sqlalchemy.pool import StaticPool
    from sqlalchemy import create_engine
    store = Store("sqlite://")
    store.engine.dispose()
    from telegram_monitor.cloud.db import md
    store.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    md.create_all(store.engine)
    TranslationCache(store.engine).create_schema()
    with store.engine.begin() as conn:
        conn.execute(insert(messages).values(key="1:1", channel_id=1, message_id=1,
            channel_name="Synthetic", timestamp=datetime.now(timezone.utc), text="Good morning, how are you?",
            created_at=datetime.now(timezone.utc)))
    cfg = SimpleNamespace(provider="openai", model="gpt-6-luna", monthly_budget_usd=5, max_output_tokens=500)
    backend = SimpleNamespace(call=AsyncMock(return_value=({"translated_text":"Доброе утро."},20,10)))
    verify = AsyncMock()
    app = create_translation_app(store, backend, cfg, enabled=True, verifier=verify, detector=lambda t:"en")
    return store, backend, cfg, verify, app

def headers(**kwargs):
    return {"Authorization":"Basic c3ludGhldGljOnRlc3Q=", "X-TM-Internal":"translation-v1", **kwargs}

def test_worker_exact_original_and_cache(context):
    store, backend, cfg, verify, app = context
    with TestClient(app) as c:
        first=c.post("/internal/translation",json={"key":"1:1"},headers=headers())
        again=c.post("/internal/translation",json={"key":"1:1"},headers=headers())
    assert first.status_code==again.status_code==200
    assert first.json()["original_text"]=="Good morning, how are you?"
    assert again.json()["cache_hit"] and backend.call.await_count==1
    assert verify.await_count==2 and verify.call_args.args==("Basic c3ludGhldGljOnRlc3Q=",)
    assert "no-store" in first.headers["Cache-Control"]

@pytest.mark.parametrize("extra", [{"Origin":"http://example.invalid"}, {"X-TM-Internal":"bad"}])
def test_worker_rejects_browser_origins(context,extra):
    store, backend, cfg, verify, app=context
    with TestClient(app) as c:
        response=c.post("/internal/translation",json={"key":"1:1"},headers=headers(**extra))
    assert response.status_code==403
    backend.call.assert_not_called()

def test_worker_auth_denial_hides_message_existence(context):
    store, backend, cfg, verify, app=context
    verify.side_effect=HTTPException(401,detail="Unauthorized")
    with TestClient(app) as c:
        r=c.post("/internal/translation",json={"key":"1:999"},headers=headers())
    assert r.status_code==401
    backend.call.assert_not_called()

def test_worker_disabled_and_validation(context):
    store, backend, cfg, verify, app=context
    app=create_translation_app(store,backend,cfg,enabled=False,verifier=verify)
    with TestClient(app) as c:
        assert c.post("/internal/translation",json={"key":"1:1"},headers=headers()).status_code==503
        assert c.post("/internal/translation",json={"key":"1:1","text":"override"},headers=headers()).status_code==422
    verify.assert_not_called()

def test_worker_missing_large_and_body_limit(context):
    store, backend, cfg, verify, app=context
    with store.engine.begin() as conn:
        conn.execute(messages.update().where(messages.c.key=="1:1").values(text="x"*3001))
    with TestClient(app) as c:
        assert c.post("/internal/translation",json={"key":"1:9"},headers=headers()).status_code==404
        assert c.post("/internal/translation",json={"key":"1:1"},headers=headers()).status_code==422
        assert c.post("/internal/translation",json={"key":"x"*2000},headers=headers()).status_code==413
    backend.call.assert_not_called()

@pytest.mark.asyncio
async def test_worker_global_concurrency(context):
    store, backend, cfg, verify, app=context
    started,resume=asyncio.Event(),asyncio.Event()
    async def delayed(*args):
        started.set()
        await resume.wait()
        return {"translated_text":"Доброе утро."},20,10
    backend.call.side_effect=delayed
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as c:
        first=asyncio.create_task(c.post("/internal/translation",json={"key":"1:1"},headers=headers()))
        await started.wait()
        second=await c.post("/internal/translation",json={"key":"1:1"},headers=headers())
        assert second.status_code==429
        resume.set()
        assert (await first).status_code==200
    assert backend.call.await_count==1

@pytest.fixture
def dashboard(monkeypatch):
    monkeypatch.setenv("DASHBOARD_USER","stage")
    monkeypatch.setenv("DASHBOARD_PASSWORD","synthetic-password-at-least16")
    monkeypatch.setenv("CLOUD_TRANSLATION_ENABLED","1")
    from telegram_monitor.cloud.api import app
    return TestClient(app)

def public_headers():
    return {"Origin":"http://testserver","X-TM-Action":"1"}

def test_dashboard_auth_and_origin(dashboard,monkeypatch):
    mock=AsyncMock()
    monkeypatch.setattr(translation_proxy.asyncio,"to_thread",mock)
    assert dashboard.get("/api/text/translation-authorize").status_code==401
    assert dashboard.post("/api/text/translate",json={"key":"1:1"}).status_code==401
    with dashboard as c:
        c.auth=("stage","synthetic-password-at-least16")
        assert c.get("/api/text/translation-authorize").json()=={"authorized":True}
        assert c.get("/api/text/translation-capabilities").json()["enabled"]
        assert c.post("/api/text/translate",json={"key":"1:1"}).status_code==403
        assert c.post("/api/text/translate",json={"key":"bad"},headers=public_headers()).status_code==422
    mock.assert_not_called()

def test_dashboard_proxies_only_key_and_existing_authorization(dashboard,monkeypatch):
    result={"key":"1:1","original_text":"original","translated_text":"перевод","version":"v1",
            "source_language":"en","target_language":"ru","status":"translated","cache_hit":False}
    mock=AsyncMock(return_value=json.dumps(result).encode())
    monkeypatch.setattr(translation_proxy.asyncio,"to_thread",mock)
    with dashboard as c:
        c.auth=("stage","synthetic-password-at-least16")
        r=c.post("/api/text/translate",json={"key":"1:1"},headers=public_headers())
    assert r.status_code==200 and r.json()==result
    args=mock.call_args
    assert args.args[1]=="http://tm-v861-live-worker:8097/internal/translation"
    assert args.args[2].startswith("Basic ")
    assert json.loads(args.kwargs["payload"])=={"key":"1:1"}

@pytest.mark.parametrize("code", [401,403,404,422,429,500])
def test_dashboard_preserves_safe_error_status(dashboard,monkeypatch,code):
    mock=AsyncMock(side_effect=urllib.error.HTTPError("internal",code,"private",{},None))
    monkeypatch.setattr(translation_proxy.asyncio,"to_thread",mock)
    with dashboard as c:
        c.auth=("stage","synthetic-password-at-least16")
        r=c.post("/api/text/translate",json={"key":"1:1"},headers=public_headers())
    assert r.status_code==(code if code<500 else 503) and "private" not in r.text

def test_dashboard_disabled_and_wrong_result_key(dashboard,monkeypatch):
    mock=AsyncMock(return_value=b'{"key":"2:2"}')
    monkeypatch.setattr(translation_proxy.asyncio,"to_thread",mock)
    with dashboard as c:
        c.auth=("stage","synthetic-password-at-least16")
        assert c.post("/api/text/translate",json={"key":"1:1"},headers=public_headers()).status_code==503
        monkeypatch.setenv("CLOUD_TRANSLATION_ENABLED","0")
        assert not c.get("/api/text/translation-capabilities").json()["enabled"]
        assert c.post("/api/text/translate",json={"key":"1:1"},headers=public_headers()).status_code==503
    assert mock.await_count==1

@pytest.mark.asyncio
async def test_worker_delegates_only_to_fixed_dashboard_and_fails_closed(monkeypatch):
    from telegram_monitor.cloud import translation_http
    mock=AsyncMock(return_value=b'{"authorized":true}')
    monkeypatch.setattr(translation_http.asyncio,"to_thread",mock)
    await translation_http.verify_dashboard("Basic synthetic")
    assert mock.call_args.args[1]=="http://tm-dashboard-v861-live-readonly:8098/api/text/translation-authorize"
    mock.return_value=b'{"authorized":false}'
    with pytest.raises(HTTPException) as error:
        await translation_http.verify_dashboard("Basic synthetic")
    assert error.value.status_code==503
    with pytest.raises(HTTPException) as error:
        await translation_http.verify_dashboard("Bearer other")
    assert error.value.status_code==401
