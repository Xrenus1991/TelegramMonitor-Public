#!/usr/bin/env python3
"""
Auto-join all channels listed in config.
Skips channels you're already in (fast), only joins new ones.

Usage: python join_channels.py
"""

import asyncio
import os
import sys

from telegram_monitor.utils import parse_config_arg


def main():
    # Early --config/-c parse: set env var BEFORE importing config
    parse_config_arg()

    # Deferred imports — config must be loaded after parse_config_arg()
    from dotenv import load_dotenv
    from telethon import TelegramClient
    from telethon.errors import (
        ChannelPrivateError,
        FloodWaitError,
        InviteHashExpiredError,
        UsernameInvalidError,
        UsernameNotOccupiedError,
    )
    from telethon.tl.functions.channels import JoinChannelRequest
    from telethon.tl.types import Channel

    from telegram_monitor.config import CHANNELS, SESSION_NAME

    load_dotenv()

    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")
    phone = os.getenv("TELEGRAM_PHONE")

    if not api_id or not api_hash:
        print("Set TELEGRAM_API_ID and TELEGRAM_API_HASH in .env first.")
        sys.exit(1)

    async def _run():
        client = TelegramClient(SESSION_NAME, int(api_id), api_hash)
        await client.start(phone=phone)
        try:
            me = await client.get_me()
            print(f"Logged in as {me.first_name} ({me.phone})")

            # Step 1: Fetch all channels we're already in (one API call)
            print("Checking existing memberships...")
            joined_usernames = set()
            async for dialog in client.iter_dialogs():
                entity = dialog.entity
                if isinstance(entity, Channel) and entity.username:
                    joined_usernames.add(entity.username.lower())
            print(f"Already in {len(joined_usernames)} channels.\n")

            # Step 2: Only join channels we're NOT already in
            to_join = [ch for ch in CHANNELS if ch.lower() not in joined_usernames]
            already = len(CHANNELS) - len(to_join)

            if not to_join:
                print(f"All {len(CHANNELS)} channels already joined. Nothing to do.")
                return

            print(f"Skipping {already} already-joined. Joining {len(to_join)} new channels...\n")

            joined = 0
            failed = []

            for i, channel in enumerate(to_join, 1):
                label = f"[{i}/{len(to_join)}]"
                try:
                    entity = await client.get_entity(channel)
                    await client(JoinChannelRequest(entity))
                    print(f"  {label} Joined: {entity.title}")
                    joined += 1
                    await asyncio.sleep(1.5)  # minimal delay for new joins only

                except FloodWaitError as e:
                    print(f"  {label} Rate limited - waiting {e.seconds}s...")
                    await asyncio.sleep(e.seconds + 1)
                    try:
                        entity = await client.get_entity(channel)
                        await client(JoinChannelRequest(entity))
                        print(f"  {label} Joined (after wait): {entity.title}")
                        joined += 1
                    except Exception as retry_err:
                        print(f"  {label} FAILED on retry '{channel}': {retry_err}")
                        failed.append((channel, str(retry_err)))

                except (ChannelPrivateError, InviteHashExpiredError):
                    print(f"  {label} PRIVATE/EXPIRED: '{channel}'")
                    failed.append((channel, "private or invite expired"))

                except (UsernameNotOccupiedError, UsernameInvalidError):
                    print(f"  {label} NOT FOUND: '{channel}'")
                    failed.append((channel, "username not found"))

                except Exception as e:
                    print(f"  {label} FAILED: '{channel}' - {e}")
                    failed.append((channel, str(e)))

            print(f"\n{'=' * 50}")
            print(f"Already in: {already} | Newly joined: {joined} | Failed: {len(failed)}")
            if failed:
                print("\nFailed channels (remove or fix in configs/):")
                for ch, reason in failed:
                    print(f"  - {ch}: {reason}")
        finally:
            await client.disconnect()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
