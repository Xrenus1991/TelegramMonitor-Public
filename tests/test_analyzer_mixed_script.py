"""Offline analyzer prompt tests: no network or Telegram session."""
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from telegram_monitor.analyzer import MessageAnalyzer


@pytest.mark.asyncio
@pytest.mark.parametrize(("message_text", "expected"), [
    ("привeт", ["привeт"]),
    ("обычный русский текст", []),
    ("hello world", []),
    ("сaйт и дoм", ["сaйт", "дoм"]),
])
async def test_analyzer_adds_mixed_script_review_metadata(message_text, expected):
    analyzer = MessageAnalyzer(topics=["test"])
    captured = []

    async def fake_model(prompt):
        captured.append(prompt)
        return None

    msg = SimpleNamespace(
        channel_name="test", message_id=42, text=message_text,
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        media_type=None, media_size_kb=0,
    )
    with patch("telegram_monitor.analyzer.provider_name", return_value="ollama"):
        with patch.object(analyzer, "_call_claude", new=fake_model):
            await analyzer._analyze_one_chunk([msg])
    assert len(captured) == 1
    records = [json.loads(line) for line in captured[0].splitlines()
               if line.lstrip().startswith('{"time_utc":')]
    assert len(records) == 1
    assert records[0]["mixed_script_tokens"] == expected
    assert records[0]["content"] == message_text
