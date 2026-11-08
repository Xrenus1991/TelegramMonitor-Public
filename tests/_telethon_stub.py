"""Test-only minimal Telethon substitutes when no Telethon wheel is installed.

This is not a Telegram integration test. It allows deterministic offline tests of
client.py pure logic without network access, credentials, or user sessions.
"""
import sys
from types import ModuleType


def install():
    telethon = ModuleType("telethon")
    events = ModuleType("telethon.events")
    errors = ModuleType("telethon.errors")
    tl = ModuleType("telethon.tl")
    types = ModuleType("telethon.tl.types")

    class TelegramClient:
        def __init__(self, *args, **kwargs):
            pass

    class FloodWaitError(Exception):
        seconds = 0

    class Channel:
        def __init__(self, id=0, title=""):
            self.id, self.title = id, title

    class MessageMediaPhoto: pass
    class MessageMediaDocument: pass
    class DocumentAttributeAnimated: pass
    class DocumentAttributeVideo: pass

    telethon.TelegramClient = TelegramClient
    telethon.events = events
    events.NewMessage = lambda **kwargs: kwargs
    errors.FloodWaitError = FloodWaitError
    for name in ("Channel", "MessageMediaPhoto", "MessageMediaDocument",
                 "DocumentAttributeAnimated", "DocumentAttributeVideo"):
        setattr(types, name, locals()[name])
    tl.types = types
    for name, mod in [("telethon", telethon), ("telethon.events", events),
                      ("telethon.errors", errors), ("telethon.tl", tl),
                      ("telethon.tl.types", types)]:
        sys.modules[name] = mod
