"""Shared fixtures for Telegram Monitor tests."""

import json
import importlib.util
import os
from pathlib import Path

# This repository snapshot deliberately excludes live channel profiles.
# Set a safe, synthetic config BEFORE test modules import config.py.
os.environ.setdefault(
    "TELEGRAM_MONITOR_CONFIG",
    str(Path(__file__).parent / "fixtures" / "test_config.json"),
)

# Offline fallback: verifies our client logic, NOT Telethon's actual behavior.
if importlib.util.find_spec("telethon") is None:
    from tests._telethon_stub import install
    install()
import pytest

# Set a dummy config so config.py doesn't prompt interactively during tests
FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _set_config_env(tmp_path, monkeypatch):
    """Point TELEGRAM_MONITOR_CONFIG at the test fixture config."""
    monkeypatch.setenv("TELEGRAM_MONITOR_CONFIG", str(FIXTURES_DIR / "test_config.json"))


@pytest.fixture
def configs_dir():
    """Path to the real configs/ directory."""
    return Path(__file__).parent.parent / "configs"


@pytest.fixture
def sample_config():
    """Load the test fixture config as a dict."""
    with open(FIXTURES_DIR / "test_config.json") as f:
        return json.load(f)
