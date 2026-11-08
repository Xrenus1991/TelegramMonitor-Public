"""Regression: disabling AI during a batch skips remaining provider calls."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram_monitor.analyzer import AnalysisResult, MessageAnalyzer


def test_disable_after_first_chunk_skips_second(monkeypatch):
    monkeypatch.setenv("AI_PROCESSING_ENABLED", "true")
    analyzer = MessageAnalyzer(["topic"])
    messages = [SimpleNamespace(text="a", channel_name="a"), SimpleNamespace(text="b", channel_name="a")]
    part = AnalysisResult(timestamp=datetime.now(timezone.utc), raw_text="ok", summary="ok")
    async def provider(_chunk):
        monkeypatch.setenv("AI_PROCESSING_ENABLED", "false")
        return part
    with patch.object(analyzer, "_split_into_chunks", return_value=[[messages[0]], [messages[1]]]), \
         patch.object(analyzer, "_retry_signature", return_value="batch"), \
         patch.object(analyzer, "_analyze_one_chunk", new_callable=AsyncMock, side_effect=provider) as mock_provider:
        assert asyncio.run(analyzer.analyze_batch(messages)) is None
        assert mock_provider.await_count == 1
    assert analyzer._partial_parts == []
    assert analyzer._partial_batch_key is None
