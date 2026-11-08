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


@pytest.mark.parametrize("text", [
    "прив​eт",  # zero-width space
    "прив‍eт",  # zero-width joiner
    "прив⁠eт",  # word joiner
])
def test_invisible_format_characters_do_not_hide_mixed_script(text):
    assert mixed_latin_cyrillic_tokens(text) == [text]


def test_invisible_format_characters_alone_are_not_suspicious():
    assert mixed_latin_cyrillic_tokens("прив​ет") == []


def test_invalid_input_rejected():
    with pytest.raises(TypeError):
        mixed_latin_cyrillic_tokens(None)


@pytest.mark.parametrize(("text", "expected"), [
    ("прив𝒆т", ["прив𝒆т"]),
    ("с𝗮йт", ["с𝗮йт"]),
    ("𝒉𝒆𝒍𝒍𝒐", []),
    ("привет", []),
])
def test_compatibility_styled_latin_letters(text, expected):
    assert mixed_latin_cyrillic_tokens(text) == expected


@pytest.mark.parametrize("text", [
    "\u200bпривeт",
    "\u2060привeт",
    "\u200dпривeт",
])
def test_leading_invisible_format_character_is_preserved(text):
    assert mixed_latin_cyrillic_tokens(text) == [text]


def test_leading_invisible_format_character_not_a_signal_by_itself():
    assert mixed_latin_cyrillic_tokens("\u200bпривет") == []


@pytest.mark.parametrize("text", [
    "\u0301привeт",
    "\u034fпривeт",
])
def test_leading_combining_marks_preserved(text):
    assert mixed_latin_cyrillic_tokens(text) == [text]


def test_leading_combining_mark_not_a_signal_by_itself():
    assert mixed_latin_cyrillic_tokens("\u0301привет") == []
