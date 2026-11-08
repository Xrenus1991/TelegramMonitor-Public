"""Source label sanitization should be bounded before character iteration."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from telegram_monitor.analyzer import MessageAnalyzer


def test_huge_source_name_keeps_bounded_prefix(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    message = SimpleNamespace(channel_name="A" * 100000 + "INJECT", text="ok",
        timestamp=datetime.now(timezone.utc), message_id=1, media_type=None, media_size_kb=0)
    prompts = []
    async def capture(prompt):
        prompts.append(prompt)
        return None
    with patch.object(MessageAnalyzer, "_call_claude", new=AsyncMock(side_effect=capture)):
        assert asyncio.run(MessageAnalyzer(["topic"])._analyze_one_chunk([message])) is None
    assert '"source": "' + "A" * 200 + '"' in prompts[0]
    assert "INJECT" not in prompts[0]
