"""Synthetic-only PostgreSQL contract; refuses nonempty/unexpected databases."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import importlib.metadata
import json
import os
import resource
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.engine import make_url
from telegram_monitor.cloud.db import Store, md, messages, requests
from telegram_monitor.cloud.language_translation import (
    TranslationCache, TranslationService, cache_key, detect_language,
)

async def check():
    url = make_url(os.environ["TM_TRANSLATION_STAGE_DATABASE_URL"])
    if (url.get_backend_name() != "postgresql" or url.database != "tm_translation_stage"
            or url.host not in {"127.0.0.1", "localhost", "postgres"} or url.username != "stage"):
        raise RuntimeError("Only the dedicated synthetic translation fixture is allowed")
    engine = create_engine(url, pool_pre_ping=True)
    if inspect(engine).get_table_names():
        raise RuntimeError("Translation stage fixture must start empty")
    md.create_all(engine)
    ledger = Store.__new__(Store)
    ledger.engine, ledger.read_only = engine, False
    cache = TranslationCache(engine)
    cache.create_schema()
    key, digest = cache_key("Concurrent synthetic text", "en", "gpt-6-luna")
    def claim(_):
        return TranslationCache(engine).lookup_or_claim(key, digest, "en", "gpt-6-luna")
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(claim, range(4)))
    owners = [value for state, value in claims if state == "claimed"]
    assert len(owners) == 1 and sum(state == "busy" for state, value in claims) == 3
    cache.finish(key, owners[0], "Синтетический текст")
    assert claim(0) == ("cached", "Синтетический текст")
    cfg = SimpleNamespace(provider="openai", model="gpt-6-luna", monthly_budget_usd=5,
                          max_output_tokens=500)
    async def synthetic_backend(model, instructions, prompt, schema, maximum):
        body = json.loads(prompt)
        assert body["target_language"] == "ru" and maximum == 500
        return {"translated_text": "Синтетический перевод"}, 20, 10
    backend = SimpleNamespace(call=AsyncMock(side_effect=synthetic_backend))
    service = TranslationService(cache, ledger, backend, cfg, enabled=True)
    inputs = [("The city published a new public transport schedule today.", "en"),
              ("Այսօր Երևանում բացվել է նոր ցուցահանդես։", "hy")]
    for text, language in inputs:
        assert detect_language(text) == language
        result = await service.translate(text)
        assert result.original_text == text and result.status == "translated"
        again = await service.translate(text)
        assert again.cache_hit and again.original_text == text
    assert backend.call.await_count == 2
    assert (await service.translate("Доброе утро, как ваши дела?")).status == "original"
    assert (await service.translate("Привет աշխարհ hello")).status == "unknown"
    cfg.monthly_budget_usd = 0
    assert (await service.translate("Another public transport schedule was published.")).status == "budget_exhausted"
    assert backend.call.await_count == 2
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(messages)).scalar_one() == 0
        assert conn.execute(select(func.count()).select_from(requests)).scalar_one() == 2
    engine.dispose()
    return {"ok": True, "fixture": "dedicated-empty-PostgreSQL", "original_preserved": True,
            "concurrent_claims": 4, "concurrent_winners": len(owners),
            "synthetic_backend_calls": 2, "cache_hits": 2, "budget_refusal": True,
            "live_provider_calls": 0, "production_database_used": False,
            "lingua_version": importlib.metadata.version("lingua-language-detector"),
            "max_rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)}

if __name__ == "__main__":
    try:
        result = asyncio.run(check())
    except Exception as exc:
        print("TRANSLATION_STAGE_FAILED=" + type(exc).__name__)
        raise SystemExit(1)
    print("TRANSLATION_STAGE=" + json.dumps(result, sort_keys=True))
