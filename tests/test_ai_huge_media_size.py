"""Huge numeric media sizes must not abort AI prompt construction."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from telegram_monitor.analyzer import MessageAnalyzer


def test_huge_numeric_media_size_falls_back_to_zero(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    msg = SimpleNamespace(channel_name="source", text="hello",
        timestamp=datetime.now(timezone.utc), message_id=1,
        media_type="photo", media_size_kb="9" * 10000)
    prompts = []
    async def capture(prompt):
        prompts.append(prompt)
        return None
    with patch.object(MessageAnalyzer, "_call_claude", new=AsyncMock(side_effect=capture)):
        assert asyncio.run(MessageAnalyzer(["topic"])._analyze_one_chunk([msg])) is None
    assert len(prompts) == 1
    assert '"media_size_kb": 0' in prompts[0]
