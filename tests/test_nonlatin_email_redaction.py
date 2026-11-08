"""Non-Latin domain suffixes must not leak email addresses to cloud AI."""
from telegram_monitor.ai_providers import mask_contacts


def test_cyrillic_domain_suffix_redacted():
    assert mask_contacts("Пишите user@пример.рф сегодня") == "Пишите [EMAIL_СКРЫТ] сегодня"


def test_latin_domain_suffix_still_redacted():
    assert mask_contacts("user@example.com") == "[EMAIL_СКРЫТ]"


def test_non_email_words_remain():
    assert mask_contacts("обычный текст без контактов") == "обычный текст без контактов"
