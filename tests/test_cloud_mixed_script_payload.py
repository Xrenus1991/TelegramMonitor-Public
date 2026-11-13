"""Mixed-script hint is passed to cloud classifier without changing source text."""
import json

from telegram_monitor.cloud.worker import pack


def _row(text):
    return [{"key": "1:2", "text": text, "channel_name": "source", "timestamp": None}]


def test_mixed_script_hint_true():
    item = json.loads(pack(_row("привeт"), 100))[0]
    assert item["mixed_script_detected"] is True
    assert item["text"] == "привeт"


def test_mixed_script_hint_false_for_normal_cyrillic():
    item = json.loads(pack(_row("привет"), 100))[0]
    assert item["mixed_script_detected"] is False


def test_mixed_script_hint_respects_truncation():
    item = json.loads(pack(_row("привет привeт"), 6))[0]
    assert item["mixed_script_detected"] is False
    assert item["truncated"] is True


def test_mixed_script_hint_does_not_expose_original_token():
    item = json.loads(pack(_row("привeт"), 100))[0]
    assert isinstance(item["mixed_script_detected"], bool)
    assert "mixed_script_tokens" not in item
