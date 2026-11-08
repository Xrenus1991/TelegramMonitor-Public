"""Shared utilities for Telegram Monitor."""

import os
import sys

MEDIA_PREFIXES = ("[PHOTO] ", "[VIDEO] ", "[GIF] ")


def parse_config_arg() -> str:
    """Extract --config/-c from sys.argv and set the env var.

    Must be called BEFORE importing config so the env var is visible.
    Returns the config path if provided, or empty string if omitted.
    """
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg in ("--config", "-c") and i + 1 < len(sys.argv):
            os.environ["TELEGRAM_MONITOR_CONFIG"] = sys.argv[i + 1]
            return sys.argv[i + 1]
        if arg.startswith("--config="):
            val = arg.split("=", 1)[1]
            os.environ["TELEGRAM_MONITOR_CONFIG"] = val
            return val
    return ""


def sanitize_filename(name: str, max_len: int = 30) -> str:
    """Replace non-alphanumeric chars with _ and truncate."""
    return "".join(c if c.isalnum() else "_" for c in name)[:max_len]


def strip_media_prefix(text: str) -> str:
    """Remove [PHOTO]/[VIDEO]/[GIF] prefix from message text."""
    for prefix in MEDIA_PREFIXES:
        if text.startswith(prefix):
            return text[len(prefix):]
    return text
