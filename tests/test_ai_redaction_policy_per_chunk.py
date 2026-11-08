"""Redaction setting is evaluated once per AI chunk, not once per message."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram_monitor.analyzer import MessageAnalyzer


def test_redaction_policy_evaluated_once_for_chunk(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    messages = [
        SimpleNamespace(channel_name="source", text="contact user@example.com",
            timestamp=datetime.now(timezone.utc), message_id=i,
            media_type=None, media_size_kb=0)
        for i in range(3)
    ]
    prompts = []
    async def capture(prompt):
        prompts.append(prompt)
        return None
    with patch("telegram_monitor.analyzer.should_redact_contacts", return_value=True) as policy, \
         patch.object(MessageAnalyzer, "_call_claude", new=AsyncMock(side_effect=capture)):
        assert asyncio.run(MessageAnalyzer(["topic"])._analyze_one_chunk(messages)) is None
    policy.assert_called_once_with()
    assert len(prompts) == 1
    assert "user@example.com" not in prompts[0]
    assert prompts[0].count("[EMAIL_СКРЫТ]") == 3
