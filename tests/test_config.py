"""Tests for telegram_monitor.config — validates config loading and all JSON profiles."""

import json
from pathlib import Path

import pytest

REQUIRED_KEYS = [
    "channels", "topics", "stances", "tones", "stance_color_rules",
    "analysis_prompt_intro", "analysis_system_prompt",
    "caption_system_prompt", "caption_language_line",
    "app_title", "summary_batch_size", "summary_interval_seconds",
]


class TestConfigLoading:
    def test_channels_loaded(self):
        from telegram_monitor.config import CHANNELS
        assert isinstance(CHANNELS, list)
        assert len(CHANNELS) > 0
        assert all(isinstance(ch, str) for ch in CHANNELS)

    def test_topics_loaded(self):
        from telegram_monitor.config import TOPICS
        assert isinstance(TOPICS, list)
        assert len(TOPICS) > 0

    def test_stances_loaded(self):
        from telegram_monitor.config import STANCES
        assert isinstance(STANCES, tuple)
        assert len(STANCES) > 0

    def test_tones_loaded(self):
        from telegram_monitor.config import TONES
        assert isinstance(TONES, tuple)
        assert len(TONES) > 0

    def test_stance_color_rules_structure(self):
        from telegram_monitor.config import STANCE_COLOR_RULES
        assert isinstance(STANCE_COLOR_RULES, list)
        for keywords, color in STANCE_COLOR_RULES:
            assert isinstance(keywords, tuple)
            assert isinstance(color, str)

    def test_timing_constants(self):
        from telegram_monitor.config import SUMMARY_BATCH_SIZE, SUMMARY_INTERVAL_SECONDS
        assert isinstance(SUMMARY_BATCH_SIZE, int) and SUMMARY_BATCH_SIZE > 0
        assert isinstance(SUMMARY_INTERVAL_SECONDS, int) and SUMMARY_INTERVAL_SECONDS > 0

    def test_urgency_levels(self):
        from telegram_monitor.config import URGENCY_DEFAULT, URGENCY_LEVELS
        assert "CRITICAL" in URGENCY_LEVELS
        assert "HIGH" in URGENCY_LEVELS
        assert "NORMAL" in URGENCY_LEVELS
        assert "LOW" in URGENCY_LEVELS
        assert URGENCY_DEFAULT in URGENCY_LEVELS

    def test_section_headers_defined(self):
        from telegram_monitor.config import (
            SECTION_SUMMARY,
            SECTION_TRANSLATIONS,
            SECTION_URGENCY,
        )
        assert SECTION_URGENCY == "URGENCY ASSESSMENT"
        assert SECTION_SUMMARY == "EXECUTIVE SUMMARY"
        assert SECTION_TRANSLATIONS == "TRANSLATIONS"

    def test_prompt_template_has_placeholders(self):
        from telegram_monitor.config import ANALYSIS_PROMPT_TEMPLATE
        assert "{n_messages}" in ANALYSIS_PROMPT_TEMPLATE
        assert "{n_sources}" in ANALYSIS_PROMPT_TEMPLATE
        assert "{msg_block}" in ANALYSIS_PROMPT_TEMPLATE
        assert "{topics_str}" in ANALYSIS_PROMPT_TEMPLATE

    def test_project_root_is_valid(self):
        from telegram_monitor.config import PROJECT_ROOT
        assert Path(PROJECT_ROOT).is_dir()
        assert (Path(PROJECT_ROOT) / "telegram_monitor").is_dir()


class TestConfigProfiles:
    """Validate every JSON config in configs/ has the correct schema."""

    @staticmethod
    def _config_files():
        configs_dir = Path(__file__).parent.parent / "configs"
        return sorted(configs_dir.glob("*.json"))

    @pytest.mark.parametrize("config_path", _config_files.__func__(),
                             ids=lambda p: p.stem)
    def test_valid_json(self, config_path):
        with open(config_path) as f:
            cfg = json.load(f)
        assert isinstance(cfg, dict)

    @pytest.mark.parametrize("config_path", _config_files.__func__(),
                             ids=lambda p: p.stem)
    def test_has_all_required_keys(self, config_path):
        with open(config_path) as f:
            cfg = json.load(f)
        missing = [k for k in REQUIRED_KEYS if k not in cfg]
        assert not missing, f"{config_path.name} missing keys: {missing}"

    @pytest.mark.parametrize("config_path", _config_files.__func__(),
                             ids=lambda p: p.stem)
    def test_channels_structure(self, config_path):
        with open(config_path) as f:
            cfg = json.load(f)
        channels = cfg["channels"]
        assert isinstance(channels, dict), "channels must be a dict of categories"
        total = 0
        for category, entries in channels.items():
            assert isinstance(entries, list), f"Category '{category}' must be a list"
            for entry in entries:
                assert "username" in entry, f"Missing 'username' in {category}"
                assert "description" in entry, f"Missing 'description' in {category}"
                assert isinstance(entry["username"], str)
                assert len(entry["username"]) > 0
                total += 1
        assert total > 0, f"{config_path.name} has no channels"

    @pytest.mark.parametrize("config_path", _config_files.__func__(),
                             ids=lambda p: p.stem)
    def test_stance_color_rules_structure(self, config_path):
        with open(config_path) as f:
            cfg = json.load(f)
        for rule in cfg["stance_color_rules"]:
            assert "keywords" in rule
            assert "color" in rule
            assert isinstance(rule["keywords"], list)
            assert len(rule["keywords"]) > 0

    @pytest.mark.parametrize("config_path", _config_files.__func__(),
                             ids=lambda p: p.stem)
    def test_prompt_placeholders(self, config_path):
        with open(config_path) as f:
            cfg = json.load(f)
        intro = cfg["analysis_prompt_intro"]
        assert "{n_messages}" in intro
        assert "{n_sources}" in intro

    @pytest.mark.parametrize("config_path", _config_files.__func__(),
                             ids=lambda p: p.stem)
    def test_no_duplicate_channels(self, config_path):
        with open(config_path) as f:
            cfg = json.load(f)
        usernames = [
            ch["username"]
            for entries in cfg["channels"].values()
            for ch in entries
        ]
        dupes = [u for u in usernames if usernames.count(u) > 1]
        assert not dupes, f"Duplicate channels in {config_path.name}: {set(dupes)}"
