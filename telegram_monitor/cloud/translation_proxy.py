"""Same-origin manual translation; dashboard keeps its existing read-only DB role."""
import asyncio
import json
import os
import urllib.error
from fastapi import APIRouter, HTTPException, Request
from .translation_http import MessageTranslation, internal_request
from .sherlock_proxy import _same_origin_json

router = APIRouter()

def enabled():
    return os.getenv("CLOUD_TRANSLATION_ENABLED", "").lower() in {"1", "true", "yes", "on"}

@router.get("/api/text/translation-authorize")
def authorize_translation():
    # api.py includes this entire router under its existing HTTP Basic dependency.
    return {"authorized": True}

@router.get("/api/text/translation-capabilities")
def capabilities():
    return {"enabled": enabled(), "max_chars": 3000, "target_language": "ru", "manual_only": True}

@router.post("/api/text/translate")
async def translate(payload: MessageTranslation, request: Request):
    _same_origin_json(request)
    if not enabled():
        raise HTTPException(503, detail="Translation disabled")
    try:
        body = await asyncio.to_thread(internal_request,
            "http://tm-v861-live-worker:8097/internal/translation",
            request.headers.get("authorization", ""),
            payload=json.dumps({"key": payload.key}).encode("utf-8"), timeout=105)
        result = json.loads(body)
        if (not isinstance(result, dict) or set(result) != {"key", "original_text", "translated_text", "source_language", "target_language", "status", "cache_hit", "version"} or result.get("key") != payload.key
                or result.get("status") not in {"translated", "original", "unknown", "busy", "failed", "budget_exhausted", "disabled"}
                or not isinstance(result.get("original_text"), str)
                or len(result["original_text"]) > 3000
                or result.get("status") in {"translated", "original"} and not isinstance(result.get("translated_text"), str)
                or not isinstance(result.get("version"), str)
                or result.get("source_language") not in {"ru", "hy", "en", None}
                or not isinstance(result.get("cache_hit"), bool)
                or result.get("target_language") != "ru"
                or not (result.get("translated_text") is None or
                        isinstance(result["translated_text"], str) and len(result["translated_text"]) <= 12000)):
            raise ValueError("Invalid translation reply")
        return result
    except urllib.error.HTTPError as exc:
        code = exc.code if exc.code in {401, 403, 404, 413, 422, 429} else 503
        raise HTTPException(code, detail="Translation unavailable") from None
    except (OSError, ValueError):
        raise HTTPException(503, detail="Translation temporarily unavailable") from None
