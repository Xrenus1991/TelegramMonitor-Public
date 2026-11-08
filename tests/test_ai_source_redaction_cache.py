"""Repeated source labels are masked once per chunk without exposing contacts."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram_monitor.analyzer import MessageAnalyzer
from telegram_monitor.ai_providers import mask_contacts as real_mask


def test_repeated_source_redacted_once_per_chunk(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_REDACT_CONTACTS", "1")
    messages = [
        SimpleNamespace(channel_name="channel@example.com", text="plain text",
            timestamp=datetime.now(timezone.utc), message_id=i,
            media_type=None, media_size_kb=0)
        for i in range(4)
    ]
    prompts = []
    calls = []
    def tracked_mask(text):
        calls.append(text)
        return real_mask(text)
    async def capture(prompt):
        prompts.append(prompt)
        return None
    with patch("telegram_monitor.analyzer.mask_contacts", side_effect=tracked_mask), \
         patch.object(MessageAnalyzer, "_call_claude", new=AsyncMock(side_effect=capture)):
        assert asyncio.run(MessageAnalyzer(["topic"])._analyze_one_chunk(messages)) is None
    assert calls.count("channel@example.com") == 1
    assert "channel@example.com" not in prompts[0]
    assert prompts[0].count("[EMAIL_СКРЫТ]") == 4
