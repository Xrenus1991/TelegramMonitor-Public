"""Regression checks from independent v6 review (synthetic/offline)."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from telegram_monitor.analyzer import AnalysisResult, MessageAnalyzer
from telegram_monitor.cli import queue_initial_history
from telegram_monitor.client import TelegramMessage
from telegram_monitor.pending_store import PendingMessageStore


def msg(i, text=None):
    return TelegramMessage("Synthetic channel", 900, text or f"Synthetic {i}",
                           datetime(2026, 10, 31, tzinfo=timezone.utc), i)


def result(n):
    return AnalysisResult(timestamp=datetime.now(timezone.utc), raw_text="ok", summary=f"Summary {n}", msg_count=n)


def test_chunks_reused_after_transient_failure(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("AI_MAX_MESSAGES_PER_CHUNK", "2")
    ai = MessageAnalyzer(["synthetic"])
    calls = []
    fail_second = [True]

    async def analysis(messages):
        ids = tuple(m.message_id for m in messages)
        calls.append(ids)
        if ids == (2, 3) and fail_second[0]:
            fail_second[0] = False
            return None
        return result(len(messages))

    ai._analyze_one_chunk = analysis
    source = [msg(i) for i in range(5)]
    assert asyncio.run(ai.analyze_batch(source)) is None
    merged = asyncio.run(ai.analyze_batch(source))
    assert merged and merged.msg_count == 5
    assert calls == [(0, 1), (2, 3), (2, 3), (4,)]
    assert ai.total_analyzed == 5
    assert ai._partial_parts == []


def test_changed_message_invalidates_completed_chunk(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("AI_MAX_MESSAGES_PER_CHUNK", "1")
    ai = MessageAnalyzer(["synthetic"])
    calls = []

    async def analysis(messages):
        calls.append(messages[0].text)
        if messages[0].message_id == 2:
            return None
        return result(1)

    ai._analyze_one_chunk = analysis
    source = [msg(1), msg(2)]
    assert asyncio.run(ai.analyze_batch(source)) is None
    source[0].text = "modified body"
    assert asyncio.run(ai.analyze_batch(source)) is None
    assert calls == ["Synthetic 1", "Synthetic 2", "modified body", "Synthetic 2"]


def test_initial_history_log_only_is_limited(monkeypatch):
    monkeypatch.delenv("ANALYZE_ALL_HISTORY", raising=False)
    class Monitor:
        async def fetch_history(self, per_channel):
            assert per_channel >= 1
            return [msg(i) for i in range(50)]
    queue = []
    added = asyncio.run(queue_initial_history(Monitor(), queue))
    assert added == len(queue) == 30
    assert [m.message_id for m in queue] == list(range(30))


def test_initial_history_log_mode_does_not_repeat_completed(tmp_path, monkeypatch):
    monkeypatch.setenv("ANALYZE_ALL_HISTORY", "1")
    store = PendingMessageStore(tmp_path / "pending.sqlite3")
    store.add(msg(0))
    store.ack([msg(0)])

    class Monitor:
        async def fetch_history(self, per_channel):
            return [msg(0), msg(1), msg(2)]

    queue = store.load()
    assert asyncio.run(queue_initial_history(Monitor(), queue, store)) == 2
    assert [m.message_id for m in queue] == [1, 2]
    assert [m.message_id for m in store.load()] == [1, 2]


def test_initial_history_failure_is_nonfatal(caplog):
    class Monitor:
        async def fetch_history(self, per_channel):
            raise RuntimeError("simulated Telegram outage")
    queue = []
    assert asyncio.run(queue_initial_history(Monitor(), queue)) == 0
    assert queue == []
    assert "simulated Telegram outage" not in caplog.text


def test_reject_ollama_incomplete_even_if_text_present(monkeypatch):
    import io
    import json
    from telegram_monitor.analyzer import MessageAnalyzer

    class FakeResponse(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): self.close()

    def urlopen(request, timeout):
        return FakeResponse(json.dumps({"done": False, "message": {"content": "## EXECUTIVE SUMMARY\\nPartially ready"}}).encode())

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    assert MessageAnalyzer._ollama_request("example prompt", "test") is None


def test_cancellation_keeps_completed_chunks_for_retry(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("AI_MAX_MESSAGES_PER_CHUNK", "1")
    async def scenario():
        ai = MessageAnalyzer(["synthetic"])
        pending = asyncio.Event()
        started = asyncio.Event()
        calls = []

        async def analysis(messages):
            i = messages[0].message_id
            calls.append(i)
            if i == 1 and not pending.is_set():
                started.set()
                await pending.wait()
            return result(1)

        ai._analyze_one_chunk = analysis
        batch = [msg(0), msg(1)]
        task = asyncio.create_task(ai.analyze_batch(batch))
        await started.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert len(ai._partial_parts) == 1
        pending.set()
        merged = await ai.analyze_batch(batch)
        assert merged and merged.msg_count == 2
        assert calls == [0, 1, 1]
    asyncio.run(scenario())


def test_urgency_reason_does_not_escalate_report():
    ai = MessageAnalyzer(["public events"])
    raw = "## URGENCY ASSESSMENT\nNORMAL\nЭто НЕ CRITICAL, это обычное событие.\n\n## EXECUTIVE SUMMARY\nОбъявление"
    parsed = ai._parse_response(raw, [msg(6)], {"Synthetic channel": 1})
    assert parsed.urgency == "NORMAL"
    assert "НЕ CRITICAL" in parsed.urgency_reason


def test_urgency_declaration_can_include_label():
    ai = MessageAnalyzer(["public events"])
    raw = "## URGENCY ASSESSMENT\nУровень: HIGH\nЕсть подтверждённый официальный источник.\n\n## EXECUTIVE SUMMARY\nОбъявление"
    parsed = ai._parse_response(raw, [msg(6)], {"Synthetic channel": 1})
    assert parsed.urgency == "HIGH"
    assert "официальный" in parsed.urgency_reason
