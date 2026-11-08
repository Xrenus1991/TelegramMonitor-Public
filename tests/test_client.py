"""Tests for telegram_monitor.client — unit tests for non-network logic."""

from telegram_monitor.client import TelegramMonitor


class TestBuildDisplayText:
    def test_text_only(self):
        assert TelegramMonitor._build_display_text("Hello", None) == "Hello"

    def test_media_with_text(self):
        assert TelegramMonitor._build_display_text("Caption", "photo") == "[PHOTO] Caption"

    def test_media_without_text(self):
        assert TelegramMonitor._build_display_text("", "video") == "[VIDEO]"

    def test_no_text_no_media(self):
        assert TelegramMonitor._build_display_text("", None) is None

    def test_gif_media(self):
        assert TelegramMonitor._build_display_text("", "gif") == "[GIF]"

    def test_document_media(self):
        assert TelegramMonitor._build_display_text("Doc", "document") == "[DOCUMENT] Doc"
