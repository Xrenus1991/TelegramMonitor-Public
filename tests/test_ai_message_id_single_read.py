"""Message ID is read once, preserving numeric type."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from telegram_monitor.analyzer import MessageAnalyzer

class CountingMessage(SimpleNamespace):
    @property
    def message_id(self):
        self.reads += 1
        return 123

def test_message_id_single_read(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    msg = CountingMessage(channel_name="source", text="hello",
        timestamp=datetime.now(timezone.utc), media_type=None, media_size_kb=0, reads=0)
    prompts = []
    async def capture(prompt):
        prompts.append(prompt)
        return None
    with patch.object(MessageAnalyzer, "_call_claude", new=AsyncMock(side_effect=capture)):
        assert asyncio.run(MessageAnalyzer(["topic"])._analyze_one_chunk([msg])) is None
    assert msg.reads == 1
    assert '"message_id": 123' in prompts[0]
