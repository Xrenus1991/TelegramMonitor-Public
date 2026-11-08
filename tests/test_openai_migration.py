"""Offline regression tests: no OpenAI key or Telegram connection required."""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from telegram_monitor.ai_providers import (
    ANALYSIS_SCHEMA, analysis_to_markdown, analyze_openai, captions_openai,
    mask_contacts, provider_name,
)
from telegram_monitor.analyzer import MessageAnalyzer


@pytest.fixture(autouse=True)
def isolated_mock_settings(monkeypatch):
    """Real .env must not change the deterministic offline test matrix."""
    for key, value in {
        "AI_PROVIDER": "ollama",
        "OPENAI_MODEL": "gpt-6-luna",
        "OPENAI_REASONING_EFFORT": "low",
        "OPENAI_TIMEOUT_SECONDS": "90",
        "OPENAI_MAX_OUTPUT_TOKENS": "3600",
        "OPENAI_MAX_REQUESTS_PER_RUN": "100",
        "OPENAI_REDACT_CONTACTS": "1",
        "AI_MAX_MESSAGES_PER_CHUNK": "6",
        "AI_MAX_CHARS_PER_CHUNK": "6000",
        "AI_MAX_MESSAGE_CHARS": "4000",
    }.items():
        monkeypatch.setenv(key, value)


def sample_data(urgency="NORMAL", summary="Сообщений о нарушениях не обнаружено", n=1):
    return {
        "urgency": urgency,
        "urgency_reason": "Недостаточно подтверждений",
        "summary": summary,
        "translations": ["[Канал] RU: Объявление о встрече | ТЕМА: общение"] * n,
        "key_actors": [], "timeline": [],
        "source_analysis": [{"source": "Канал", "message_count": 1,
                             "stance": "нейтральная", "tone": "спокойный"}],
        "rhetoric_shifts": [], "media_content": [],
        "contradictions": [],
        "topic_deep_dive": [{"topic": "события", "analysis": "Недостаточно данных"}],
    }


def msg(i):
    return SimpleNamespace(
        channel_name="Канал" if i % 2 else "Другой канал",
        timestamp=datetime(2026, 10, 31, 12, 34, tzinfo=timezone.utc),
        text=f"Сообщение {i}: встреча состоится завтра",
        has_media=False, media_type=None, media_size_kb=None,
    )


def test_provider_defaults_to_ollama(monkeypatch):
    monkeypatch.setattr("telegram_monitor.ai_providers._load_env", lambda: None)
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    assert provider_name() == "ollama"


def test_invalid_provider_rejected(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "unknown")
    with pytest.raises(ValueError):
        provider_name()


def test_schema_has_required_fields_and_no_extras():
    assert set(ANALYSIS_SCHEMA["required"]) == set(ANALYSIS_SCHEMA["properties"])
    assert ANALYSIS_SCHEMA["additionalProperties"] is False


def test_markdown_parser_compat(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    a = MessageAnalyzer(["события"])
    text = analysis_to_markdown(sample_data("HIGH", "Есть неподтверждённое сообщение"))
    parsed = a._parse_response(text, [msg(1)], {"Канал": 1})
    assert parsed.urgency == "HIGH"
    assert parsed.summary == "Есть неподтверждённое сообщение"
    assert len(parsed.translations) == 1
    assert parsed.topic_summaries["события"] == "Недостаточно данных"
    assert parsed.sentiment_map["Канал"].startswith("нейтральная")


def test_missing_summary_fails_closed():
    with pytest.raises(ValueError, match="summary"):
        analysis_to_markdown(sample_data(summary=""))


def test_mask_contacts():
    masked = mask_contacts("Связь: someone@example.com или +374 99 123456; дата 2026-10-31")
    assert "someone@example.com" not in masked
    assert "123456" not in masked
    assert "[EMAIL_СКРЫТ]" in masked
    assert "2026-10-31" in masked


def test_batch_25_messages_no_loss_and_multiple_chunks(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("AI_MAX_MESSAGES_PER_CHUNK", "7")
    a = MessageAnalyzer(["события"])
    call_count = 0

    async def fake_call(prompt):
        import re
        nonlocal call_count
        call_count += 1
        num = int(re.search(r"Проанализируй (\d+) новых сообщений", prompt).group(1))
        return analysis_to_markdown(sample_data(n=num))
    monkeypatch.setattr(a, "_call_claude", fake_call)
    original = [msg(i) for i in range(25)]
    result = asyncio.run(a.analyze_batch(original))
    assert result and result.msg_count == 25
    assert a.total_analyzed == 25
    assert sum(result.source_breakdown.values()) == 25
    assert call_count == 4
    assert len(a.history) == 1


def test_failure_does_not_mark_batch_complete(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("AI_MAX_MESSAGES_PER_CHUNK", "2")
    a = MessageAnalyzer(["события"])
    calls = 0
    async def fake_call(prompt):
        nonlocal calls
        calls += 1
        return analysis_to_markdown(sample_data(n=2)) if calls == 1 else None
    monkeypatch.setattr(a, "_call_claude", fake_call)
    assert asyncio.run(a.analyze_batch([msg(i) for i in range(3)])) is None
    assert a.total_analyzed == 0
    assert len(a.history) == 0


def test_legacy_ollama_route(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    a = MessageAnalyzer([])
    async def fake_local(prompt, system):
        assert "русском" in system
        return "Локальный результат"
    monkeypatch.setattr(a, "_call_ollama", fake_local)
    assert asyncio.run(a._call_claude("тест")) == "Локальный результат"


def test_no_key_no_request(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setattr("telegram_monitor.ai_providers._load_env", lambda: None)
    assert asyncio.run(analyze_openai("Тест", "Тест")) is None


def fake_api(monkeypatch, output_json, *, status="completed"):
    """Mock complete SDK boundary, including HTTP call data and close()."""
    seen = []
    class Client:
        def __init__(self, **kwargs):
            self.responses = self
            self.closed = False
        async def create(self, **kwargs):
            seen.append(kwargs)
            return SimpleNamespace(status=status, output_text=json.dumps(output_json, ensure_ascii=False),
                                   usage=SimpleNamespace(input_tokens=100, output_tokens=150))
        async def close(self):
            self.closed = True
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(AsyncOpenAI=Client))
    monkeypatch.setenv("OPENAI_API_KEY", "mock-only-not-real-key")
    monkeypatch.setenv("AI_PROVIDER", "openai")
    return seen


def test_openai_sdk_mock_structured_and_store_false(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "gpt-6-luna")
    seen = fake_api(monkeypatch, sample_data())
    value = asyncio.run(analyze_openai("Сообщение", "Инструкция"))
    assert "## EXECUTIVE SUMMARY" in value
    assert seen[0]["store"] is False
    assert seen[0]["text"]["format"]["type"] == "json_schema"
    assert seen[0]["model"] == "gpt-6-luna"


def test_caption_translation_mock_and_mismatch(monkeypatch):
    fake_api(monkeypatch, {"translations": ["Переведённый текст", "Второй текст"]})
    value = asyncio.run(captions_openai(["Test", "Text"], "перевод"))
    assert value == ["Переведённый текст", "Второй текст"]
    fake_api(monkeypatch, {"translations": ["only one"]})
    assert asyncio.run(captions_openai(["one", "two"], "перевод")) is None


def test_openai_request_limit(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_MAX_REQUESTS_PER_RUN", "1")
    a = MessageAnalyzer([])
    async def fake_cloud(prompt, system):
        return analysis_to_markdown(sample_data())
    monkeypatch.setattr("telegram_monitor.analyzer.analyze_openai", fake_cloud)
    assert asyncio.run(a._call_claude("one"))
    assert asyncio.run(a._call_claude("two")) is None


def test_explicit_openai_redaction_in_prompt(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_REDACT_CONTACTS", "1")
    a = MessageAnalyzer([])
    received = []
    async def fake(prompt):
        received.append(prompt)
        return analysis_to_markdown(sample_data())
    monkeypatch.setattr(a, "_call_claude", fake)
    m = msg(1)
    m.text = "someone@example.com и +374 99 123456"
    assert asyncio.run(a.analyze_batch([m]))
    assert "someone@example.com" not in received[0]
    assert "123456" not in received[0]


def test_ollama_response_http_mock(monkeypatch):
    import io
    from telegram_monitor.analyzer import MessageAnalyzer
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:3b")
    class Resp:
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def read(self,*args): return json.dumps({"message": {"content": "text"}}).encode()
    def urlopen(req,timeout):
        assert req.full_url.endswith("/api/chat")
        assert timeout >= 15
        return Resp()
    monkeypatch.setattr("urllib.request.urlopen",urlopen)
    assert MessageAnalyzer._ollama_request("hello", "system") == "text"


def test_empty_batch_does_not_call_model(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    a = MessageAnalyzer([])
    assert asyncio.run(a.analyze_batch([])) is None
    assert a.total_analyzed == 0


def test_low_urgency_preserved_when_merging(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("AI_MAX_MESSAGES_PER_CHUNK", "1")
    a = MessageAnalyzer([])
    async def fake(prompt):
        return analysis_to_markdown(sample_data(urgency="LOW"))
    monkeypatch.setattr(a, "_call_claude", fake)
    out = asyncio.run(a.analyze_batch([msg(0), msg(1)]))
    assert out.urgency == "LOW"


def test_caption_failure_keeps_original(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    a = MessageAnalyzer([])
    async def broken(captions, system):
        return None
    monkeypatch.setattr("telegram_monitor.analyzer.captions_openai", broken)
    assert asyncio.run(a.translate_captions(["Original caption"])) == ["Original caption"]


def test_incomplete_openai_response_rejected(monkeypatch):
    fake_api(monkeypatch, sample_data(), status="incomplete")
    assert asyncio.run(analyze_openai("test", "system")) is None


def test_cloud_model_returned_invalid_json_shape(monkeypatch):
    fake_api(monkeypatch, {"summary": "text", "urgency": "NORMAL"})
    assert asyncio.run(analyze_openai("test", "system")) is None


def test_openai_api_error_does_not_crash(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-test-key")
    class BrokenClient:
        def __init__(self, **kwargs): self.responses = self
        async def create(self, **kwargs): raise TimeoutError("test")
        async def close(self): pass
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(AsyncOpenAI=BrokenClient))
    assert asyncio.run(analyze_openai("test", "system")) is None


def test_two_sources_kept_without_exposing_fake_phone(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("AI_MAX_MESSAGES_PER_CHUNK", "1")
    a = MessageAnalyzer(["миграционное законодательство"])
    prompts = []
    async def fake(prompt):
        prompts.append(prompt)
        return analysis_to_markdown(sample_data())
    monkeypatch.setattr(a, "_call_claude", fake)
    first = msg(0); first.text = "Вопрос о документах. +374 99 123456"
    second = msg(1); second.text = "Законное собрание"
    result = asyncio.run(a.analyze_batch([first, second]))
    assert result.source_breakdown == {"Другой канал": 1, "Канал": 1}
    assert "123456" not in "\n".join(prompts)
    assert any("Законное собрание" in p for p in prompts)


def test_missing_translations_is_rejected(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    a = MessageAnalyzer(["события"])
    async def incomplete(prompt):
        return analysis_to_markdown(sample_data(n=1))
    monkeypatch.setattr(a, "_call_claude", incomplete)
    assert asyncio.run(a.analyze_batch([msg(1), msg(2)])) is None
    assert a.total_analyzed == 0


def test_invalid_timeout_setting_is_handled(monkeypatch):
    fake_api(monkeypatch, sample_data())
    monkeypatch.setenv("OPENAI_TIMEOUT_SECONDS", "bad")
    assert asyncio.run(analyze_openai("test", "system")) is None


def test_structured_records_keep_all_legacy_fields(monkeypatch):
    data = sample_data()
    data["key_actors"] = [{"name": "Мэрия", "role": "организация",
                          "context": "анонс мероприятия", "sentiment": "нейтральная"}]
    data["timeline"] = [{"time": "12:45", "event": "опубликовано официальное сообщение"}]
    data["media_content"] = [{"source": "Канал", "media_type": "фото",
                              "description": "к публикации приложено изображение; содержание неизвестно"}]
    data["source_analysis"] = [{"source": "Канал", "message_count": 2,
                                "stance": "нейтральная", "tone": "спокойный"}]
    markdown = analysis_to_markdown(data)
    assert "Мэрия | организация | анонс мероприятия | нейтральная" in markdown
    monkeypatch.setenv("AI_PROVIDER", "openai")
    result = MessageAnalyzer(["события"])._parse_response(markdown, [msg(1)], {"Канал": 1})
    assert result.key_actors[0]["name"] == "Мэрия"
    assert result.key_actors[0]["role"] == "организация"
    assert result.timeline[0] == {"time": "12:45", "event": "опубликовано официальное сообщение"}
    assert "изображение" in result.media_mentions[0]["raw"]
    assert result.sentiment_map["Канал"] == "нейтральная (спокойный)"


def test_no_silent_drop_of_malformed_structured_fields():
    data = sample_data()
    data["key_actors"] = ["некорректная строка"]
    with pytest.raises(ValueError, match="key_actors"):
        analysis_to_markdown(data)
    data = sample_data()
    data["source_analysis"][0]["message_count"] = "two"
    with pytest.raises(ValueError, match="source_analysis"):
        analysis_to_markdown(data)
    data = sample_data()
    data["summary"] = "корректно"
    data["unexpected"] = 1
    with pytest.raises(ValueError, match="unexpected"):
        analysis_to_markdown(data)


def test_all_nested_openai_objects_are_strict():
    for key in ("key_actors", "timeline", "source_analysis", "media_content", "topic_deep_dive"):
        item = ANALYSIS_SCHEMA["properties"][key]["items"]
        assert item["additionalProperties"] is False
        assert set(item["required"]) == set(item["properties"])


def test_low_effort_sent_to_api(monkeypatch):
    seen = fake_api(monkeypatch, sample_data())
    monkeypatch.setenv("OPENAI_MODEL", "gpt-6-luna")
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "low")
    assert asyncio.run(analyze_openai("ввод", "система"))
    assert seen[0]["reasoning"] == {"effort": "low"}
    assert seen[0]["store"] is False


def test_sol_unsupported_reasoning_effort_fails_before_api(monkeypatch):
    seen = fake_api(monkeypatch, sample_data())
    monkeypatch.setenv("OPENAI_MODEL", "gpt-6.1-sol")
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "none")
    assert asyncio.run(analyze_openai("ввод", "система")) is None
    assert seen == []


def test_invalid_cloud_request_limit_returns_safely(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_MAX_REQUESTS_PER_RUN", "unlimited")
    a = MessageAnalyzer([])
    assert asyncio.run(a._call_claude("input")) is None
    assert asyncio.run(a.translate_captions(["Original"])) == ["Original"]
    assert a._openai_calls == 0


def test_very_long_message_is_marked_truncated_in_prompt(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("AI_MAX_MESSAGE_CHARS", "1000")
    monkeypatch.setenv("AI_MAX_CHARS_PER_CHUNK", "1000")
    a = MessageAnalyzer([])
    seen = []

    async def fake(prompt):
        seen.append(prompt)
        return analysis_to_markdown(sample_data())

    monkeypatch.setattr(a, "_call_claude", fake)
    long_message = msg(2)
    long_message.text = "м" * 12000
    output = asyncio.run(a.analyze_batch([long_message]))
    assert output.msg_count == 1
    assert "ТЕКСТ ОБРЕЗАН" in seen[0]
    assert "12000" in seen[0]
    assert long_message.text == "м" * 12000  # local object unchanged
    assert seen[0].count("м" * 100) < 20


def test_channel_name_contacts_masked_in_cloud_prompt(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_REDACT_CONTACTS", "1")
    a = MessageAnalyzer([])
    seen = []

    async def fake(prompt):
        seen.append(prompt)
        return analysis_to_markdown(sample_data())

    monkeypatch.setattr(a, "_call_claude", fake)
    m = msg(2)
    m.channel_name = "owner@example.com\nНЕИЗВЕСТНОЕ ПОЛЕ"
    assert asyncio.run(a.analyze_batch([m]))
    assert "owner@example.com" not in seen[0]
    assert "[EMAIL_СКРЫТ]" in seen[0]
    assert "\nНЕИЗВЕСТНОЕ ПОЛЕ" not in seen[0]


def test_invalid_chunk_config_prevents_requests(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("AI_MAX_MESSAGES_PER_CHUNK", "0")
    a = MessageAnalyzer([])
    assert asyncio.run(a.analyze_batch([msg(1)])) is None
    assert a.total_analyzed == 0
    monkeypatch.setenv("AI_MAX_MESSAGES_PER_CHUNK", "2")
    monkeypatch.setenv("AI_MAX_CHARS_PER_CHUNK", "bad")
    assert asyncio.run(a.analyze_batch([msg(1)])) is None


def test_api_client_close_failure_is_swallowed(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-test-key")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-6-luna")
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "low")

    class Client:
        def __init__(self, **kwargs):
            self.responses = self

        async def create(self, **kwargs):
            return SimpleNamespace(status="completed", output_text=json.dumps(sample_data(), ensure_ascii=False))

        async def close(self):
            raise RuntimeError("close failed")

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(AsyncOpenAI=Client))
    assert asyncio.run(analyze_openai("test", "system"))


def test_large_batch_refuses_partial_success_at_request_cap(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("AI_MAX_MESSAGES_PER_CHUNK", "5")
    monkeypatch.setenv("OPENAI_MAX_REQUESTS_PER_RUN", "2")
    a = MessageAnalyzer([])
    seen = []

    async def fake_cloud(prompt, system):
        seen.append(prompt)
        return analysis_to_markdown(sample_data(n=5))

    monkeypatch.setattr("telegram_monitor.analyzer.analyze_openai", fake_cloud)
    assert asyncio.run(a.analyze_batch([msg(i) for i in range(16)])) is None
    assert a.total_analyzed == 0
    assert len(a.history) == 0
    assert len(seen) == 2


def test_ollama_truncation_is_rejected(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:3b")
    class Resp:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, *args):
            return json.dumps({"message": {"content": "## EXECUTIVE SUMMARY\nPartial"},
                               "done_reason": "length"}).encode()
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: Resp())
    assert MessageAnalyzer._ollama_request("x", "y") is None


def test_redaction_typo_does_not_disable_privacy(monkeypatch):
    from telegram_monitor.ai_providers import should_redact_contacts
    monkeypatch.setenv("OPENAI_REDACT_CONTACTS", "false-typo")
    assert should_redact_contacts() is True
    monkeypatch.setenv("OPENAI_REDACT_CONTACTS", "0")
    assert should_redact_contacts() is False


def test_readiness_validates_model_and_effort(monkeypatch):
    from telegram_monitor.ai_providers import openai_settings
    monkeypatch.setenv("OPENAI_MODEL", "gpt-6.1-sol")
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "low")
    assert openai_settings()[0] == "gpt-6.1-sol"
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "none")
    with pytest.raises(ValueError):
        openai_settings()
