"""
Multi-pane Textual TUI for the Telegram monitor.

Layout:
┌──────────────────────────┬──────────────────┐
│  LIVE FEED (scrolls)     │  MEDIA WALL      │
│                          │  (persists)      │
├──────────────────────────┴──────────────────┤
│  INTELLIGENCE BRIEFING (scrolls)            │
├─────────────────────────────────────────────┤
│  STATUS BAR                                 │
└─────────────────────────────────────────────┘
"""

import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from rich.markup import escape
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.events import Key
from textual.widgets import Footer, RichLog, Static

from telegram_monitor.config import (
    APP_TITLE,
    BRIEFING_MAX_LINES,
    BRIEFING_TEXT_TRUNCATE,
    CAPTION_TRUNCATE,
    CHANNEL_NAME_TRUNCATE,
    DISPLAY_LABEL_CONTRADICTIONS,
    DISPLAY_LABEL_KEY_ACTORS,
    DISPLAY_LABEL_MEDIA_REFS,
    DISPLAY_LABEL_RHETORIC,
    DISPLAY_LABEL_SOURCES,
    DISPLAY_LABEL_SUMMARY,
    DISPLAY_LABEL_TIMELINE,
    DISPLAY_LABEL_TOPIC_ANALYSIS,
    DISPLAY_LABEL_TRANSLATIONS,
    FEED_MAX_LINES,
    HISTORY_CHUNK_SIZE,
    HISTORY_PER_CHANNEL,
    INITIAL_ANALYSIS_MAX_MESSAGES,
    ANALYSIS_RETRY_SECONDS,
    MAX_BATCH_MULTIPLIER,
    MEDIA_DISPLAY_COLORS,
    MEDIA_MAX_LINES,
    MSG_ANALYSIS_TRIGGER,
    MSG_ANALYZING,
    MSG_COLLECTING,
    MSG_COPIED,
    MSG_GENERATING_BRIEFING,
    MSG_LOADING_HISTORY,
    MSG_NO_BRIEFING,
    MSG_NO_MEDIA,
    MSG_NO_RECENT,
    MSG_NO_RESULT,
    MSG_TRANSLATING,
    MSG_WAITING_MEDIA,
    PANEL_TITLE_BRIEFING,
    PANEL_TITLE_FEED,
    PANEL_TITLE_MEDIA,
    STANCE_COLOR_RULES,
    STANCE_DEFAULT_COLOR,
    SUMMARY_TRUNCATE,
    TEXT_TRUNCATE,
    URGENCY_RICH_COLORS,
)
from telegram_monitor.iterm_images import can_display_inline, display_image, display_video_thumbnail
from telegram_monitor.pending_store import enabled_pending_store
from telegram_monitor.utils import strip_media_prefix

logger = logging.getLogger(__name__)

# ─── iTerm2 native image detection ──────────────────────────────────────────
_ITERM2 = can_display_inline()

CSS = """
Screen {
    layout: grid;
    grid-size: 2 3;
    grid-columns: 2fr 3fr;
    grid-rows: 1fr 1fr auto;
}

#feed {
    column-span: 1;
    row-span: 1;
    border: solid $primary;
    height: 100%;
    overflow-y: auto;
}

#media {
    column-span: 1;
    row-span: 1;
    border: solid $secondary;
    height: 100%;
    overflow-y: auto;
}

#briefing {
    column-span: 2;
    row-span: 1;
    border: solid $success;
    height: 100%;
    overflow-y: auto;
}

#status-bar {
    column-span: 2;
    row-span: 1;
    height: 3;
    border: solid $warning;
    content-align: left middle;
    padding: 0 1;
}

RichLog {
    scrollbar-size: 1 1;
    padding: 0 1;
}
"""


class FeedPanel(RichLog):
    """Live message feed — auto-scrolls, keeps history."""

    BORDER_TITLE = PANEL_TITLE_FEED

    def add_message(self, msg):
        ts = msg.timestamp.strftime("%H:%M:%S")
        ch = msg.channel_name[:CHANNEL_NAME_TRUNCATE].ljust(CHANNEL_NAME_TRUNCATE)
        text = msg.text.replace("\n", " ")[:TEXT_TRUNCATE]

        media_tag = ""
        if msg.has_media and msg.media_type:
            color = MEDIA_DISPLAY_COLORS.get(msg.media_type, "dim")
            media_tag = f"[{color}][{msg.media_type.upper()}][/{color}] "

        line = (
            f"[dim]{ts}[/dim]  "
            f"{media_tag}"
            f"[cyan]{escape(ch)}[/cyan]  "
            f"{escape(text)}"
        )
        self.write(Text.from_markup(line))


class MediaPanel(RichLog):
    """Media wall — images/videos persist with descriptions."""

    BORDER_TITLE = PANEL_TITLE_MEDIA

    async def add_media(self, msg, translated_caption: str = ""):
        ts = msg.timestamp.strftime("%H:%M:%S")
        ch = msg.channel_name[:CHANNEL_NAME_TRUNCATE]
        mt = (msg.media_type or "file").upper()

        # Use translated caption if provided, otherwise fall back to original
        caption = translated_caption
        if not caption:
            if msg.media_caption:
                caption = msg.media_caption.replace("\n", " ")
            elif msg.text:
                caption = strip_media_prefix(msg.text).replace("\n", " ")

        color = MEDIA_DISPLAY_COLORS.get(mt.lower(), "dim")

        header = (
            f"[dim]{ts}[/dim] [{color}]{mt}[/{color}]  "
            f"[cyan]{escape(ch)}[/cyan]"
        )
        self.write(Text.from_markup(header))

        if caption:
            self.write(Text.from_markup(f"  [bold white]{escape(caption[:CAPTION_TRUNCATE])}[/bold white]"))

        # Show media metadata + clickable link (no text-based image rendering)
        if msg.media_path and Path(msg.media_path).exists():
            abs_path = str(Path(msg.media_path).resolve())
            file_url = Path(abs_path).as_uri()
            size_kb = Path(msg.media_path).stat().st_size // 1024
            hint = "  [dim]press [bold]v[/bold] to view[/dim]" if _ITERM2 else ""
            self.write(Text.from_markup(
                f"  [dim italic]{escape(Path(msg.media_path).name)}  ({size_kb}KB)[/dim italic]  "
                f"[link={file_url}][bold underline blue]Open File[/bold underline blue][/link]"
                f"{hint}"
            ))
        self.write(Text(""))


class BriefingPanel(RichLog):
    """Intelligence briefing — accumulates analysis over time."""

    BORDER_TITLE = PANEL_TITLE_BRIEFING

    def add_analysis(self, analysis):
        ts = analysis.timestamp.strftime("%H:%M:%S UTC")
        n = analysis.msg_count

        # Safety cap — the bidi markers that caused the hang are gone,
        # but keep a generous upper bound just in case.
        def _t(text: str, limit: int = BRIEFING_TEXT_TRUNCATE) -> str:
            text = text.replace("\n", " ")
            return text[:limit] if len(text) > limit else text

        uc = URGENCY_RICH_COLORS.get(analysis.urgency, "white on green")
        self.write(Text(""))
        self.write(Text.from_markup(
            f"[{uc}] ◆ {analysis.urgency} [/{uc}]  "
            f"[dim]{ts} · {n} messages analyzed[/dim]"
        ))
        if analysis.urgency_reason:
            self.write(Text.from_markup(
                f"  [dim]{escape(_t(analysis.urgency_reason))}[/dim]"
            ))

        self.write(Text(""))
        self.write(Text.from_markup(f"[bold green]{DISPLAY_LABEL_SUMMARY}[/bold green]"))
        self.write(Text.from_markup(f"  {escape(_t(analysis.summary, SUMMARY_TRUNCATE))}"))

        if analysis.timeline:
            self.write(Text(""))
            self.write(Text.from_markup(f"[bold blue]{DISPLAY_LABEL_TIMELINE}[/bold blue]"))
            for i, evt in enumerate(analysis.timeline[:6]):
                t = escape(_t(str(evt.get("time") or "     ")))
                is_last = (i == min(len(analysis.timeline), 6) - 1)
                branch = "└─" if is_last else "├─"
                self.write(Text.from_markup(
                    f"  [blue]{branch}[/blue] [bold]{t}[/bold]  "
                    f"{escape(_t(evt['event']))}"
                ))

        if analysis.key_actors:
            self.write(Text(""))
            self.write(Text.from_markup(f"[bold magenta]{DISPLAY_LABEL_KEY_ACTORS}[/bold magenta]"))
            for a in analysis.key_actors[:5]:
                name = a.get("name", "?")
                role = a.get("role", "")
                ctx = _t(a.get("context", ""), 80)
                sent = a.get("sentiment", "neutral").lower()
                dot = "[red]●[/red]" if "neg" in sent else "[green]●[/green]" if "pos" in sent else "[dim]●[/dim]"
                self.write(Text.from_markup(
                    f"  {dot} [bold]{escape(name[:30])}[/bold]  "
                    f"[dim]{escape(role[:30])}[/dim]  "
                    f"{escape(ctx)}"
                ))

        if analysis.source_breakdown:
            self.write(Text(""))
            self.write(Text.from_markup(f"[bold cyan]{DISPLAY_LABEL_SOURCES}[/bold cyan]"))
            max_c = max(analysis.source_breakdown.values(), default=1)
            for ch_name, count in sorted(
                    analysis.source_breakdown.items(),
                    key=lambda x: x[1], reverse=True)[:6]:
                stance = analysis.sentiment_map.get(ch_name, "")
                bar_w = 15
                filled = min(int((count / max_c) * bar_w), bar_w) if max_c else 0
                bar = "█" * filled + "░" * (bar_w - filled)

                s = stance.lower()
                icon_color = STANCE_DEFAULT_COLOR
                for keywords, color in STANCE_COLOR_RULES:
                    if any(w in s for w in keywords):
                        icon_color = color
                        break
                icon = f"[{icon_color}]■[/{icon_color}]"

                lbl = ch_name[:16].ljust(16)
                st = stance[:24] if stance else ""
                self.write(Text.from_markup(
                    f"  {icon} [cyan]{escape(lbl)}[/cyan] {bar} "
                    f"[dim]{count:>2}  {escape(st)}[/dim]"
                ))

        if analysis.rhetoric_shifts:
            self.write(Text(""))
            self.write(Text.from_markup(f"[bold yellow]{DISPLAY_LABEL_RHETORIC}[/bold yellow]"))
            for s in analysis.rhetoric_shifts[:3]:
                self.write(Text.from_markup(f"  [yellow]→[/yellow] {escape(_t(s))}"))

        if analysis.contradictions:
            self.write(Text(""))
            self.write(Text.from_markup(f"[bold red]{DISPLAY_LABEL_CONTRADICTIONS}[/bold red]"))
            for c in analysis.contradictions[:2]:
                self.write(Text.from_markup(f"  [red]⚡[/red] {escape(_t(c))}"))

        if analysis.translations:
            self.write(Text(""))
            n_trans = len(analysis.translations)
            self.write(Text.from_markup(
                f"[bold blue]{DISPLAY_LABEL_TRANSLATIONS}[/bold blue] [dim]({n_trans})[/dim]"
            ))
            for t in analysis.translations[:8]:
                raw = t.get("raw", "")
                self.write(Text.from_markup(f"  {escape(_t(raw))}"))
            if n_trans > 8:
                self.write(Text.from_markup(f"  [dim]... +{n_trans - 8} more[/dim]"))

        if analysis.topic_summaries:
            self.write(Text(""))
            self.write(Text.from_markup(f"[bold green]{DISPLAY_LABEL_TOPIC_ANALYSIS}[/bold green]"))
            for topic, summary in list(analysis.topic_summaries.items())[:3]:
                self.write(Text.from_markup(f"  [bold magenta]▸ {escape(_t(topic, 60))}[/bold magenta]"))
                self.write(Text.from_markup(f"    {escape(_t(summary))}"))

        if analysis.media_mentions:
            self.write(Text(""))
            self.write(Text.from_markup(
                f"[bold magenta]{DISPLAY_LABEL_MEDIA_REFS}[/bold magenta] [dim]({len(analysis.media_mentions)})[/dim]"
            ))
            for mm in analysis.media_mentions[:3]:
                self.write(Text.from_markup(
                    f"  [yellow]▸[/yellow] {escape(_t(mm.get('raw', '')))}"
                ))

        self.write(Text(""))
        self.write(Text.from_markup("[dim]─────────────────────────────────────────[/dim]"))


class StatusBar(Static):
    """Bottom status bar showing live stats."""

    def update_status(self, status: dict, analysis=None):
        n_ch = status["channels_active"]
        n_msg = status["total_messages"]
        n_media = status.get("media_downloaded", 0)
        uptime = status.get("uptime", "00:00:00")
        n_fail = len(status.get("channels_failed", []))
        now = datetime.now(timezone.utc).strftime("%H:%M:%S")

        urgency = "NORMAL"
        if analysis:
            urgency = analysis.urgency

        uc = URGENCY_RICH_COLORS.get(urgency, "white on green")

        parts = [
            f"[{uc}] {urgency} [/{uc}]",
            f"[dim]{now} UTC[/dim]",
            f"[cyan]{n_ch}[/cyan] ch",
            f"[yellow]{n_msg}[/yellow] cached",
            f"[yellow]{status.get('live_messages_received', 0)}[/yellow] live",
            f"[magenta]{n_media}[/magenta] media",
            f"[dim]up:{uptime}[/dim]",
        ]
        if n_fail:
            parts.append(f"[red]{n_fail} failed[/red]")

        if status.get("rate_limited"):
            secs = status["rate_limit_seconds"]
            req = status.get("rate_limit_request", "")
            label = f" {req}" if req else ""
            parts.append(f"[bold white on red] RATE LIMITED{label} {secs}s [/bold white on red]")

        self.update(Text.from_markup("  │  ".join(parts)))


class MonitorApp(App):
    """Main Textual TUI application."""

    CSS = CSS
    TITLE = APP_TITLE
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("f", "toggle_feed", "Feed"),
        ("b", "toggle_briefing", "Briefing"),
        ("m", "toggle_media", "Media"),
        ("c", "copy_briefing", "Copy"),
        ("v", "view_media", "View Media"),
    ]

    def __init__(self, telegram_monitor, analyzer, config):
        super().__init__()
        self.telegram_monitor = telegram_monitor
        self.analyzer = analyzer
        self.config = config
        self._pending_store = enabled_pending_store()
        self._pending_messages = self._pending_store.load() if self._pending_store else []
        if self._pending_messages:
            logger.info("Restored %d pending messages for analysis", len(self._pending_messages))
        self._last_summary_time = 0
        self._media_paths: list[str] = []
        self._analysis_running = False
        self._retry_at = 0.0
        self._ack_waiting = []  # successful analyses awaiting SQLite acknowledgement

    def compose(self) -> ComposeResult:
        yield FeedPanel(id="feed", highlight=False, markup=True, max_lines=FEED_MAX_LINES, wrap=False)
        yield MediaPanel(id="media", highlight=False, markup=True, max_lines=MEDIA_MAX_LINES, wrap=False)
        yield BriefingPanel(id="briefing", highlight=False, markup=True, max_lines=BRIEFING_MAX_LINES, wrap=True)
        yield StatusBar(id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        # Show initial content so panels aren't blank
        n_ch = self.telegram_monitor.status["channels_active"]
        n_fail = len(self.telegram_monitor.status.get("channels_failed", []))

        feed = self.query_one("#feed", FeedPanel)
        feed.write(Text.from_markup(
            f"[bold green]◉ Connected[/bold green]  "
            f"Monitoring [cyan]{n_ch}[/cyan] channels"
            + (f"  [red]({n_fail} failed)[/red]" if n_fail else "")
        ))
        feed.write(Text.from_markup(f"[dim]{MSG_LOADING_HISTORY}[/dim]"))

        briefing = self.query_one("#briefing", BriefingPanel)
        briefing.write(Text.from_markup(
            f"[dim]{MSG_COLLECTING}[/dim]"
        ))
        briefing.write(Text.from_markup(
            f"[dim]{MSG_ANALYSIS_TRIGGER.format(batch_size=self.config['batch_size'], interval=self.config['interval'])}[/dim]"
        ))

        media = self.query_one("#media", MediaPanel)
        media.write(Text.from_markup(f"[dim]{MSG_WAITING_MEDIA}[/dim]"))

        # Update status bar immediately
        status_bar = self.query_one("#status-bar", StatusBar)
        status_bar.update_status(self.telegram_monitor.status)

        # Start background workers
        self.load_history()
        self.poll_messages()
        self.update_status_bar()

    async def _translate_media_captions(self, messages: list) -> dict[tuple[int, int], str]:
        """Batch-translate media captions. Keys include channel id to avoid collisions."""
        media_msgs = [m for m in messages if m.has_media and m.media_type]
        if not media_msgs:
            return {}

        # Gather captions to translate
        captions = []
        msg_ids = []
        for m in media_msgs:
            raw = m.media_caption or ""
            if not raw and m.text:
                raw = strip_media_prefix(m.text)
            raw = raw.replace("\n", " ").strip()
            if raw:
                captions.append(raw)
                msg_ids.append((m.channel_id, m.message_id))

        if not captions:
            return {}

        try:
            translations = await self.analyzer.translate_captions(captions)
            return dict(zip(msg_ids, translations))
        except Exception as e:
            logger.error("Caption translation failed (%s)", type(e).__name__)
            return {}

    def _enqueue_messages(self, messages: list) -> None:
        """Persist before analysis when explicitly opted in; avoid recovered duplicates."""
        if self._pending_store is None:
            self._pending_messages.extend(messages)
            return
        for msg in messages:
            try:
                if self._pending_store.add(msg):
                    self._pending_messages.append(msg)
            except Exception:
                # Keep processing in memory but make degraded crash recovery visible.
                logger.exception("Pending-message persistence failed; memory-only fallback")
                self._pending_messages.append(msg)

    @work(exclusive=True, group="history")
    async def load_history(self) -> None:
        """Fetch recent messages from channels and run first briefing."""
        feed = self.query_one("#feed", FeedPanel)
        media = self.query_one("#media", MediaPanel)
        briefing = self.query_one("#briefing", BriefingPanel)
        try:
            logger.info("[load_history] fetching history...")
            history = await self.telegram_monitor.fetch_history(per_channel=HISTORY_PER_CHANNEL)
            if history:
                logger.info(f"[load_history] got {len(history)} messages")
                # Persist messages before any slower translation or UI rendering.
                # Otherwise cancellation during media processing can lose them.
                analyze_all = os.getenv("ANALYZE_ALL_HISTORY", "0").strip() == "1"
                candidates = history if analyze_all else history[:INITIAL_ANALYSIS_MAX_MESSAGES]
                self._enqueue_messages(candidates)
                logger.info("[load_history] fetched=%d; queued=%d; pending=%d",
                            len(history), len(candidates), len(self._pending_messages))
                # Batch-translate media captions
                media_msgs = [m for m in history if m.has_media and m.media_type]
                caption_map = {}
                if media_msgs:
                    media.write(Text.from_markup(
                        f"[dim]{MSG_TRANSLATING}[/dim]"
                    ))
                    logger.info(f"[load_history] translating {len(media_msgs)} captions...")
                    caption_map = await self._translate_media_captions(history)
                    logger.info(f"[load_history] translation done, got {len(caption_map)} results")

                feed.write(Text.from_markup(
                    f"\n[bold yellow]─── {len(history)} recent messages ───[/bold yellow]"
                ))

                # Write in small chunks with real pauses to keep UI responsive.
                # Each write() triggers a Textual repaint; batching prevents O(n^2)
                # rendering on the growing RichLog.
                for i in range(0, len(history), HISTORY_CHUNK_SIZE):
                    chunk = history[i:i + HISTORY_CHUNK_SIZE]
                    for msg in chunk:
                        feed.add_message(msg)
                        if msg.has_media and msg.media_type:
                            translated = caption_map.get((msg.channel_id, msg.message_id), "")
                            await media.add_media(msg, translated_caption=translated)
                            if msg.media_path:
                                self._media_paths.append(msg.media_path)
                    # Real pause between chunks so Textual can render + process input
                    await asyncio.sleep(0.05)
                    logger.info(f"[load_history] wrote chunk {i//HISTORY_CHUNK_SIZE + 1}/{(len(history) + HISTORY_CHUNK_SIZE - 1)//HISTORY_CHUNK_SIZE}")

                feed.write(Text.from_markup(
                    "[bold yellow]─── live feed ───[/bold yellow]\n"
                ))

                # Explicit opt-in can enqueue *all* fetched history. Otherwise
                # retain the established capped initial briefing to limit API use.
                self._last_summary_time = time.time()
                if not self._analysis_running and self._pending_messages:
                    batch = self._pending_messages[:self.config["batch_size"] * MAX_BATCH_MULTIPLIER]
                    briefing.write(Text.from_markup(
                        f"[bold yellow]{MSG_GENERATING_BRIEFING.format(n=len(batch))}[/bold yellow]"
                    ))
                    if self._start_analysis(batch):
                        del self._pending_messages[:len(batch)]
                logger.info("[load_history] initial analysis scheduled")
            else:
                feed.write(Text.from_markup(f"[dim]{MSG_NO_RECENT}[/dim]"))
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.error("Failed to load history (%s)", type(e).__name__)
            feed.write(Text.from_markup(f"[red]History load error: {escape(str(e))}[/red]"))

    @work(exclusive=True, group="poller")
    async def poll_messages(self) -> None:
        """Poll for new Telegram messages and feed them to panels."""
        feed = self.query_one("#feed", FeedPanel)
        media = self.query_one("#media", MediaPanel)
        self._last_summary_time = time.time()

        try:
            while True:
                self._retry_acknowledgements()
                try:
                    msgs = await self.telegram_monitor.get_new_messages(timeout=0.5)
                except asyncio.CancelledError:
                    return
                except Exception as e:
                    logger.error("get_new_messages error (%s)", type(e).__name__)
                    await asyncio.sleep(2)
                    continue

                # Record live messages before optional cloud caption translation.
                # An API timeout must not discard the analysis queue.
                if msgs:
                    self._enqueue_messages(msgs)

                # Translate media captions for this batch
                caption_map = {}
                media_in_batch = [m for m in msgs if m.has_media and m.media_type]
                if media_in_batch:
                    caption_map = await self._translate_media_captions(msgs)

                for i, msg in enumerate(msgs):
                    feed.add_message(msg)
                    if msg.has_media and msg.media_type:
                        translated = caption_map.get((msg.channel_id, msg.message_id), "")
                        await media.add_media(msg, translated_caption=translated)
                        if msg.media_path:
                            self._media_paths.append(msg.media_path)
                    # Yield every 5 messages so Textual can process input
                    if i % 5 == 4:
                        await asyncio.sleep(0.01)

                now = time.time()
                batch_size = self.config["batch_size"]
                interval = self.config["interval"]

                should_analyze = (
                    not self._analysis_running and now >= self._retry_at
                    and (
                        len(self._pending_messages) >= batch_size or
                        (self._pending_messages and now - self._last_summary_time >= interval)
                    )
                )
                if should_analyze:
                    batch = self._pending_messages[:batch_size * MAX_BATCH_MULTIPLIER]
                    if self._start_analysis(batch):
                        del self._pending_messages[:len(batch)]
                        self._last_summary_time = now
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.error("poll_messages crashed (%s)", type(e).__name__)
            feed.write(Text.from_markup(f"[red]Poller error: {escape(str(e))}[/red]"))

    def _retry_acknowledgements(self) -> None:
        """Retry failed DB acknowledgements without repeating paid model calls."""
        if not self._pending_store or not self._ack_waiting:
            return
        try:
            self._pending_store.ack(self._ack_waiting)
        except Exception:
            logger.warning("Pending queue acknowledgement still unavailable")
        else:
            self._ack_waiting.clear()

    def _start_analysis(self, messages: list) -> bool:
        """Reserve the worker synchronously so subsequent polls cannot cancel it."""
        if self._analysis_running or not messages:
            return False
        self._analysis_running = True
        try:
            self.run_analysis(messages)
            return True
        except Exception:
            self._analysis_running = False
            logger.exception("Unable to schedule analysis worker")
            return False

    @work(exclusive=True, thread=False, group="analysis")
    async def run_analysis(self, messages: list) -> None:
        """Run one briefing; failed batches are retried after a delay."""
        briefing = self.query_one("#briefing", BriefingPanel)
        briefing.write(Text.from_markup(
            f"[dim]{MSG_ANALYZING.format(n=len(messages))}[/dim]"
        ))
        try:
            logger.info(f"[run_analysis] calling analyze_batch with {len(messages)} messages")
            result = await self.analyzer.analyze_batch(messages)
            logger.info(f"[run_analysis] analyze_batch returned: {result is not None}")
            if result:
                logger.info(f"[run_analysis] rendering briefing (urgency={result.urgency}, "
                           f"response_len={len(result.raw_text)})")
                briefing.add_analysis(result)
                logger.info("[run_analysis] briefing rendered OK")
                status_bar = self.query_one("#status-bar", StatusBar)
                status_bar.update_status(self.telegram_monitor.status, result)
                # Acknowledge only after the briefing has been rendered.
                if self._pending_store:
                    try:
                        self._pending_store.ack(messages)
                    except Exception:
                        logger.exception("Could not acknowledge analyzed messages in persistent queue")
                        self._ack_waiting.extend(messages)
            else:
                self._pending_messages[:0] = messages
                self._retry_at = time.time() + ANALYSIS_RETRY_SECONDS
                logger.warning("Briefing failed; retained %d messages for retry", len(messages))
                briefing.write(Text.from_markup(f"[dim]{MSG_NO_RESULT} (повтор позже)[/dim]"))
        except asyncio.CancelledError:
            self._pending_messages[:0] = messages
            self._retry_at = time.time() + ANALYSIS_RETRY_SECONDS
            raise
        except Exception as e:
            self._pending_messages[:0] = messages
            self._retry_at = time.time() + ANALYSIS_RETRY_SECONDS
            logger.error("Analysis error (%s)", type(e).__name__)
            briefing.write(Text.from_markup(f"[red]Analysis error: {escape(type(e).__name__)}[/red]"))
        finally:
            self._analysis_running = False

    @work(exclusive=True, group="status")
    async def update_status_bar(self) -> None:
        """Periodically update the status bar."""
        status_bar = self.query_one("#status-bar", StatusBar)
        try:
            while True:
                status_bar.update_status(
                    self.telegram_monitor.status,
                    self.analyzer.latest,
                )
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.error("Status bar worker crashed (%s)", type(e).__name__)

    def action_toggle_feed(self) -> None:
        self.query_one("#feed").toggle_class("hidden")

    def action_toggle_briefing(self) -> None:
        self.query_one("#briefing").toggle_class("hidden")

    def action_copy_briefing(self) -> None:
        """Copy the latest intelligence briefing to the system clipboard."""
        latest = self.analyzer.latest
        if not latest:
            self.notify(MSG_NO_BRIEFING, severity="warning")
            return
        self.copy_to_clipboard(latest.raw_text)
        self.notify(MSG_COPIED, severity="information")

    def on_key(self, event: Key) -> None:
        """Catch Ctrl+C at the event level to ensure quit always works."""
        if event.key == "ctrl+c":
            event.stop()
            self.exit()

    def action_toggle_media(self) -> None:
        self.query_one("#media").toggle_class("hidden")

    def action_view_media(self) -> None:
        """View the latest media file using iTerm2 native inline display."""
        if not self._media_paths:
            self.notify(MSG_NO_MEDIA, severity="warning")
            return

        path = Path(self._media_paths[-1])
        if not path.exists():
            self.notify(f"File not found: {path.name}", severity="error")
            return

        if _ITERM2:
            try:
                with self.suspend():
                    sys.stdout.write(f"\n  {path.name}\n\n")
                    sys.stdout.flush()
                    ext = path.suffix.lower()
                    if ext in (".mp4", ".mov", ".avi", ".mkv", ".webm"):
                        display_video_thumbnail(str(path), width="80")
                    else:
                        display_image(str(path), width="80")
                    sys.stdout.write("\n  Press Enter to return to monitor...")
                    sys.stdout.flush()
                    sys.stdin.readline()
            except AttributeError:
                self.notify("Media viewer requires Textual 1.0+", severity="error")
        else:
            # Non-iTerm2: open with system viewer
            import subprocess
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            try:
                subprocess.Popen([opener, str(path)])
                self.notify(f"Opened {path.name}")
            except FileNotFoundError:
                self.notify(f"No viewer found ({opener})", severity="error")
