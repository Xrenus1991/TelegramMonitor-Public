"""VPS Telegram ingestion. Only one collector process should run per session.

Each periodic reverse-ordered scan fills gaps caused by disconnects; the DB
checkpoint advances atomically with ingestion. Never download media in v7.
"""
import asyncio
import logging
import os
import signal
from datetime import timezone
from pathlib import Path
from .db import Store
from .source_registry import SourceRegistry, source_info
from .comments import CommentStore, channel_ids_from_env, collect_thread

log = logging.getLogger("cloud.collector")


def sender_user_id(raw):
    """Return a Telegram *user* ID only when the sender is explicitly PeerUser.

    Anonymous admins and channel-as-sender posts have PeerChannel (or no
    from_id); their numeric chat IDs must never be mislabeled as user IDs.
    """
    peer = getattr(raw, "from_id", None)
    uid = getattr(peer, "user_id", None) if peer is not None else None
    if isinstance(uid, int) and not isinstance(uid, bool) and 0 < uid < 2**63:
        return uid
    return None


def normalise(raw, entity):
    body = getattr(raw, "message", None) or ""
    body = str(body).strip()
    if not body and getattr(raw, "media", None):
        body = "[МЕДИА БЕЗ ТЕКСТОВОЙ ПОДПИСИ — содержимое не изучено]"
    if not body:
        return None
    date = raw.date
    if date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)
    return {
        "channel_id": int(entity.id), "message_id": int(raw.id),
        "sender_user_id": sender_user_id(raw),
        "channel_name": str(getattr(entity, "title", str(entity.id))),
        "timestamp": date.astimezone(timezone.utc), "text": body,
    }


async def sync_recent_messages(client, store: Store, entity, count: int):
    """One-shot newest-first Telegram sample, without changing sync checkpoints.

    Only missing messages enter the normal Worker queue; already classified
    messages remain untouched to avoid accidental extra OpenAI cost.
    """
    if type(count) is not int or not 1 <= count <= 1000:
        raise ValueError("Invalid recent message count")
    records = []
    scanned = 0
    async for raw in client.iter_messages(entity, limit=count):
        scanned += 1
        value = normalise(raw, entity)
        if value:
            records.append(value)
    records.sort(key=lambda item: item["message_id"])
    saved = 0
    for start in range(0, len(records), 100):
        saved += store.ingest(int(entity.id), records[start:start + 100],
                             advance_checkpoint=False)
    return scanned, saved


async def sync_entity(client, store: Store, entity, initial: int, *, max_catchup: int | None = None):
    """Capture gaps with a safe checkpoint independent of live messages.

    Initial bootstrap fetches at most N newest, then commits oldest-first.
    Subsequent catchup fetches ALL newer ids in ascending order in pages.
    """
    if max_catchup is not None and not (1 <= max_catchup <= 10000):
        raise ValueError("max_catchup must be between 1 and 10000")
    last_id = store.checkpoint(int(entity.id))
    count = 0
    observed_max = last_id
    if last_id == 0:
        # Telethon default is newest first; reverse locally BEFORE checkpointing.
        recent = []
        async for raw in client.iter_messages(entity, limit=initial):
            observed_max = max(observed_max, int(raw.id))
            data = normalise(raw, entity)
            if data:
                recent.append(data)
        recent.sort(key=lambda r: r["message_id"])
        for offset in range(0, len(recent), 100):
            count += store.ingest(int(entity.id), recent[offset:offset + 100])
        # Service-only / empty events must not cause an endless bootstrap scan.
        # This is safe only after ALL readable messages were committed.
        if observed_max > store.checkpoint(int(entity.id)):
            store.advance_to(int(entity.id), observed_max)
        return count
    pending = []
    async for raw in client.iter_messages(entity, min_id=last_id, reverse=True, limit=max_catchup):
        observed_max = max(observed_max, int(raw.id))
        data = normalise(raw, entity)
        if data:
            pending.append(data)
        if len(pending) >= 100:
            count += store.ingest(int(entity.id), pending)
            pending = []
    if pending:
        count += store.ingest(int(entity.id), pending)
    if observed_max > store.checkpoint(int(entity.id)):
        store.advance_to(int(entity.id), observed_max)
    return count


async def resolve_sources(client, requested, known):
    """Retry source resolution after transient startup/network failures.

    `known` is a mapping of username -> Telegram entity, updated in place.
    """
    for username in requested:
        if username in known:
            continue
        try:
            entity = await client.get_entity(username)
            if not hasattr(entity, "id"):
                raise ValueError("not a Telegram entity")
            known[username] = entity
        except Exception as exc:
            log.warning("Could not resolve a source (%s); will retry", type(exc).__name__)
    return {int(e.id): e for e in known.values()}


def _bool_env(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _seconds_env(name, default, *, minimum, maximum):
    value = int(os.getenv(name, str(default)))
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


async def scan_source_dialogs(client, *, include_channels=False):
    """Read only dialog metadata; do not read messages of unapproved sources.

    Raise on any partial Telegram scan, leaving the database untouched.
    """
    sources = []
    entities = {}
    async for dialog in client.iter_dialogs(limit=None):
        info = source_info(dialog, include_channels=include_channels)
        if info is not None:
            sources.append(info)
            entities[info["peer_id"]] = dialog.entity
    return sources, entities


async def scan_group_dialogs(client):
    """Backward-compatible group-only discovery interface for old callers."""
    return await scan_source_dialogs(client, include_channels=False)


def active_entity_map(configured, dialogs, approved, *, blocked=frozenset(), selected_only=False):
    """Apply persisted operator choices to BOTH configured and discovered sources.

    Legacy configured sources keep running by default, except explicitly
    disabled/left sources. Strict mode opts in to an approved-only allowlist.
    """
    combined = {peer_id: entity for peer_id, entity in configured.items()
                if peer_id not in blocked and (not selected_only or peer_id in approved)}
    for peer_id, entity in dialogs.items():
        if peer_id in approved and peer_id not in blocked and peer_id not in combined:
            combined[peer_id] = entity
    return combined


async def run():
    from telethon import TelegramClient, events, utils
    from telethon.errors import FloodWaitError
    from .settings import Settings
    cfg = Settings.from_env()
    if not cfg.telegram_api_id or not cfg.telegram_api_hash:
        raise RuntimeError("TG_API_ID/TG_API_HASH missing")
    session = Path(cfg.telegram_session_path)
    if not Path(str(session) + ".session").exists():
        raise RuntimeError("Telegram session not found; create/copy a session securely first")
    store = Store(cfg.database_url)
    client = TelegramClient(str(session), cfg.telegram_api_id, cfg.telegram_api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        raise RuntimeError("Telegram session is not authorized; do not use interactive login on VPS")
    known = {}
    entities = await resolve_sources(client, cfg.channels, known)
    # Opt-in metadata discovery. Never enroll existing dialogs on first scan.
    discovery_enabled = _bool_env("CLOUD_DISCOVERY_ENABLED", False)
    # Opt-in only: default preserves legacy configured-source monitoring.
    strict_selection = _bool_env("CLOUD_SOURCE_SELECTION_STRICT", False)
    if strict_selection and not discovery_enabled:
        raise RuntimeError("Strict source selection requires CLOUD_DISCOVERY_ENABLED")
    registry = SourceRegistry(cfg.database_url, initialize=True) if discovery_enabled else None
    scan_seconds = _seconds_env("CLOUD_DISCOVERY_SCAN_SECONDS", 600, minimum=120, maximum=86400)
    auto_public = _bool_env("CLOUD_DISCOVERY_AUTO_PUBLIC", True)
    include_channels = _bool_env("CLOUD_DISCOVERY_INCLUDE_CHANNELS", False)
    auto_public_channels = _bool_env("CLOUD_DISCOVERY_AUTO_PUBLIC_CHANNELS", False)
    discovery_history = _seconds_env("CLOUD_DISCOVERY_INITIAL_HISTORY", 100, minimum=1, maximum=1000)
    # Prevent a single huge group backlog from delaying all other sources.
    catchup_limit = _seconds_env("CLOUD_SYNC_MAX_PER_SOURCE", 500, minimum=1, maximum=10000)
    recent_enabled = discovery_enabled and _bool_env("CLOUD_SOURCE_RECENT_ENABLED", False)
    # Comments are collected only from an explicit operator allowlist.
    comments_enabled = _bool_env("CLOUD_COMMENTS_ENABLED", False)
    comments_ids = channel_ids_from_env(os.getenv("CLOUD_COMMENTS_CHANNEL_IDS", "")) if comments_enabled else set()
    comments_seconds = _seconds_env("CLOUD_COMMENTS_SCAN_SECONDS", 600, minimum=120, maximum=86400)
    comments_per_cycle = _seconds_env("CLOUD_COMMENTS_MAX_POSTS_PER_SCAN", 10, minimum=1, maximum=50)
    comments_per_post = _seconds_env("CLOUD_COMMENTS_MAX_PER_POST", 100, minimum=1, maximum=200)
    comments_lookback = _seconds_env("CLOUD_COMMENTS_LOOKBACK_HOURS", 72, minimum=1, maximum=168)
    comments_retention = _seconds_env("CLOUD_COMMENTS_RETENTION_DAYS", 30, minimum=1, maximum=365)
    comment_store = CommentStore(cfg.database_url, initialize=True) if comments_enabled and comments_ids else None
    if comments_enabled and not comments_ids:
        log.warning("Comment collection enabled but no channel IDs allowed; nothing collected")
    last_comment_scan = -1.0
    comment_blocked_until = -1.0
    last_comment_prune = -1.0
    last_scan = -1.0
    dialog_entities = {}
    if not entities and not discovery_enabled:
        raise RuntimeError("No Telegram channels resolved")

    @client.on(events.NewMessage)
    async def live(event):
        try:
            # Filter before reading event payload. New sources added after a
            # transient get_entity failure are accepted without process restart.
            raw_chat_id = getattr(event, "chat_id", None)
            if raw_chat_id is None:
                return
            channel_id, _peer_type = utils.resolve_id(int(raw_chat_id))
            entity = entities.get(int(channel_id))
            if entity is None:
                return
            data = normalise(event.message, entity)
            if data:
                store.ingest(int(entity.id), [data], advance_checkpoint=False)
        except Exception as exc:
            log.error("Live ingestion failed (%s); periodic sync will retry", type(exc).__name__)

    try:
        while True:
            if len(known) < len(cfg.channels):
                await resolve_sources(client, cfg.channels, known)
            static_entities = {int(e.id): e for e in known.values()}

            if registry is not None:
                monotonic = asyncio.get_running_loop().time()
                if last_scan < 0 or monotonic - last_scan >= scan_seconds:
                    try:
                        source_rows, fresh_entities = await scan_source_dialogs(
                            client, include_channels=include_channels)
                        # Persist baseline/changes only after a COMPLETE scan.
                        result = registry.record_scan(
                            source_rows, auto_public=auto_public,
                            auto_public_channels=auto_public_channels)
                        dialog_entities = fresh_entities
                        channels = sum(row["kind"] == "channel" for row in source_rows)
                        log.info("Source discovery: found=%d channels=%d approved=%d first_scan=%s",
                                 result["found"], channels, result["approved"], result["first_scan"])
                    except FloodWaitError as exc:
                        log.warning("Telegram discovery rate limited for %d seconds", exc.seconds)
                        await asyncio.sleep(max(1, exc.seconds))
                    except Exception as exc:
                        log.error("Group discovery failed (%s); keeping previous source list",
                                  type(exc).__name__)
                    finally:
                        last_scan = asyncio.get_running_loop().time()

                try:
                    approved, blocked = registry.selection_ids()
                except Exception as exc:
                    # Fail closed for ALL sources: otherwise a previously disabled
                    # static group could be read during a registry outage.
                    log.error("Source selections unavailable (%s); pausing collection",
                              type(exc).__name__)
                    entities.clear()
                    await asyncio.sleep(cfg.sync_seconds)
                    continue
                active_auto = {k: v for k, v in dialog_entities.items() if k in approved}
            else:
                approved, blocked = set(), set()
                active_auto = {}
            # Mutate in-place so event handlers see edits without a restart.
            # Disabled configured sources are filtered, not just auto-discovered ones.
            entities.clear()
            entities.update(active_entity_map(
                static_entities, active_auto, approved,
                blocked=blocked, selected_only=strict_selection))

            for peer_id, entity in tuple(entities.items()):
                try:
                    history = discovery_history if peer_id in active_auto and peer_id not in static_entities else cfg.initial_history
                    n = await sync_entity(client, store, entity, history, max_catchup=catchup_limit)
                    if n:
                        log.info("Saved %d new messages from channel_id=%d", n, entity.id)
                except FloodWaitError as exc:
                    log.warning("Telegram rate limited; sleeping %d seconds", exc.seconds)
                    await asyncio.sleep(max(1, exc.seconds))
                except Exception as exc:
                    log.error("History sync failed channel_id=%d type=%s", entity.id, type(exc).__name__)
            if recent_enabled and registry is not None:
                # At most two bounded requests each polling cycle; never start
                # a second Telegram session or push directly to OpenAI.
                for _ in range(2):
                    try:
                        job = registry.claim_recent(entities.keys())
                    except Exception as exc:
                        log.error("Recent request claim failed (%s)", type(exc).__name__)
                        break
                    if job is None:
                        break
                    try:
                        scanned, saved = await sync_recent_messages(
                            client, store, entities[job["peer_id"]], job["count"])
                        registry.finish_recent(job["id"], scanned=scanned, saved=saved)
                        log.info("Recent source request completed channel_id=%d scanned=%d queued=%d",
                                 job["peer_id"], scanned, saved)
                    except FloodWaitError as exc:
                        registry.finish_recent(job["id"], error="FloodWaitError")
                        log.warning("Recent request rate limited; cooldown=%d", exc.seconds)
                        await asyncio.sleep(max(1, exc.seconds))
                        break
                    except Exception as exc:
                        # Record only the exception class; no message contents or
                        # personal identifiers are exposed in audit/logs.
                        try:
                            registry.finish_recent(job["id"], error=type(exc).__name__[:40])
                        except Exception:
                            log.error("Recent request failure could not be recorded")
                        log.error("Recent request failed (%s)", type(exc).__name__)
            if comment_store is not None:
                monotonic = asyncio.get_running_loop().time()
                if monotonic >= comment_blocked_until and (last_comment_scan < 0 or monotonic - last_comment_scan >= comments_seconds):
                    try:
                        allowed = {pid: entity for pid, entity in entities.items()
                                   if pid in comments_ids and getattr(entity, "broadcast", False)
                                   and not getattr(entity, "megagroup", False)}
                        posts = comment_store.choose_posts(
                            set(allowed), lookback_hours=comments_lookback,
                            per_cycle=comments_per_cycle, retry_seconds=comments_seconds)
                        total_added = 0
                        for post in posts:
                            pid = post["channel_id"]
                            try:
                                added, count = await collect_thread(
                                    client, allowed[pid], comment_store, post["post_id"],
                                    max_comments=comments_per_post)
                                total_added += added
                                if count >= comments_per_post:
                                    log.warning("Comment sample capped channel_id=%d post_id=%d", pid, post["post_id"])
                            except FloodWaitError as exc:
                                # Do not delay the main collector's other source syncs.
                                comment_blocked_until = asyncio.get_running_loop().time() + max(1, exc.seconds)
                                log.warning("Comment polling rate limited; deferring for %d seconds", exc.seconds)
                                break
                            except Exception as exc:
                                log.warning("Comments unavailable channel_id=%d post_id=%d type=%s",
                                            pid, post["post_id"], type(exc).__name__)
                        log.info("Comment scan: allowed_channels=%d posts_checked=%d saved=%d",
                                 len(allowed), len(posts), total_added)
                        if last_comment_prune < 0 or monotonic - last_comment_prune >= 86400:
                            removed = comment_store.prune(retention_days=comments_retention)
                            log.info("Expired comments removed=%d", removed)
                            last_comment_prune = monotonic
                    except Exception as exc:
                        log.error("Comment collection failed (%s)", type(exc).__name__)
                    finally:
                        last_comment_scan = asyncio.get_running_loop().time()
            await asyncio.sleep(cfg.sync_seconds)
    finally:
        if registry is not None:
            registry.dispose()
        await client.disconnect()


def main():
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(run())

if __name__ == "__main__":
    main()
