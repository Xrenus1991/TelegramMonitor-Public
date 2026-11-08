"""Tests for telegram_monitor.utils."""

from telegram_monitor.utils import sanitize_filename, strip_media_prefix


class TestSanitizeFilename:
    def test_alphanumeric_unchanged(self):
        assert sanitize_filename("HelloWorld123") == "HelloWorld123"

    def test_unicode_preserved_spaces_replaced(self):
        result = sanitize_filename("חדשות קאן")
        assert " " not in result
        assert "_" in result

    def test_spaces_and_symbols(self):
        assert sanitize_filename("My Channel!@#$") == "My_Channel____"

    def test_truncation(self):
        long = "a" * 50
        assert len(sanitize_filename(long)) == 30

    def test_custom_max_len(self):
        assert sanitize_filename("abcdefgh", max_len=5) == "abcde"

    def test_empty_string(self):
        assert sanitize_filename("") == ""


class TestStripMediaPrefix:
    def test_photo_prefix(self):
        assert strip_media_prefix("[PHOTO] Some caption") == "Some caption"

    def test_video_prefix(self):
        assert strip_media_prefix("[VIDEO] Video caption") == "Video caption"

    def test_gif_prefix(self):
        assert strip_media_prefix("[GIF] Animated") == "Animated"

    def test_no_prefix(self):
        assert strip_media_prefix("Regular text") == "Regular text"

    def test_empty_string(self):
        assert strip_media_prefix("") == ""

    def test_prefix_in_middle_not_stripped(self):
        assert strip_media_prefix("Before [PHOTO] After") == "Before [PHOTO] After"
