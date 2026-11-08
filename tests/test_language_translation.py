import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import create_engine, inspect, select
from telegram_monitor.cloud.db import Store, budgets, messages, requests
from telegram_monitor.cloud.language_translation import (
    MAX_CHARS, TranslationCache, TranslationService, cache_key, detect_language, translations,
)

@pytest.fixture
def setup():
    ledger = Store("sqlite://")
    cache = TranslationCache(ledger.engine)
    cache.create_schema()
    backend = SimpleNamespace(call=AsyncMock(return_value=({"translated_text": "Доброе утро."}, 20, 10)))
    cfg = SimpleNamespace(provider="openai", model="gpt-6-luna", monthly_budget_usd=5, max_output_tokens=500)
    service = TranslationService(cache, ledger, backend, cfg, enabled=True, detector=lambda text: "en")
    return service, cache, ledger, backend, cfg

@pytest.mark.asyncio
async def test_default_disabled_does_not_touch_detector_cache_or_backend(setup):
    service, cache, ledger, backend, cfg = setup
    def forbidden(text):
        raise AssertionError("Disabled detector called")
    service = TranslationService(None, None, backend, cfg, detector=forbidden)
    result = await service.translate("Good morning.")
    assert result.status == "disabled" and result.original_text == "Good morning."
    backend.call.assert_not_called()

@pytest.mark.asyncio
async def test_dry_run_does_not_translate(setup):
    service, cache, ledger, backend, cfg = setup
    cfg.provider = "dry_run"
    assert (await service.translate("Good morning.")).status == "disabled"
    backend.call.assert_not_called()

@pytest.mark.asyncio
async def test_translation_cache_and_real_ledger_accounting(setup):
    service, cache, ledger, backend, cfg = setup
    text = " Good morning.\n"
    first = await service.translate(text)
    second = await service.translate(text)
    assert first.original_text == second.original_text == text
    assert first.translated_text == second.translated_text == "Доброе утро."
    assert not first.cache_hit and second.cache_hit
    assert backend.call.await_count == 1
    with ledger.engine.connect() as c:
        assert c.execute(select(messages)).first() is None
        rows = c.execute(select(requests)).mappings().all()
        assert len(rows) == 1 and rows[0]["state"] == "confirmed"
        assert rows[0]["charged_micro_usd"] == 7
        assert c.execute(select(budgets.c.used_micro_usd)).scalar_one() == 7
    args = backend.call.call_args.args
    assert json.loads(args[2])["source_text"] == text
    assert args[-1] == 500

@pytest.mark.asyncio
@pytest.mark.parametrize("language,status", [(None, "unknown"), ("fr", "unknown"), ("ru", "original")])
async def test_unknown_and_russian_do_not_spend(setup, language, status):
    service, cache, ledger, backend, cfg = setup
    service.detector = lambda text: language
    text = "Исходный текст\n"
    result = await service.translate(text)
    assert result.status == status and result.original_text == text
    assert result.translated_text == (text if language == "ru" else None)
    backend.call.assert_not_called()

@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["", "  ", "a" * (MAX_CHARS + 1), None, 123])
async def test_input_limits(setup, value):
    service, cache, ledger, backend, cfg = setup
    with pytest.raises(ValueError):
        await service.translate(value)
    backend.call.assert_not_called()

@pytest.mark.asyncio
async def test_budget_refusal_is_not_cached_and_can_retry_without_new_cap(setup):
    service, cache, ledger, backend, cfg = setup
    cfg.monthly_budget_usd = 0
    assert (await service.translate("Good morning.")).status == "budget_exhausted"
    backend.call.assert_not_called()
    with ledger.engine.connect() as c:
        assert c.execute(select(translations)).first() is None
    cfg.monthly_budget_usd = 5
    assert (await service.translate("Good morning.")).status == "translated"

@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{}, {"translated_text": ""}, {"translated_text": 1},
    {"translated_text": "a" * 12001}, {"translated_text": "x", "extra": True}])
async def test_malformed_response_is_failed_without_automatic_rebilling(setup, body):
    service, cache, ledger, backend, cfg = setup
    backend.call.return_value = body, 20, 10
    with pytest.raises(ValueError):
        await service.translate("Good morning.")
    assert (await service.translate("Good morning.")).status == "failed"
    assert backend.call.await_count == 1

@pytest.mark.asyncio
async def test_transport_failure_keeps_existing_reservation_and_blocks_rebilling(setup):
    service, cache, ledger, backend, cfg = setup
    backend.call.side_effect = TimeoutError("synthetic")
    with pytest.raises(TimeoutError):
        await service.translate("Good morning.")
    assert (await service.translate("Good morning.")).status == "failed"
    with ledger.engine.connect() as c:
        row = c.execute(select(requests)).mappings().one()
        assert row["state"] == "unknown"
        assert c.execute(select(budgets.c.used_micro_usd)).scalar_one() == row["reserved_micro_usd"]

@pytest.mark.asyncio
async def test_two_overlapping_calls_only_send_once(setup):
    service, cache, ledger, backend, cfg = setup
    started, resume = asyncio.Event(), asyncio.Event()
    async def delayed(*args):
        started.set()
        await resume.wait()
        return {"translated_text": "Доброе утро."}, 20, 10
    backend.call.side_effect = delayed
    task = asyncio.create_task(service.translate("Good morning."))
    await started.wait()
    assert (await service.translate("Good morning.")).status == "busy"
    resume.set()
    assert (await task).status == "translated"
    assert (await service.translate("Good morning.")).cache_hit
    assert backend.call.await_count == 1

@pytest.mark.asyncio
async def test_cancellation_keeps_reservation_and_failed_claim(setup):
    service, cache, ledger, backend, cfg = setup
    started = asyncio.Event()
    async def blocked(*args):
        started.set()
        await asyncio.Event().wait()
    backend.call.side_effect = blocked
    task = asyncio.create_task(service.translate("Good morning."))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await service.translate("Good morning.")).status == "failed"

def test_cache_separates_exact_original_language_model_and_version():
    original = cache_key("Hello world", "en", "gpt-6-luna")[0]
    assert len({original,
        cache_key("Hello world ", "en", "gpt-6-luna")[0],
        cache_key("Hello world", "hy", "gpt-6-luna")[0],
        cache_key("Hello world", "en", "gpt-6.1-sol")[0],
        cache_key("Hello world", "en", "gpt-6-luna", "v2")[0]}) == 5

def test_cache_constructor_does_not_create_schema():
    engine = create_engine("sqlite://")
    TranslationCache(engine)
    assert inspect(engine).get_table_names() == []

def test_wrong_claim_cannot_finish_or_release(setup):
    service, cache, ledger, backend, cfg = setup
    key, digest = cache_key("Good morning.", "en", cfg.model)
    state, claim = cache.lookup_or_claim(key, digest, "en", cfg.model)
    assert state == "claimed"
    with pytest.raises(RuntimeError):
        cache.finish(key, "wrong", "x")
    cache.release_unbilled(key, "wrong")
    assert cache.lookup_or_claim(key, digest, "en", cfg.model)[0] == "busy"
    cache.finish(key, claim, "Доброе утро.")
    assert cache.lookup_or_claim(key, digest, "en", cfg.model) == ("cached", "Доброе утро.")

def test_real_lingua_synthetic_corpus():
    pytest.importorskip("lingua")
    cases = json.loads(Path("tests/fixtures/language_feasibility_ru_am_en.json").read_text())
    assert all(detect_language(case["text"]) == case["expected"] for case in cases)
