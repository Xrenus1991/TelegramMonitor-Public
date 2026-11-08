#!/usr/bin/env python3
"""Offline collection diagnostics; never contacts Telegram or uploads messages."""
from __future__ import annotations
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
config_name = os.environ.get('TELEGRAM_MONITOR_CONFIG',
                             'configs/armenia_groups_osint_pro.json')
config_path = Path(config_name).expanduser()
if not config_path.is_absolute():
    config_path = ROOT / config_path
if not config_path.is_file():
    raise SystemExit(f"Не найден JSON: {config_path}")
config = json.loads(config_path.read_text(encoding='utf-8'))
ch = config.get('channels', {})
channels = [item['username'] for group in ch.values() for item in group]
requested = int(os.environ.get('TELEGRAM_HISTORY_PER_CHANNEL', '50'))
requested = max(1, min(200, requested))
initial = max(1, min(100, int(os.environ.get('INITIAL_ANALYSIS_MAX_MESSAGES', '30'))))
print('Количество Telegram-источников:', len(channels))
print('Количество настроенных уникальных источников:', len(set(channels)))
print('История на один канал:', requested)
print('Теоретический максимум загрузки при запуске:', requested * len(channels))
print('Первичная аналитическая сводка: максимум', initial, 'сообщений')
print('Анализ всей загруженной истории:', os.getenv('ANALYZE_ALL_HISTORY', '0') == '1')
print('Восстанавливаемая SQLite-очередь:', os.getenv('PERSIST_ANALYSIS_QUEUE', '0') == '1')
print('Аналитический порог:', config.get('summary_batch_size'))
print('Интервал аналитической обработки (сек.):', config.get('summary_interval_seconds'))
print('Провайдер ИИ:', os.getenv('AI_PROVIDER', 'ollama'))
print('Модуль клиента найден:', (ROOT/'telegram_monitor/client.py').exists())
print('Примечание: реальные доступные сообщения и права доступа эта проверка не подтверждает.')
