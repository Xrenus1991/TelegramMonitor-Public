"""Malformed media size metadata must not abort an AI chunk."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import pytest
from telegram_monitor.analyzer import MessageAnalyzer


@pytest.mark.parametrize("size", ["unknown", "12.5", -1, None])
def test_invalid_media_size_does_not_abort_chunk(monkeypatch, size):
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    msg = SimpleNamespace(channel_name="source", text="hello",
        timestamp=datetime.now(timezone.utc), message_id=1,
        media_type="photo", media_size_kb=size)
    prompts = []
    async def capture(prompt):
        prompts.append(prompt)
        return None
    with patch.object(MessageAnalyzer, "_call_claude", new=AsyncMock(side_effect=capture)):
        assert asyncio.run(MessageAnalyzer(["topic"])._analyze_one_chunk([msg])) is None
    assert len(prompts) == 1
    assert '"media_size_kb": 0' in prompts[0]
