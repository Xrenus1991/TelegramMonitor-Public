"""Offline regression checks for Telegram channel comment-thread discovery.

No Telegram login, network, database, or personal data is required.
"""
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from telegram_monitor.cloud.comments import collect_thread
from telegram_monitor.cloud import collector


class FakeStore:
    def __init__(self):
        self.saved = []

    def save_thread(self, channel_id, post_id, rows, *, capped):
        self.saved.append((channel_id, post_id, rows, capped))
        return len(rows)


class FakeClient:
    def __init__(self, replies=None, *, comments=(), fail_at=None, missing=False):
        self.replies = replies
        self.comments = list(comments)
        self.fail_at = fail_at
        self.missing = missing
        self.post_reads = 0
        self.reply_reads = 0

    async def get_messages(self, entity, ids):
        self.post_reads += 1
        if self.fail_at == "post":
            raise RuntimeError("POST_ERROR")
        if self.missing:
            return None
        return SimpleNamespace(replies=self.replies)

    async def iter_messages(self, entity, reply_to, limit):
        self.reply_reads += 1
        if self.fail_at == "reply":
            raise RuntimeError("REPLY_ERROR")
        for comment in self.comments[:limit]:
            yield comment


ENTITY = SimpleNamespace(id=12345)


class CommentDiscussionGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_collector_imports_patched_function(self):
        self.assertIs(collector.collect_thread, collect_thread)

    async def test_no_or_empty_discussion_skips_reply_request(self):
        for reply_info in (
            None,
            SimpleNamespace(comments=False, replies=7),
            SimpleNamespace(comments=True, replies=0),
        ):
            with self.subTest(reply_info=repr(reply_info)):
                client = FakeClient(reply_info)
                store = FakeStore()
                result = await collect_thread(
                    client, ENTITY, store, 111, max_comments=50
                )
                self.assertEqual(result, (0, 0))
                self.assertEqual(client.post_reads, 1)
                self.assertEqual(client.reply_reads, 0)
                self.assertEqual(store.saved, [(12345, 111, [], False)])

    async def test_existing_discussion_saves_comment(self):
        raw_comment = SimpleNamespace(
            id=567,
            date=datetime.now(timezone.utc),
            message="Example comment",
            peer_id=SimpleNamespace(channel_id=333),
            from_id=None,
        )
        client = FakeClient(
            SimpleNamespace(comments=True, replies=1),
            comments=[raw_comment],
        )
        store = FakeStore()
        added, seen = await collect_thread(
            client, ENTITY, store, 114, max_comments=50
        )
        self.assertEqual((added, seen), (1, 1))
        self.assertEqual(client.reply_reads, 1)
        self.assertEqual(len(store.saved), 1)
        self.assertEqual(len(store.saved[0][2]), 1)
        self.assertFalse(store.saved[0][3])

    async def test_missing_post_is_not_acknowledged(self):
        store = FakeStore()
        client = FakeClient(missing=True)
        with self.assertRaises(ValueError):
            await collect_thread(client, ENTITY, store, 115, max_comments=50)
        self.assertEqual(store.saved, [])

    async def test_telegram_failure_does_not_record_completed_scan(self):
        for fail_at in ("post", "reply"):
            with self.subTest(fail_at=fail_at):
                store = FakeStore()
                client = FakeClient(
                    SimpleNamespace(comments=True, replies=1),
                    fail_at=fail_at,
                )
                with self.assertRaises(RuntimeError):
                    await collect_thread(
                        client, ENTITY, store, 116, max_comments=50
                    )
                self.assertEqual(store.saved, [])


if __name__ == "__main__":
    unittest.main()
