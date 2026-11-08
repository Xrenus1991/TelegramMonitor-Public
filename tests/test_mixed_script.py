"""Offline regression tests for mixed-script moderation signals."""
import pytest

from telegram_monitor.mixed_script import mixed_latin_cyrillic_tokens


@pytest.mark.parametrize(("text", "expected"), [
    ("привет мир", []),
    ("hello world", []),
    ("привeт", ["привeт"]),  # Latin e
    ("сaйт cайт", ["сaйт", "cайт"]),
    ("привeт, привeт!", ["привeт"]),
    ("ПрИвEт\nдoм", ["ПрИвEт", "дoм"]),
    ("Русский English 123", []),
    ("aб", ["aб"]),
    ("аb", ["аb"]),
    ("дoм.example", ["дoм"]),
    ("", []),
])
def test_mixed_script_detection(text, expected):
    assert mixed_latin_cyrillic_tokens(text) == expected


def test_does_not_mutate_message():
    original = "привeт"
    assert mixed_latin_cyrillic_tokens(original) == [original]
    assert original == "привeт"


def test_combining_marks_preserved():
    assert mixed_latin_cyrillic_tokens("аe\u0301") == ["аe\u0301"]


def test_invalid_input_rejected():
    with pytest.raises(TypeError):
        mixed_latin_cyrillic_tokens(None)
