"""Untrusted message IDs cannot inflate generated AI prompts."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from telegram_monitor.analyzer import MessageAnalyzer


def test_message_id_is_bounded_in_prompt(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    msg = SimpleNamespace(channel_name="source", text="hello",
        timestamp=datetime.now(timezone.utc), message_id="9" * 10000 + "INJECT",
        media_type=None, media_size_kb=0)
    prompts = []
    async def capture(prompt):
        prompts.append(prompt)
        return None
    with patch.object(MessageAnalyzer, "_call_claude", new=AsyncMock(side_effect=capture)):
        assert asyncio.run(MessageAnalyzer(["topic"])._analyze_one_chunk([msg])) is None
    assert '"message_id": "' + "9" * 64 + '"' in prompts[0]
    assert "INJECT" not in prompts[0]
