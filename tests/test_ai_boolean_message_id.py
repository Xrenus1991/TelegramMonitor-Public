"""Boolean IDs are not valid numeric Telegram message identifiers."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from telegram_monitor.analyzer import MessageAnalyzer


def test_boolean_message_id_not_numeric(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    msg = SimpleNamespace(channel_name="source", text="hello",
        timestamp=datetime.now(timezone.utc), message_id=True,
        media_type=None, media_size_kb=0)
    prompts = []
    async def capture(prompt):
        prompts.append(prompt)
        return None
    with patch.object(MessageAnalyzer, "_call_claude", new=AsyncMock(side_effect=capture)):
        assert asyncio.run(MessageAnalyzer(["topic"])._analyze_one_chunk([msg])) is None
    assert '"message_id": "True"' in prompts[0]
    assert '"message_id": true' not in prompts[0]
