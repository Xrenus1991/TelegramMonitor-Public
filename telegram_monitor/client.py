"""
Telegram real-time channel monitor using Telethon (MTProto).
Connects as a user client and streams new messages from configured channels.
Supports text, images, and video with media downloading.
"""

import asyncio
import logging
import re
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from telethon.tl.types import (
    Channel,
    DocumentAttributeAnimated,
    DocumentAttributeVideo,
    MessageMediaDocument,
    MessageMediaPhoto,
)

from telegram_monitor.config import MEDIA_DIR_PATH, MEDIA_EXTENSIONS, SESSION_NAME
from telegram_monitor.utils import sanitize_filename

logger = logging.getLogger(__name__)

# Matches Telethon's internal log: "Sleeping for 23s (0:00:23) on GetHistoryRequest flood wait"
# We intercept this to show a rate-limit countdown in the TUI status bar.
_FLOOD_RE = re.compile(r"Sleeping for (\d+)s .* flood wait")

MEDIA_DIR = Path(MEDIA_DIR_PATH)


@dataclass
class TelegramMessage:
    channel_name: str
    channel_id: int
    text: str
    timestamp: datetime
    message_id: int
    has_media: bool = False
    media_type: str | None = None       # "photo", "video", "gif", "document"
    media_path: str | None = None       # local path to downloaded file
    media_caption: str | None = None    # caption on media if text was empty
    media_size_kb: int = 0


class TelegramMonitor:
    """Connects to Telegram and streams messages from monitored channels."""

    def __init__(self, api_id: int, api_hash: str, channels: list[str],
                 max_per_channel: int = 200, phone: str | None = None,
                 download_media: bool = True, max_media_mb: float = 20.0):
        self.api_id = api_id
        self.api_hash = api_hash
        self.channel_names = channels
        self.max_per_channel = max_per_channel
        self.phone = phone
        self.download_media = download_media
        self.max_media_bytes = int(max_media_mb * 1024 * 1024)

        self.client = TelegramClient(SESSION_NAME, api_id, api_hash)
        self.messages: dict[str, deque[TelegramMessage]] = {}
        self.resolved_channels: dict[int, str] = {}  # id -> title
        self._resolved_entities: dict[int, object] = {}  # exact Telethon entities
        self._seen_ids: set[tuple[int, int]] = set()
        self._seen_order: deque[tuple[int, int]] = deque()
        self._received_total = 0
        self._history_counts: dict[str, int] = {}
        self._message_queue: asyncio.Queue[TelegramMessage] = asyncio.Queue()
        self._running = False
        self._unresolved: list[str] = []
        self._media_count = 0
        self._start_time: datetime | None = None
        self._rate_limit_until: float = 0  # unix timestamp when flood wait expires
        self._rate_limit_request: str = ""  # which request triggered the wait

        # Intercept Telethon's flood wait log messages
        self._install_flood_handler()

        if download_media:
            MEDIA_DIR.mkdir(parents=True, exist_ok=True)

    def _install_flood_handler(self):
        """Attach a logging handler to Telethon's logger to detect flood waits."""
        monitor = self

        class _FloodHandler(logging.Handler):
            def emit(self, record):
                msg = record.getMessage()
                m = _FLOOD_RE.search(msg)
                if m:
                    seconds = int(m.group(1))
                    monitor._rate_limit_until = time.time() + seconds
                    # Extract request name (e.g. "GetHistoryRequest")
                    req_match = re.search(r"on (\w+)", msg)
                    monitor._rate_limit_request = req_match.group(1) if req_match else ""

        telethon_logger = logging.getLogger("telethon")
        self._flood_handler = _FloodHandler()
        telethon_logger.addHandler(self._flood_handler)

    def _classify_media(self, media) -> tuple[str | None, int]:
        """Classify media type and get size estimate."""
        if isinstance(media, MessageMediaPhoto):
            return "photo", 0  # photo sizes aren't easily available pre-download

        if isinstance(media, MessageMediaDocument) and media.document:
            doc = media.document
            size = doc.size or 0
            for attr in doc.attributes:
                if isinstance(attr, DocumentAttributeAnimated):
                    return "gif", size
                if isinstance(attr, DocumentAttributeVideo):
                    return "video", size
            mime = doc.mime_type or ""
            if mime.startswith("image/"):
                return "photo", size
            if mime.startswith("video/"):
                return "video", size
            return "document", size

        return None, 0

    async def _download_media(self, event, media_type: str, channel_name: str) -> str | None:
        """Download media and return the actual path provided by Telethon."""
        if not self.download_media:
            return None
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_name = sanitize_filename(channel_name)
            ext = MEDIA_EXTENSIONS.get(media_type, "")
            channel_id = abs(int(event.chat_id))
            filename = f"{ts}_{channel_id}_{safe_name}_{event.message.id}{ext}"
            path = MEDIA_DIR / filename

            downloaded = await asyncio.wait_for(
                event.message.download_media(file=str(path)), timeout=30.0
            )
            # Telethon may add an extension or return None for a skipped file.
            actual = Path(downloaded) if isinstance(downloaded, (str, Path)) else None
            if actual is None or not actual.is_file():
                logger.warning("Media download returned no usable local file")
                return None
            self._media_count += 1
            return str(actual)
        except asyncio.TimeoutError:
            logger.warning("Media download timed out (30s)")
            return None
        except Exception as e:
            logger.warning("Media download failed (%s)", type(e).__name__)
            return None

    async def _download_history_media(self, raw_msg, media_type: str,
                                      channel_name: str, channel_id: int = 0) -> str | None:
        """Download media from a historical message. Skips if file exists."""
        if not self.download_media:
            return None
        try:
            ts = raw_msg.date.strftime("%Y%m%d_%H%M%S")
            safe_name = sanitize_filename(channel_name)
            ext = MEDIA_EXTENSIONS.get(media_type, "")
            filename = f"{ts}_{channel_id}_{safe_name}_{raw_msg.id}{ext}"
            path = MEDIA_DIR / filename
            if path.is_file():
                return str(path)
            downloaded = await asyncio.wait_for(
                raw_msg.download_media(file=str(path)), timeout=30.0
            )
            actual = Path(downloaded) if isinstance(downloaded, (str, Path)) else None
            if actual is None or not actual.is_file():
                logger.warning("History media download returned no usable local file")
                return None
            self._media_count += 1
            return str(actual)
        except asyncio.TimeoutError:
            logger.warning("History media download timed out (30s)")
            return None
        except Exception as e:
            logger.warning("History media download failed (%s)", type(e).__name__)
            return None

    @staticmethod
    def _utc_timestamp(date: datetime) -> datetime:
        """Do not shift the absolute instant of a timezone-aware timestamp."""
        if date.tzinfo is None:
            return date.replace(tzinfo=timezone.utc)
        return date.astimezone(timezone.utc)

    @staticmethod
    def _build_display_text(text: str, media_type: str | None) -> str | None:
        """Build display text with media indicator prefix. Returns None if empty."""
        display_text = text
        if not display_text and media_type:
            display_text = f"[{media_type.upper()}]"
        elif media_type and display_text:
            display_text = f"[{media_type.upper()}] {display_text}"
        return display_text or None

    async def start(self) -> None:
        """Authenticate and start listening."""
        await self.client.start(phone=self.phone)
        me = await self.client.get_me()
        logger.info("Telegram session authenticated")
        self._start_time = datetime.now(timezone.utc)

        # Resolve channels — use session cache first, then API with rate-limit guard
        resolved_entities = []
        need_api = []

        # Phase 1: resolve from session cache (no API calls)
        for ch in self.channel_names:
            try:
                input_entity = await self.client.get_input_entity(ch)
                entity = await self.client.get_entity(input_entity)
                if isinstance(entity, Channel):
                    self.resolved_channels[entity.id] = entity.title
                    self._resolved_entities[entity.id] = entity
                    self.messages[entity.title] = deque(maxlen=self.max_per_channel)
                    resolved_entities.append(entity)
                    logger.info(f"  Cached: {entity.title}")
                else:
                    logger.warning(f"  {ch} is not a channel, skipping")
            except (ValueError, KeyError):
                need_api.append(ch)
            except FloodWaitError as e:
                need_api.append(ch)
                logger.warning(f"  Cache miss + flood wait ({e.seconds}s) for '{ch}'")
            except Exception:
                need_api.append(ch)

        # Phase 2: resolve uncached channels via API with delay between calls
        if need_api:
            logger.info(f"  Resolving {len(need_api)} uncached channels via API...")
            for ch in need_api:
                try:
                    entity = await self.client.get_entity(ch)
                    if isinstance(entity, Channel):
                        self.resolved_channels[entity.id] = entity.title
                        self._resolved_entities[entity.id] = entity
                        self.messages[entity.title] = deque(maxlen=self.max_per_channel)
                        resolved_entities.append(entity)
                        logger.info(f"  Resolved: {entity.title}")
                    else:
                        logger.warning(f"  {ch} is not a channel, skipping")
                    await asyncio.sleep(0.5)  # rate-limit guard
                except FloodWaitError as e:
                    self._unresolved.append(ch)
                    # All remaining channels will hit the same flood wait — skip them
                    remaining = need_api[need_api.index(ch) + 1:]
                    self._unresolved.extend(remaining)
                    wait_min = e.seconds // 60
                    logger.warning(
                        f"  Flood wait: {e.seconds}s ({wait_min}m). "
                        f"Skipping {len(remaining) + 1} uncached channels."
                    )
                    break
                except Exception as e:
                    self._unresolved.append(ch)
                    logger.warning("  Could not resolve channel (%s)", type(e).__name__)

        if not resolved_entities:
            raise RuntimeError(
                "No channels could be resolved. "
                "You may be rate-limited by Telegram — wait and try again, "
                "or run join_channels.py first to populate the session cache."
            )

        # Register event handler for new messages in resolved channels
        @self.client.on(events.NewMessage(chats=resolved_entities))
        async def on_new_message(event):
            chat = await event.get_chat()
            channel_title = getattr(chat, "title", str(event.chat_id))
            channel_id = int(getattr(chat, "id", event.chat_id))
            # Avoid duplicate downloads across history and live events.
            if (channel_id, int(event.message.id)) in self._seen_ids:
                return

            text = event.raw_text or ""
            media = event.message.media
            media_type, media_size = self._classify_media(media) if media else (None, 0)
            media_path = None

            # Download media (photos always, videos only if under size limit)
            if media_type and self.download_media:
                if media_type == "photo" or media_size <= self.max_media_bytes:
                    media_path = await self._download_media(event, media_type, channel_title)

            display_text = self._build_display_text(text, media_type)
            if not display_text:
                return  # no text and no recognized media

            msg = TelegramMessage(
                channel_name=channel_title,
                # Telethon event.chat_id is often -100... while history uses
                # positive Channel.id; normalize to the resolved chat entity id.
                channel_id=channel_id,
                text=display_text,
                timestamp=self._utc_timestamp(event.message.date),
                message_id=event.message.id,
                has_media=media_type is not None,
                media_type=media_type,
                media_path=media_path,
                media_caption=event.raw_text if media_type and event.raw_text else None,
                media_size_kb=media_size // 1024,
            )

            if not self._mark_seen(msg):
                return
            if channel_title in self.messages:
                self.messages[channel_title].append(msg)
            self._received_total += 1
            await self._message_queue.put(msg)

        self._running = True
        logger.info(f"Monitoring {len(resolved_entities)} channels. Waiting for messages...")

    def _mark_seen(self, msg: TelegramMessage) -> bool:
        """Deduplicate overlap between live events and initial history."""
        key = (msg.channel_id, msg.message_id)
        if key in self._seen_ids:
            return False
        if len(self._seen_order) >= 10000:
            removed = self._seen_order.popleft()
            self._seen_ids.discard(removed)
        self._seen_order.append(key)
        self._seen_ids.add(key)
        return True

    async def get_new_messages(self, timeout: float = 1.0, max_items: int = 200) -> list[TelegramMessage]:
        """Drain a bounded number; avoid freezing the UI during message bursts."""
        max_items = max(1, min(10000, int(max_items)))
        messages = []
        try:
            msg = await asyncio.wait_for(self._message_queue.get(), timeout=timeout)
            messages.append(msg)
            while len(messages) < max_items and not self._message_queue.empty():
                messages.append(self._message_queue.get_nowait())
        except asyncio.TimeoutError:
            pass
        return messages

    @property
    def status(self) -> dict:
        uptime = ""
        if self._start_time:
            delta = datetime.now(timezone.utc) - self._start_time
            hours, remainder = divmod(int(delta.total_seconds()), 3600)
            minutes, seconds = divmod(remainder, 60)
            uptime = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

        # Rate limit countdown
        rate_limit_remaining = max(0, int(self._rate_limit_until - time.time()))

        return {
            "connected": self._running,
            "channels_active": len(self.resolved_channels),
            "channels_failed": self._unresolved,
            "total_messages": sum(len(q) for q in self.messages.values()),
            "live_messages_received": self._received_total,
            "history_counts": dict(self._history_counts),
            "channel_counts": {name: len(q) for name, q in self.messages.items()},
            "media_downloaded": self._media_count,
            "uptime": uptime,
            "rate_limited": rate_limit_remaining > 0,
            "rate_limit_seconds": rate_limit_remaining,
            "rate_limit_request": self._rate_limit_request if rate_limit_remaining > 0 else "",
        }

    async def fetch_history(self, per_channel: int = 5) -> list[TelegramMessage]:
        """Fetch recent messages from all monitored channels.
        Call after start() to populate the feed with existing messages.
        Downloads media for messages that have it."""
        all_msgs = []
        per_channel = max(1, min(int(per_channel), 200))
        for ch_id, ch_title in self.resolved_channels.items():
            channel_count = 0
            try:
                entity = self._resolved_entities.get(ch_id, ch_id)
                async for raw_msg in self.client.iter_messages(entity, limit=per_channel):
                    if not raw_msg or (not raw_msg.text and not raw_msg.media):
                        continue

                    text = raw_msg.text or ""
                    media = raw_msg.media
                    media_type, media_size = self._classify_media(media) if media else (None, 0)

                    # Download media (photos always, videos under size limit)
                    media_path = None
                    if media_type and self.download_media:
                        if media_type == "photo" or media_size <= self.max_media_bytes:
                            media_path = await self._download_history_media(
                                raw_msg, media_type, ch_title, ch_id)

                    display_text = self._build_display_text(text, media_type)
                    if not display_text:
                        continue

                    msg = TelegramMessage(
                        channel_name=ch_title,
                        channel_id=ch_id,
                        text=display_text,
                        timestamp=self._utc_timestamp(raw_msg.date),
                        message_id=raw_msg.id,
                        has_media=media_type is not None,
                        media_type=media_type,
                        media_path=media_path,
                        media_caption=raw_msg.text if media_type and raw_msg.text else None,
                        media_size_kb=media_size // 1024,
                    )

                    if not self._mark_seen(msg):
                        continue
                    if ch_title in self.messages:
                        self.messages[ch_title].append(msg)
                    all_msgs.append(msg)
                    channel_count += 1
            except Exception as e:
                logger.warning("Failed to fetch history for %s (%s)", ch_title, type(e).__name__)
            finally:
                self._history_counts[ch_title] = channel_count
                logger.info("History %s: %d/%d messages", ch_title, channel_count, per_channel)

        all_msgs.sort(key=lambda m: m.timestamp, reverse=True)
        logger.info(f"Fetched {len(all_msgs)} historical messages from {len(self.resolved_channels)} channels")
        return all_msgs

    async def stop(self) -> None:
        self._running = False
        if getattr(self, "_flood_handler", None) is not None:
            logging.getLogger("telethon").removeHandler(self._flood_handler)
            self._flood_handler = None
        try:
            await asyncio.wait_for(self.client.disconnect(), timeout=5.0)
        except (Exception, asyncio.CancelledError):
            pass
