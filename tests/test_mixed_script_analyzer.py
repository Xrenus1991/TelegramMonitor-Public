"""Offline integration checks: mixed-script signals reach AI prompt data."""
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from telegram_monitor.analyzer import MessageAnalyzer


@pytest.mark.asyncio
async def test_mixed_script_tokens_are_in_prompt_data(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    analyzer = MessageAnalyzer(["test"])
    captured = []

    async def capture(prompt):
        captured.append(prompt)
        return None

    analyzer._call_claude = capture
    msg = SimpleNamespace(
        text="привeт обычный текст",
        channel_name="test",
        timestamp=datetime.now(timezone.utc),
        message_id=42,
        media_type=None,
        media_size_kb=0,
    )
    await analyzer._analyze_one_chunk([msg])
    assert len(captured) == 1
    assert '"mixed_script_tokens": ["привeт"]' in captured[0]
    assert '"content": "привeт обычный текст"' in captured[0]


@pytest.mark.asyncio
async def test_mixed_script_signal_is_bounded(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    analyzer = MessageAnalyzer(["test"])
    captured = []

    async def capture(prompt):
        captured.append(prompt)
        return None

    analyzer._call_claude = capture
    msg = SimpleNamespace(
        text=" ".join("аb" + str(i) for i in range(20)),
        channel_name="test",
        timestamp=datetime.now(timezone.utc),
        message_id=43,
        media_type=None,
        media_size_kb=0,
    )
    await analyzer._analyze_one_chunk([msg])
    assert len(captured) == 1
    # Tokens are separated by digits, so the repeated mixed token is deduplicated.
    assert '"mixed_script_tokens": ["аb"]' in captured[0]



@pytest.mark.asyncio
async def test_long_mixed_script_signal_is_truncated_but_content_preserved(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    analyzer = MessageAnalyzer(["test"])
    prompts = []

    async def capture(prompt):
        prompts.append(prompt)
        return None

    analyzer._call_claude = capture
    original = "а" + "b" * 500
    msg = SimpleNamespace(
        text=original,
        channel_name="test",
        timestamp=datetime.now(timezone.utc),
        message_id=44,
        media_type=None,
        media_size_kb=0,
    )
    await analyzer._analyze_one_chunk([msg])
    assert len(prompts) == 1
    assert '"mixed_script_tokens": ["' + original[:80] + '"]' in prompts[0]
    assert '"content": "' + original + '"' in prompts[0]
