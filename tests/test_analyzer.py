"""Tests for telegram_monitor.analyzer — parsing logic (no Claude API calls)."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

from telegram_monitor.analyzer import AnalysisResult, MessageAnalyzer


def _make_msg(channel="test_channel", text="Hello", media_type=None):
    """Create a mock TelegramMessage."""
    msg = MagicMock()
    msg.channel_name = channel
    msg.text = text
    msg.timestamp = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    msg.message_id = 1
    msg.has_media = media_type is not None
    msg.media_type = media_type
    msg.media_size_kb = 0
    return msg


class TestSplitSections:
    def test_basic_sections(self):
        analyzer = MessageAnalyzer(topics=["Topic A"])
        text = (
            "## URGENCY ASSESSMENT\nCRITICAL\nActive attack.\n\n"
            "## EXECUTIVE SUMMARY\nSomething happened.\n\n"
            "## TRANSLATIONS\n- [ch1] EN: Hello | TOPIC: Topic A\n"
        )
        sections = analyzer._split_sections(text)
        assert "URGENCY ASSESSMENT" in sections
        assert "EXECUTIVE SUMMARY" in sections
        assert "TRANSLATIONS" in sections

    def test_empty_input(self):
        analyzer = MessageAnalyzer(topics=[])
        assert analyzer._split_sections("") == {}

    def test_no_headers(self):
        analyzer = MessageAnalyzer(topics=[])
        assert analyzer._split_sections("just plain text") == {}


class TestParseResponse:
    def _parse(self, text, topics=None):
        analyzer = MessageAnalyzer(topics=topics or ["Topic A"])
        msgs = [_make_msg()]
        return analyzer._parse_response(text, msgs, {"test_channel": 1})

    def test_urgency_critical(self):
        text = "## URGENCY ASSESSMENT\nCRITICAL\nMajor attack in progress.\n\n## EXECUTIVE SUMMARY\nTest."
        result = self._parse(text)
        assert result.urgency == "CRITICAL"
        assert "attack" in result.urgency_reason.lower()

    def test_urgency_low(self):
        text = "## URGENCY ASSESSMENT\nLOW\nRoutine updates.\n\n## EXECUTIVE SUMMARY\nNothing new."
        result = self._parse(text)
        assert result.urgency == "LOW"

    def test_urgency_default_when_missing(self):
        text = "## EXECUTIVE SUMMARY\nNo urgency section."
        result = self._parse(text)
        assert result.urgency == "NORMAL"

    def test_summary_parsed(self):
        text = "## URGENCY ASSESSMENT\nNORMAL\nOK.\n\n## EXECUTIVE SUMMARY\nThis is the summary."
        result = self._parse(text)
        assert result.summary == "This is the summary."

    def test_translations_parsed(self):
        text = (
            "## TRANSLATIONS\n"
            "- [ch1] EN: Hello world | TOPIC: Topic A\n"
            "- [ch2] EN: Goodbye | TOPIC: Topic A\n"
        )
        result = self._parse(text)
        assert len(result.translations) == 2

    def test_key_actors_parsed(self):
        text = (
            "## KEY ACTORS & ENTITIES\n"
            "- John Doe | President | Mentioned in speech | Sentiment: neutral\n"
        )
        result = self._parse(text)
        assert len(result.key_actors) == 1
        assert result.key_actors[0]["name"] == "John Doe"
        assert result.key_actors[0]["role"] == "President"

    def test_timeline_parsed(self):
        text = (
            "## TIMELINE OF EVENTS\n"
            "- 08:30 Explosion reported\n"
            "- 09:15 Officials respond\n"
        )
        result = self._parse(text)
        assert len(result.timeline) == 2
        assert result.timeline[0]["time"] == "08:30"
        assert result.timeline[0]["event"] == "Explosion reported"

    def test_contradictions_parsed(self):
        text = (
            "## CONTRADICTIONS & CROSS-REFERENCES\n"
            "- Source A says X, Source B says Y\n"
            "- No confirmed casualties vs 10 reported\n"
        )
        result = self._parse(text)
        assert len(result.contradictions) == 2

    def test_topic_deep_dive_parsed(self):
        text = (
            "## TOPIC DEEP DIVE\n"
            "### Topic A\n"
            "Analysis of topic A goes here.\n"
            "### Topic B\n"
            "Analysis of topic B.\n"
        )
        result = self._parse(text, topics=["Topic A", "Topic B"])
        assert "Topic A" in result.topic_summaries
        assert "Topic B" in result.topic_summaries

    def test_msg_count(self):
        text = "## EXECUTIVE SUMMARY\nTest."
        result = self._parse(text)
        assert result.msg_count == 1


class TestGetSection:
    def test_direct_match(self):
        sections = {"KEY ACTORS & ENTITIES": "data"}
        result = MessageAnalyzer._get_section(sections, "KEY ACTORS & ENTITIES")
        assert result == "data"

    def test_alias_match(self):
        sections = {"KEY ACTORS": "data via alias"}
        result = MessageAnalyzer._get_section(sections, "KEY ACTORS & ENTITIES")
        assert result == "data via alias"

    def test_default_when_missing(self):
        result = MessageAnalyzer._get_section({}, "MISSING", default="fallback")
        assert result == "fallback"


class TestAnalysisResult:
    def test_defaults(self):
        result = AnalysisResult(
            timestamp=datetime.now(timezone.utc),
            raw_text="test",
        )
        assert result.urgency == "NORMAL"
        assert result.summary == ""
        assert result.translations == []
        assert result.msg_count == 0
