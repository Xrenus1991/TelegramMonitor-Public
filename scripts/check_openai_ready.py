#!/usr/bin/env python3
"""Dry-run diagnostics by default. --live uses a synthetic prompt and incurs API charges."""
import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка готовности OpenAI API (без Telegram)")
    parser.add_argument("--live", action="store_true", help="Отправить один тестовый запрос в OpenAI API")
    args = parser.parse_args()
    try:
        from telegram_monitor.ai_providers import _load_env, captions_openai, provider_name, openai_settings
        _load_env()
        selected = provider_name()
        model, timeout, max_output, effort = openai_settings()
    except Exception as exc:
        print("ОШИБКА КОНФИГУРАЦИИ:", type(exc).__name__, str(exc))
        return 2
    print("AI_PROVIDER:", selected)
    print("OPENAI_MODEL:", model)
    print("OPENAI_REASONING_EFFORT:", effort)
    print("OPENAI_TIMEOUT_SECONDS:", timeout)
    print("OPENAI_MAX_OUTPUT_TOKENS:", max_output)
    print("OPENAI_API_KEY:", "задан" if os.getenv("OPENAI_API_KEY") else "НЕ задан")
    sdk_ready = True
    try:
        from openai import AsyncOpenAI  # noqa: F401
        print("OpenAI SDK: установлен")
    except ImportError:
        sdk_ready = False
        print("OpenAI SDK: НЕ установлен; python -m pip install openai python-dotenv")
    if not args.live:
        print("Результат: локальная проверка завершена; API и Telegram НЕ вызывались.")
        return 0 if (sdk_ready and bool(os.getenv("OPENAI_API_KEY"))) else 2
    if not sdk_ready:
        return 2
    if not os.getenv("OPENAI_API_KEY"):
        print("Для --live нужен OPENAI_API_KEY в локальном .env.")
        return 2
    result = asyncio.run(captions_openai(["Hello, this is a test message."],
                                         "Переведи нейтральную фразу на русский язык."))
    if not result or len(result) != 1:
        print("ЖИВОЙ ЗАПРОС: неуспешен. Проверьте API billing, модель, сеть и настройки.")
        return 1
    print("ЖИВОЙ ЗАПРОС: успешен; результат:", result[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
