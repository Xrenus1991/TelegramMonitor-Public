#!/usr/bin/env python3
"""
Telegram Monitor - CLI entry point.
Monitors public Telegram channels and summarizes with Ollama or OpenAI.

Usage:
    python monitor.py              # Multi-pane TUI dashboard
    python monitor.py --log-only   # Log mode (no TUI)
    python monitor.py --setup      # First-time setup guidance
"""

import argparse
import asyncio
import faulthandler
import logging
import os
import sys
import time

from telegram_monitor.utils import parse_config_arg


async def queue_initial_history(monitor, pending_messages: list, store=None) -> int:
    """Apply the same initial-history limits in log mode as in the TUI.

    Messages saved in SQLite are restored separately. Insert-or-ignore prevents
    charging again for IDs already completed in earlier runs.
    """
    from telegram_monitor.config import HISTORY_PER_CHANNEL, INITIAL_ANALYSIS_MAX_MESSAGES

    try:
        history = await monitor.fetch_history(per_channel=HISTORY_PER_CHANNEL)
    except Exception:
        logging.getLogger(__name__).warning("Initial Telegram history unavailable; continuing live monitoring")
        return 0

    selected = history if os.getenv("ANALYZE_ALL_HISTORY", "0").strip() == "1" else history[:INITIAL_ANALYSIS_MAX_MESSAGES]
    added = 0
    for msg in selected:
        if store:
            try:
                if not store.add(msg):
                    continue
            except Exception:
                logging.getLogger(__name__).warning("Initial-history persistence failed; using in-memory fallback")
        pending_messages.append(msg)
        added += 1
    logging.getLogger(__name__).info("Initial Telegram history: fetched=%d, queued=%d", len(history), added)
    return added


def main():
    # Early --config/-c parse: set env var BEFORE importing config
    parse_config_arg()

    # Deferred imports — config must be loaded after parse_config_arg()
    import atexit

    from dotenv import load_dotenv

    from telegram_monitor.analyzer import MessageAnalyzer
    from telegram_monitor.pending_store import enabled_pending_store
    from telegram_monitor.client import TelegramMonitor
    from telegram_monitor.config import (
        CHANNELS,
        LOG_FILE,
        MAX_BATCH_MULTIPLIER,
        MAX_MESSAGES_PER_CHANNEL,
        MSG_CONNECTED,
        MSG_CONNECTING,
        MSG_DISCONNECTED,
        MSG_NO_CREDENTIALS,
        MSG_RUN_SETUP,
        PROJECT_ROOT,
        SUMMARY_BATCH_SIZE,
        SUMMARY_INTERVAL_SECONDS,
        TOPICS,
    )

    # Dump Python stack traces on SIGABRT / segfault / SIGUSR1 for debugging hangs
    # Write to a file so traces are readable even when the TUI is active
    _fault_path = str(PROJECT_ROOT / "faulthandler.log")
    _fault_file = open(_fault_path, "w")
    atexit.register(_fault_file.close)
    faulthandler.enable(file=_fault_file)
    if hasattr(faulthandler, "register"):
        import signal
        faulthandler.register(signal.SIGUSR1, file=_fault_file, all_threads=True)

    load_dotenv()
    logger = logging.getLogger("telegram_monitor")

    def setup_guide():
        print("""
=== Telegram Monitor Setup ===

1. GET TELEGRAM API CREDENTIALS:
   - Go to https://my.telegram.org
   - Log in with your phone number
   - Click "API development tools"
   - Create an app (any name/description)
   - Note your api_id and api_hash

2. CREATE .env FILE:
   cp .env.example .env
   # Edit .env with your api_id and api_hash

3. JOIN CHANNELS:
   python join_channels.py

4. CUSTOMIZE (optional):
   Edit your config in configs/ (or run python configure.py) to change channels, topics, and timing.
   Use --config to load a specific config (e.g. --config configs/israel.json).

5. RUN:
   python monitor.py

   First run will ask for your phone number and a verification code
   sent to your Telegram app. After that, it reconnects automatically.

KEYBOARD SHORTCUTS (TUI mode):
   q       Quit
   f       Toggle feed panel
   b       Toggle briefing panel
   m       Toggle media panel
   c       Copy latest briefing to clipboard
   v       View most recent media
""")

    async def connect_telegram() -> TelegramMonitor:
        """Connect to Telegram and return the monitor instance."""
        api_id = os.getenv("TELEGRAM_API_ID")
        api_hash = os.getenv("TELEGRAM_API_HASH")
        phone = os.getenv("TELEGRAM_PHONE")

        if not api_id or not api_hash:
            print(MSG_NO_CREDENTIALS)
            print(MSG_RUN_SETUP)
            sys.exit(1)

        monitor = TelegramMonitor(
            api_id=int(api_id),
            api_hash=api_hash,
            channels=CHANNELS,
            max_per_channel=MAX_MESSAGES_PER_CHANNEL,
            phone=phone,
            download_media=True,
            max_media_mb=20.0,
        )

        print(MSG_CONNECTING)
        await monitor.start()
        print(MSG_CONNECTED.format(n=monitor.status['channels_active']))
        return monitor

    async def run_tui(monitor: TelegramMonitor, analyzer: MessageAnalyzer) -> None:
        """Launch the multi-pane Textual TUI."""
        from telegram_monitor.tui import MonitorApp

        app = MonitorApp(
            telegram_monitor=monitor,
            analyzer=analyzer,
            config={
                "batch_size": SUMMARY_BATCH_SIZE,
                "interval": SUMMARY_INTERVAL_SECONDS,
            },
        )
        try:
            await app.run_async()
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass

    async def run_log_only(monitor: TelegramMonitor, analyzer: MessageAnalyzer) -> None:
        """Simple log mode without TUI."""
        store = enabled_pending_store()
        pending_messages = store.load() if store else []
        if pending_messages:
            logger.info("Restored %d persisted pending messages", len(pending_messages))
        last_summary_time = time.time()
        retry_at = 0.0
        ack_waiting = []  # confirmed results whose DB acknowledgement failed

        await queue_initial_history(monitor, pending_messages, store)
        try:
            while True:
                if store and ack_waiting:
                    try:
                        store.ack(ack_waiting)
                    except Exception:
                        logger.warning("Persistent queue acknowledgement still unavailable")
                    else:
                        ack_waiting.clear()
                new_msgs = await monitor.get_new_messages(timeout=1.0)
                if new_msgs:
                    for m in new_msgs:
                        if store:
                            try:
                                if store.add(m):
                                    pending_messages.append(m)
                            except Exception:
                                logger.exception("Persistence unavailable: using in-memory pending queue")
                                pending_messages.append(m)
                        else:
                            pending_messages.append(m)
                    for m in new_msgs:
                        ts = m.timestamp.strftime("%H:%M:%S")
                        media_tag = f" [{m.media_type}]" if m.has_media else ""
                        # Real Telegram message text may contain personal data.
                        if os.getenv("LOG_MESSAGE_TEXT", "0") == "1":
                            logger.info("[%s] [%s]%s %s", ts, m.channel_name,
                                        media_tag, m.text[:200])
                        else:
                            logger.info("[%s] [%s]%s message_id=%s", ts, m.channel_name,
                                        media_tag, m.message_id)

                now = time.time()
                should_analyze = (
                    (len(pending_messages) >= SUMMARY_BATCH_SIZE) or
                    (pending_messages and now - last_summary_time >= SUMMARY_INTERVAL_SECONDS)
                )

                if should_analyze and now >= retry_at:
                    batch = pending_messages[:SUMMARY_BATCH_SIZE * MAX_BATCH_MULTIPLIER]
                    last_summary_time = now
                    try:
                        result = await analyzer.analyze_batch(batch)
                        if result:
                            if store:
                                try:
                                    store.ack(batch)
                                except Exception:
                                    logger.exception("Persistent queue acknowledgement failed")
                                    ack_waiting.extend(batch)
                            del pending_messages[:len(batch)]
                            if os.getenv("LOG_ANALYSIS_TEXT", "0") == "1":
                                logger.info("[%s] %s", result.urgency, result.summary[:200])
                            else:
                                logger.info("[%s] Analyzed %d messages (summary hidden from log)",
                                            result.urgency, len(batch))
                        else:
                            retry_at = time.time() + 60
                            logger.warning("Analysis failed; %d messages retained for retry", len(batch))
                    except Exception as e:
                        retry_at = time.time() + 60
                        logger.error("Analysis failed (%s); pending retained", type(e).__name__)

        except KeyboardInterrupt:
            pass

    parser = argparse.ArgumentParser(description="Telegram Channel Monitor")
    parser.add_argument("--config", "-c", metavar="PATH",
                        help="Path to JSON config file (auto-detected from configs/ if omitted)")
    parser.add_argument("--setup", action="store_true", help="Show setup instructions")
    parser.add_argument("--log-only", action="store_true", help="Log mode (no TUI)")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    if args.setup:
        setup_guide()
        return

    level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()]
        if args.log_only else [logging.FileHandler(LOG_FILE)],
    )

    async def _run():
        monitor = None
        try:
            monitor = await connect_telegram()
            analyzer = MessageAnalyzer(topics=TOPICS)

            if args.log_only:
                await run_log_only(monitor, analyzer)
            else:
                await run_tui(monitor, analyzer)
        except KeyboardInterrupt:
            pass
        finally:
            if monitor:
                await monitor.stop()
                print(MSG_DISCONNECTED)

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
