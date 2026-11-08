"""Media size is read only once per AI prompt message."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from telegram_monitor.analyzer import MessageAnalyzer

class CountingMessage(SimpleNamespace):
    @property
    def media_size_kb(self):
        self.reads += 1
        return "12"

def test_media_size_single_read(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    msg = CountingMessage(channel_name="source", text="hello",
        timestamp=datetime.now(timezone.utc), message_id=1, media_type=None, reads=0)
    prompts = []
    async def capture(prompt):
        prompts.append(prompt)
        return None
    with patch.object(MessageAnalyzer, "_call_claude", new=AsyncMock(side_effect=capture)):
        assert asyncio.run(MessageAnalyzer(["topic"])._analyze_one_chunk([msg])) is None
    assert msg.reads == 1
    assert '"media_size_kb": 12' in prompts[0]
