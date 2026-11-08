"""Bound untrusted media labels before scanning characters."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from telegram_monitor.analyzer import MessageAnalyzer


def test_huge_media_label_keeps_bounded_prefix(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    message = SimpleNamespace(channel_name="source", text="ok",
        timestamp=datetime.now(timezone.utc), message_id=1,
        media_type="X" * 100000 + "INJECT", media_size_kb=0)
    prompts = []
    async def capture(prompt):
        prompts.append(prompt)
        return None
    with patch.object(MessageAnalyzer, "_call_claude", new=AsyncMock(side_effect=capture)):
        assert asyncio.run(MessageAnalyzer(["topic"])._analyze_one_chunk([message])) is None
    assert '"media_type": "' + "X" * 80 + '"' in prompts[0]
    assert "INJECT" not in prompts[0]
