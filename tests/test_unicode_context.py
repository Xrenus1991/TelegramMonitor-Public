import pytest
from telegram_monitor.cloud import unicode_context as module


def test_disabled_and_unavailable_preserve_original(monkeypatch):
    text = "Гpaницa @Гpaницa https://example.org/Гpaницa"
    assert module.inspect_unicode(text)["normalized"] == text
    monkeypatch.setattr(module, "_backend", lambda: None)
    result = module.inspect_unicode(text, enabled=True)
    assert result["normalized"] == text and result["available"] is False


@pytest.mark.parametrize("text, expected", [
    ("Гpaницa", "граница"),
    ("пoжар", "пожар"),
    ("угрοза", "угроза"),
    ("гра\u200bница", "граница"),
])
def test_reviewed_variants(text, expected):
    pytest.importorskip("confusable_homoglyphs")
    result = module.inspect_unicode(text, enabled=True)
    assert result["available"] and result["original"] == text
    assert result["normalized"] == expected
    assert len(result["findings"]) == 1
    assert module.inspect_unicode(expected, enabled=True)["findings"] == []


@pytest.mark.parametrize("text", [
    "OpenAI модель API Python", "macОС", "Երևան Հայաստան", "հայ\u200dերեն",
    "https://example.org/Гpaницa @Гpaницa mailГpaницa@example.com",
    "www.example.org/пoжар t.me/Гpaницa", "Гpaницa.com", "обычный русский текст",
])
def test_legitimate_and_protected_tokens_preserved(text):
    pytest.importorskip("confusable_homoglyphs")
    result = module.inspect_unicode(text, enabled=True)
    assert result["normalized"] == text and result["findings"] == []


def test_bounded_input_preserves_tail():
    pytest.importorskip("confusable_homoglyphs")
    text = "a" * module.MAX_CHARS + " Гpaницa"
    result = module.inspect_unicode(text, enabled=True)
    assert result["truncated"] and result["normalized"] == text


def test_invalid_input():
    with pytest.raises(TypeError):
        module.inspect_unicode(None)
