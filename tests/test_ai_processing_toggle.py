"""Tests for the operator-controlled AI processing switch."""
import asyncio
from unittest.mock import patch

from telegram_monitor.analyzer import MessageAnalyzer


def test_ai_disabled_does_not_invoke_provider(monkeypatch):
    monkeypatch.setenv("AI_PROCESSING_ENABLED", "false")
    analyzer = MessageAnalyzer(["topic"])
    with patch.object(analyzer, "_analyze_one_chunk") as provider:
        result = asyncio.run(analyzer.analyze_batch([object()]))
    assert result is None
    provider.assert_not_called()
    assert analyzer._partial_parts == []


def test_ai_off_alias_does_not_invoke_provider(monkeypatch):
    monkeypatch.setenv("AI_PROCESSING_ENABLED", "OFF")
    analyzer = MessageAnalyzer(["topic"])
    with patch.object(analyzer, "_split_into_chunks") as split:
        assert asyncio.run(analyzer.analyze_batch([object()])) is None
    split.assert_not_called()


def test_empty_batch_returns_none_when_disabled(monkeypatch):
    monkeypatch.setenv("AI_PROCESSING_ENABLED", "0")
    assert asyncio.run(MessageAnalyzer([]).analyze_batch([])) is None


def test_ai_unknown_setting_fails_closed(monkeypatch):
    monkeypatch.setenv("AI_PROCESSING_ENABLED", "truue")
    analyzer = MessageAnalyzer(["topic"])
    with patch.object(analyzer, "_analyze_one_chunk") as provider:
        assert asyncio.run(analyzer.analyze_batch([object()])) is None
    provider.assert_not_called()


def test_ai_explicit_true_is_not_blocked(monkeypatch):
    monkeypatch.setenv("AI_PROCESSING_ENABLED", "YES")
    analyzer = MessageAnalyzer(["topic"])
    with patch.object(analyzer, "_split_into_chunks", side_effect=ValueError("sentinel")) as split:
        assert asyncio.run(analyzer.analyze_batch([object()])) is None
    split.assert_called_once()
