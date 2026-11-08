"""Cross-module regression tests that do not require live API connections."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from telegram_monitor.analyzer import MessageAnalyzer
from telegram_monitor.ai_providers import analysis_to_markdown


def sample():
    return dict(urgency='NORMAL', urgency_reason='Не установлено', summary='Сводка',
                translations=['[Канал] RU: Текст | ТЕМА: иное'],
                key_actors=[], timeline=[], source_analysis=[], rhetoric_shifts=[],
                media_content=[], contradictions=[], topic_deep_dive=[])


def test_caption_requests_share_analysis_budget(monkeypatch):
    monkeypatch.setenv('AI_PROVIDER', 'openai')
    monkeypatch.setenv('OPENAI_MAX_REQUESTS_PER_RUN', '1')
    called = []

    async def mock_translate(captions, system):
        called.append(('caption', len(captions)))
        return ['Перевод']

    async def mock_analysis(prompt, system):
        called.append(('analysis', 1))
        return analysis_to_markdown(sample())

    monkeypatch.setattr('telegram_monitor.analyzer.captions_openai', mock_translate)
    monkeypatch.setattr('telegram_monitor.analyzer.analyze_openai', mock_analysis)
    analyzer = MessageAnalyzer(['Тема'])
    assert asyncio.run(analyzer.translate_captions(['Caption'])) == ['Перевод']
    assert asyncio.run(analyzer._call_claude('test')) is None
    assert called == [('caption', 1)]


def test_same_message_text_can_be_analyzed_with_ollama(monkeypatch):
    monkeypatch.setenv('AI_PROVIDER', 'ollama')
    analyzer = MessageAnalyzer(['Тема'])
    async def fake(prompt, system):
        assert 'русском' in system
        return '## EXECUTIVE SUMMARY\nСводка'
    monkeypatch.setattr(analyzer, '_call_ollama', fake)
    m = SimpleNamespace(channel_name='Канал', timestamp=datetime.now(timezone.utc),
        text='Хороший день', has_media=False, media_type=None, media_size_kb=0)
    result = asyncio.run(analyzer.analyze_batch([m]))
    assert result and result.summary == 'Сводка'
