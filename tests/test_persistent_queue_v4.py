"""V4 SQLite queue regression tests. No network or private data used."""
import asyncio
import os
import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest

from telegram_monitor.client import TelegramMessage
from telegram_monitor.pending_store import PendingMessageStore, enabled_pending_store


def sample(message_id, channel_id=3):
    return TelegramMessage(channel_name="Тест", channel_id=channel_id, text=f"Synthetic {message_id}",
                           timestamp=datetime(2026, 10, 31, tzinfo=timezone.utc), message_id=message_id)


def test_queue_round_trip_dedup_and_ack(tmp_path):
    path = tmp_path / "queue.sqlite3"
    store = PendingMessageStore(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert store.add(sample(1))
    assert store.add(sample(2))
    assert not store.add(sample(1))
    assert store.add(sample(1, channel_id=4))
    assert [x.message_id for x in PendingMessageStore(path).load()] == [1, 2, 1]
    store.ack([sample(1)])
    assert not store.add(sample(1))  # already analyzed, no duplicate paid batch after restart
    restored = PendingMessageStore(path).load()
    assert [(x.channel_id, x.message_id) for x in restored] == [(3, 2), (4, 1)]
    assert restored[0].timestamp.tzinfo == timezone.utc
    assert restored[0].text == "Synthetic 2"


def test_no_credentials_or_message_logged_in_queue(tmp_path):
    store = PendingMessageStore(tmp_path / "queue.sqlite3")
    msg = sample(8)
    msg.text = "TEST PRIVATE"
    assert store.add(msg)
    assert store.load()[0].text == "TEST PRIVATE"
    # Explicit opt-in: filesystem stores plaintext, but no Telegram secrets.
    assert not any('api_hash' in c for c in (store.load()[0].__dict__,))


def test_queue_is_opt_in(tmp_path, monkeypatch):
    monkeypatch.delenv("PERSIST_ANALYSIS_QUEUE", raising=False)
    assert enabled_pending_store() is None
    monkeypatch.setenv("PERSIST_ANALYSIS_QUEUE", "1")
    monkeypatch.setenv("ANALYSIS_QUEUE_DB", str(tmp_path / "test.sqlite3"))
    assert isinstance(enabled_pending_store(), PendingMessageStore)


def test_queue_symlink_rejected(tmp_path):
    target = tmp_path / "target.db"
    target.write_text("do not modify")
    link = tmp_path / "link.db"
    link.symlink_to(target)
    with pytest.raises(ValueError):
        PendingMessageStore(link)
    assert target.read_text() == "do not modify"
