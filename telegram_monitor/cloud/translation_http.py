"""Internal opt-in translation endpoint; existing dashboard verifies access."""
import asyncio
from contextlib import contextmanager
from dataclasses import asdict
import os
import urllib.error
import urllib.request
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, func
from .db import messages
from .language_translation import TranslationCache, TranslationService, MAX_CHARS

class MessageTranslation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=3, max_length=100, pattern=r"^[0-9]+:[0-9]+$")

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def internal_request(url, authorization, *, payload=None, timeout=3):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    headers = {"Authorization": authorization, "Accept": "application/json"}
    if payload is not None:
        headers.update({"Content-Type": "application/json", "X-TM-Internal": "translation-v1"})
    request = urllib.request.Request(url, data=payload, headers=headers)
    with opener.open(request, timeout=timeout) as reply:
        body = reply.read(65537)
        if len(body) > 65536:
            raise ValueError("Internal response too large")
        return body

async def verify_dashboard(authorization):
    if not authorization.startswith("Basic ") or len(authorization) > 4096:
        raise HTTPException(401, detail="Unauthorized")
    try:
        body = await asyncio.to_thread(internal_request,
            "http://tm-dashboard-v861-live-readonly:8098/api/text/translation-authorize",
            authorization)
        import json
        if json.loads(body) != {"authorized": True}:
            raise ValueError("Invalid authorization reply")
    except urllib.error.HTTPError as exc:
        raise HTTPException(401 if exc.code == 401 else 503, detail="Authorization unavailable") from None
    except (OSError, ValueError):
        raise HTTPException(503, detail="Authorization unavailable") from None

def create_translation_app(store, backend, cfg, *, enabled=False, verifier=verify_dashboard, detector=None):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    lock = asyncio.Lock()
    kwargs = {} if detector is None else {"detector": detector}
    service = TranslationService(TranslationCache(store.engine), store, backend, cfg,
                                 enabled=enabled, **kwargs)

    @app.middleware("http")
    async def privacy(request, call_next):
        if request.method == "POST":
            if len(await request.body()) > 1024:
                return JSONResponse({"detail": "Request too large"}, status_code=413,
                                    headers={"Cache-Control": "no-store, private"})
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store, private"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/health")
    def health():
        return {"service": "up", "translation_enabled": enabled}

    @app.post("/internal/translation")
    async def translate(payload: MessageTranslation, request: Request):
        if not enabled or cfg.provider != "openai":
            raise HTTPException(503, detail="Translation disabled")
        if (request.headers.get("x-tm-internal") != "translation-v1"
                or request.headers.get("origin") or
                request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json"):
            raise HTTPException(403, detail="Internal JSON request required")
        if lock.locked():
            raise HTTPException(429, detail="Translation busy")
        async with lock:
            await verifier(request.headers.get("authorization", ""))
            try:
                with store.engine.connect() as conn:
                    row = conn.execute(select(
                        func.substr(messages.c.text, 1, MAX_CHARS + 1),
                        func.length(messages.c.text)).where(messages.c.key == payload.key)).first()
                if row is None:
                    raise HTTPException(404, detail="Message not found")
                if row[1] > MAX_CHARS:
                    raise HTTPException(422, detail="Message exceeds translation limit")
                result = await service.translate(row[0])
            except HTTPException:
                raise
            except Exception:
                raise HTTPException(503, detail="Translation temporarily unavailable") from None
        return {"key": payload.key, **asdict(result)}

    return app

async def serve_translation(store, backend, cfg):
    import uvicorn
    class InternalServer(uvicorn.Server):
        # Preserve the worker process's original signal behavior.
        @contextmanager
        def capture_signals(self):
            yield
        def install_signal_handlers(self):
            pass
    application = create_translation_app(store, backend, cfg, enabled=True)
    server = InternalServer(uvicorn.Config(application, host="0.0.0.0", port=8097,
                                          access_log=False, log_level="warning"))
    try:
        await server.serve()
    except (Exception, SystemExit):
        # No credentials, source text or provider error bodies in logs.
        import logging
        logging.getLogger("cloud.worker").error("Translation HTTP stopped; classification continues")
