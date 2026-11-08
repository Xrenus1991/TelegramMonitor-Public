"""Additional regression tests for the repeated, offline v4 audit."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from telegram_monitor.analyzer import MessageAnalyzer
from telegram_monitor.client import TelegramMessage
from telegram_monitor.pending_store import PendingMessageStore


def _message(message_id):
    return TelegramMessage(
        channel_name="Public synthetic", channel_id=81,
        text=f"Synthetic {message_id}",
        timestamp=datetime(2026, 10, 31, tzinfo=timezone.utc),
        message_id=message_id,
        has_media=True, media_type="photo", media_caption="Caption",
    )


def test_many_captions_are_split_in_order(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("AI_CAPTION_BATCH_SIZE", "15")
    calls = []

    async def translated(captions, system):
        calls.append(list(captions))
        return [f"RU:{text}" for text in captions]

    monkeypatch.setattr("telegram_monitor.analyzer.captions_openai", translated)
    ai = MessageAnalyzer([])
    original = [f"Text-{n}" for n in range(37)]
    output = asyncio.run(ai.translate_captions(original))
    assert output == [f"RU:{x}" for x in original]
    assert list(map(len, calls)) == [15, 15, 7]
    assert ai._openai_calls == 3


def test_caption_budget_exhaustion_does_not_drop_remaining(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("AI_CAPTION_BATCH_SIZE", "2")
    monkeypatch.setenv("OPENAI_MAX_REQUESTS_PER_RUN", "1")
    calls = []

    async def translated(captions, system):
        calls.append(list(captions))
        return ["перевод:" + text for text in captions]

    monkeypatch.setattr("telegram_monitor.analyzer.captions_openai", translated)
    ai = MessageAnalyzer([])
    data = [f"text {n}" for n in range(5)]
    assert asyncio.run(ai.translate_captions(data)) == ["перевод:text 0", "перевод:text 1", "text 2", "text 3", "text 4"]
    assert len(calls) == 1
    assert ai._openai_calls == 1


def test_invalid_caption_limit_uses_small_safe_default(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("AI_CAPTION_BATCH_SIZE", "nonsense")
    calls = []

    async def translated(captions, system):
        calls.append(len(captions))
        return list(captions)

    monkeypatch.setattr("telegram_monitor.analyzer.captions_openai", translated)
    assert asyncio.run(MessageAnalyzer([]).translate_captions(["x"] * 17)) == ["x"] * 17
    assert calls == [15, 2]


def test_history_is_persisted_before_caption_translation(tmp_path, monkeypatch):
    from tests.test_tui_scheduling import load_tui, fake_app
    mod = load_tui(monkeypatch)
    monkeypatch.setenv("PERSIST_ANALYSIS_QUEUE", "1")
    monkeypatch.setenv("ANALYSIS_QUEUE_DB", str(tmp_path / "pending.sqlite3"))

    async def scenario():
        class Analyzer:
            async def analyze_batch(self, messages):
                return SimpleNamespace(raw_text="ok", urgency="NORMAL")
        app, _ = fake_app(mod, Analyzer())
        msg = _message(6)

        async def fetch(per_channel):
            return [msg]

        entered_translate = asyncio.Event()
        stall = asyncio.Event()
        async def slow_translate(messages):
            entered_translate.set()
            await stall.wait()
            return {}

        app.telegram_monitor.fetch_history = fetch
        app._translate_media_captions = slow_translate
        task = app.load_history()
        await asyncio.wait_for(entered_translate.wait(), timeout=2)
        # Translation has not finished; the history must already be durable.
        assert [x.message_id for x in PendingMessageStore(tmp_path / "pending.sqlite3").load()] == [6]
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    asyncio.run(scenario())


def test_failed_queue_ack_is_retried_without_model_call(tmp_path, monkeypatch):
    from tests.test_tui_scheduling import load_tui, fake_app
    mod = load_tui(monkeypatch)
    monkeypatch.setenv("PERSIST_ANALYSIS_QUEUE", "1")
    monkeypatch.setenv("ANALYSIS_QUEUE_DB", str(tmp_path / "pending.sqlite3"))

    async def scenario():
        calls = []
        class Analyzer:
            async def analyze_batch(self, messages):
                calls.append(len(messages))
                return SimpleNamespace(raw_text="ok", urgency="NORMAL")

        app, _ = fake_app(mod, Analyzer())
        msg = _message(7)
        app._enqueue_messages([msg])
        original_ack = app._pending_store.ack
        failures = [True]

        def flaky(messages):
            if failures[0]:
                failures[0] = False
                raise OSError("temporary simulated SQLite I/O failure")
            return original_ack(messages)

        app._pending_store.ack = flaky
        assert app._start_analysis([msg])
        del app._pending_messages[:]
        await asyncio.sleep(0.02)
        assert len(app._ack_waiting) == 1
        assert calls == [1]
        assert [x.message_id for x in app._pending_store.load()] == [7]
        app._retry_acknowledgements()
        assert app._ack_waiting == []
        assert app._pending_store.load() == []
        assert calls == [1]  # no second paid analysis
    asyncio.run(scenario())
