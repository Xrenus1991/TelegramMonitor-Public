"""Offline Textual worker scheduling simulations; real GUI smoke test still needed."""
import asyncio
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace


def load_tui(monkeypatch):
    """Swap only Textual's worker/decorator layer for an event-loop simulation."""
    textual = ModuleType('textual')
    app_module = ModuleType('textual.app')
    events = ModuleType('textual.events')
    widgets = ModuleType('textual.widgets')
    def work(**kwargs):
        def decorate(fn):
            def schedule(self, *a, **kw):
                return asyncio.create_task(fn(self, *a, **kw))
            return schedule
        return decorate
    class FakeBase:
        def __init__(self, *a, **k):
            pass
    textual.work = work
    app_module.App = FakeBase
    app_module.ComposeResult = object
    events.Key = object
    widgets.Footer = FakeBase
    widgets.RichLog = FakeBase
    widgets.Static = FakeBase
    for name, obj in [('textual', textual), ('textual.app', app_module),
                      ('textual.events', events), ('textual.widgets', widgets)]:
        monkeypatch.setitem(sys.modules, name, obj)
    path = Path(__file__).resolve().parent.parent / 'telegram_monitor' / 'tui.py'
    spec = importlib.util.spec_from_file_location('offline_tui_mock', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Panel:
    def __init__(self):
        self.writes = []
        self.analyses = []
    def write(self, x):
        self.writes.append(x)
    def add_analysis(self, x):
        self.analyses.append(x)
    def add_message(self, x):
        return None
    async def add_media(self, *a, **k):
        return None
    def update_status(self, *a):
        return None


def fake_app(mod, analyzer):
    app = mod.MonitorApp(telegram_monitor=SimpleNamespace(status={'channels_active': 1},
                         fetch_history=None), analyzer=analyzer, config={'batch_size': 15, 'interval': 240})
    panels = {n: Panel() for n in ('#feed', '#briefing', '#media', '#status-bar')}
    app.query_one = lambda key, *args: panels[key]
    return app, panels


def test_worker_not_cancelled_and_failure_requeues(monkeypatch):
    mod = load_tui(monkeypatch)
    async def scenario():
        gate = asyncio.Event()
        calls = 0
        class Analyzer:
            async def analyze_batch(self, batch):
                nonlocal calls
                calls += 1
                if calls == 1:
                    await gate.wait()
                    return None
                return SimpleNamespace(urgency='NORMAL', raw_text='ok')
        app, panels = fake_app(mod, Analyzer())
        app._pending_messages = []
        app._analysis_running = False
        app._retry_at = 0
        app._media_paths = []
        first = [1, 2, 3]
        assert app._start_analysis(first)
        assert not app._start_analysis([4, 5])
        await asyncio.sleep(0)
        gate.set()
        await asyncio.sleep(0.02)
        assert app._pending_messages == first
        assert app._retry_at > 0
        assert not app._analysis_running
        assert app._start_analysis(first)
        await asyncio.sleep(0.02)
        assert len(panels['#briefing'].analyses) == 1
        assert calls == 2
    asyncio.run(scenario())


def test_caption_translation_id_contains_channel(monkeypatch):
    mod = load_tui(monkeypatch)
    async def scenario():
        class Analyzer:
            async def translate_captions(self, captions):
                return ['Подпись А', 'Подпись Б']
        app, _ = fake_app(mod, Analyzer())
        ms = [SimpleNamespace(has_media=True, media_type='photo', media_caption='Caption',
                              text='[PHOTO] Caption', message_id=1, channel_id=i)
              for i in (10, 11)]
        translations = await app._translate_media_captions(ms)
        assert translations == {(10, 1): 'Подпись А', (11, 1): 'Подпись Б'}
    asyncio.run(scenario())


def test_history_load_uses_configurable_limit(monkeypatch):
    mod = load_tui(monkeypatch)
    async def scenario():
        captured = []
        class Analyzer:
            async def analyze_batch(self, msgs):
                return SimpleNamespace(urgency='NORMAL', raw_text='ok')
        app, _ = fake_app(mod, Analyzer())
        app._pending_messages = []
        app._analysis_running = False
        app._retry_at = 0
        app._media_paths = []
        app._last_summary_time = 0
        async def fake_history(per_channel):
            captured.append(per_channel)
            return []
        app.telegram_monitor.fetch_history = fake_history
        task = app.load_history()
        await task
        assert captured == [mod.HISTORY_PER_CHANNEL]
        assert mod.HISTORY_PER_CHANNEL != 3
    asyncio.run(scenario())


def test_durable_queue_restored_after_analysis_failure(tmp_path, monkeypatch):
    mod = load_tui(monkeypatch)
    monkeypatch.setenv('PERSIST_ANALYSIS_QUEUE', '1')
    monkeypatch.setenv('ANALYSIS_QUEUE_DB', str(tmp_path / 'pending.sqlite3'))
    monkeypatch.setenv('ANALYZE_ALL_HISTORY', '1')
    from telegram_monitor.client import TelegramMessage
    from telegram_monitor.pending_store import PendingMessageStore
    from datetime import datetime, timezone

    history = [TelegramMessage('Public', 42, f'Content {n}',
              datetime(2026, 10, 31, tzinfo=timezone.utc), n) for n in range(4)]
    class FailAnalyzer:
        async def analyze_batch(self, msgs): return None

    async def scenario():
        first, _ = fake_app(mod, FailAnalyzer())
        first.config['batch_size'] = 2
        async def fetch(per_channel): return history
        first.telegram_monitor.fetch_history = fetch
        task = first.load_history()
        await task
        await asyncio.sleep(0.02)
        assert len(first._pending_messages) == 4
        assert len(PendingMessageStore(tmp_path / 'pending.sqlite3').load()) == 4

        calls = []
        class GoodAnalyzer:
            async def analyze_batch(self, msgs):
                calls.extend(m.message_id for m in msgs)
                return SimpleNamespace(urgency='NORMAL', raw_text='ok')
        recovered, _ = fake_app(mod, GoodAnalyzer())
        assert len(recovered._pending_messages) == 4
        batch = recovered._pending_messages[:]
        assert recovered._start_analysis(batch)
        del recovered._pending_messages[:]
        await asyncio.sleep(0.02)
        assert calls == [0, 1, 2, 3]
        assert PendingMessageStore(tmp_path / 'pending.sqlite3').load() == []
    asyncio.run(scenario())
