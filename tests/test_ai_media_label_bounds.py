"""AI prompt media labels are bounded and control characters are normalized."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram_monitor.analyzer import MessageAnalyzer


def test_media_label_is_bounded(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    message = SimpleNamespace(
        channel_name="source", text="hello", timestamp=datetime.now(timezone.utc),
        message_id=1, media_type="x" * 200 + "\nINJECT", media_size_kb=0,
    )
    analyzer = MessageAnalyzer(["topic"])
    prompts = []

    async def capture(prompt):
        prompts.append(prompt)
        return None

    with patch.object(analyzer, "_call_claude", new=AsyncMock(side_effect=capture)):
        assert asyncio.run(analyzer._analyze_one_chunk([message])) is None
    assert len(prompts) == 1
    assert '"media_type": "' + "x" * 80 + '"' in prompts[0]
    assert "INJECT" not in prompts[0]
