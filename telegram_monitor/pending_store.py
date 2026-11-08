"""Opt-in crash-recovery queue for not-yet-analyzed Telegram messages.

Only pending messages are stored. No credentials or session files are touched.
The queue is deliberately disabled by default: enabling it stores plaintext
Telegram message content on local disk, so operators must opt in explicitly.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from telegram_monitor.config import PROJECT_ROOT


class PendingMessageStore:
    """SQLite-backed FIFO with unique (channel_id, message_id) message keys."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Do not follow a symlink to a potentially unintended database.
        if self.path.is_symlink():
            raise ValueError("Pending queue database must not be a symlink")
        fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        with self._connect() as con:
            con.execute("""CREATE TABLE IF NOT EXISTS pending_messages (
                position INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                payload TEXT NOT NULL,
                UNIQUE (channel_id, message_id)
            )""")
            # Completed IDs, not their texts, prevent paid historical re-analysis
            # after a restart and repeated startup-history fetches.
            con.execute("""CREATE TABLE IF NOT EXISTS completed_messages (
                position INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                UNIQUE (channel_id, message_id)
            )""")

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(str(self.path), timeout=10)
        try:
            connection.execute("PRAGMA busy_timeout = 10000")
            with connection:
                yield connection
        finally:
            connection.close()

    def add(self, msg) -> bool:
        """Persist a dataclass TelegramMessage before handing it to an analyzer."""
        payload = asdict(msg)
        payload["timestamp"] = msg.timestamp.isoformat()
        with self._connect() as con:
            cursor = con.execute(
                """INSERT OR IGNORE INTO pending_messages (channel_id, message_id, payload)
                   SELECT ?, ?, ? WHERE NOT EXISTS (
                     SELECT 1 FROM completed_messages WHERE channel_id=? AND message_id=?
                   )""",
                (int(msg.channel_id), int(msg.message_id), json.dumps(payload, ensure_ascii=False),
                 int(msg.channel_id), int(msg.message_id)),
            )
            return cursor.rowcount == 1

    def load(self) -> list:
        from telegram_monitor.client import TelegramMessage

        with self._connect() as con:
            rows = con.execute("SELECT payload FROM pending_messages ORDER BY position").fetchall()
        restored = []
        for (raw,) in rows:
            data = json.loads(raw)
            data["timestamp"] = datetime.fromisoformat(data["timestamp"])
            restored.append(TelegramMessage(**data))
        return restored

    def ack(self, messages: list) -> None:
        """Remove only confirmed, successfully analyzed messages."""
        if not messages:
            return
        keys = [(int(m.channel_id), int(m.message_id)) for m in messages]
        with self._connect() as con:
            con.executemany(
                "INSERT OR IGNORE INTO completed_messages (channel_id, message_id) VALUES (?, ?)", keys)
            con.executemany(
                "DELETE FROM pending_messages WHERE channel_id=? AND message_id=?", keys)
            # Keep a bounded history of processed IDs, no message texts.
            count = con.execute("SELECT COUNT(*) FROM completed_messages").fetchone()[0]
            if count > 50000:
                con.execute("""DELETE FROM completed_messages WHERE position IN (
                    SELECT position FROM completed_messages ORDER BY position ASC LIMIT ?
                )""", (count - 50000,))


def enabled_pending_store() -> PendingMessageStore | None:
    value = os.environ.get("PERSIST_ANALYSIS_QUEUE", "0").strip()
    if value != "1":
        return None
    return PendingMessageStore(os.environ.get(
        "ANALYSIS_QUEUE_DB", str(PROJECT_ROOT / "data" / "pending_analysis.sqlite3")
    ))
