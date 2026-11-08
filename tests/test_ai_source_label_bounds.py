"""AI prompt source labels must not permit unbounded or control-character content."""
import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram_monitor.analyzer import MessageAnalyzer


def test_source_name_bounded_and_controls_removed(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    label = "A" * 300 + "\nINJECT\t\x00"
    message = SimpleNamespace(
        channel_name=label, text="hello", timestamp=datetime.now(timezone.utc),
        message_id=1, media_type=None, media_size_kb=0,
    )
    analyzer = MessageAnalyzer(["topic"])
    captured = []

    async def capture(prompt):
        captured.append(prompt)
        return None

    with patch.object(analyzer, "_call_claude", new=AsyncMock(side_effect=capture)):
        assert asyncio.run(analyzer._analyze_one_chunk([message])) is None
    assert len(captured) == 1
    assert '"source": "' + "A" * 200 + '"' in captured[0]
    assert "INJECT" not in captured[0]
    assert "\\u0000" not in captured[0]
