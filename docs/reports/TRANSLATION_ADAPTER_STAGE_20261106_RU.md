# LANG-01: адаптер языка и перевода, 6 ноября 2026

## Реализовано

[PR33](https://github.com/Xrenus1991/TelegramMonitor/pull/33), merge8760ade86aaaef1dbeb30b9e6e0fe307c7f457e6.

- TranslationService выключен по умолчанию; dry_run не вызывает детектор/провайдер.
- Lingua2.2.0 (Apache2.0) ограничена RU/HY/EN. Короткий/сильносмешанный ввод может быть unknown. RU возвращается без оплаты.
- Для en/hy→ru используются существующие backend, Settings, paid_call, reserve/settle и общий месячный предел. Input≤3000 символов, output≤min(1000,cfg.max_output_tokens), один вызов без retry, timeout100s.
- original_text возвращается точно; translated_text и версия отделены. Таблица messages не меняется.
- Кэш tm_text_translations отделён от основной схемы; создание не вызывается конструктором/сервисом автоматически. SHA256 точного текста, язык, model и версии входят в ключ.
- Уникальный claim предотвращает оплату одного ключа параллельными вызовами. При сетевой ошибке/отмене сохраняется штатный резерв и failed-claim; автоматического повторного списания нет. Crash оставляет busy. Отказ бюджета удаляет только собственный неоплаченный claim.

## Доказательства

| Проверка | Результат |
|---|---|
| Локальные адресные тесты | 24passed |
| [Общий cloud CI и PG-контракт](https://github.com/Xrenus1991/TelegramMonitor/actions/runs/42171123070) | 415passed,11skipped,5subtests;4конкурентных claims/1владелец |
| [Server stage](https://github.com/Xrenus1991/TelegramMonitor/actions/runs/42171694049) | Реальная Lingua2.2.0 и отдельная пустая PostgreSQL; cache/original/budget passed |
| Ресурсы server stage | peakRSS62.7MiB; перед запуском2083MiB available RAM/64236MiB free disk |
| Внешние вызовы stage | 0; backend синтетический, ledger настоящий на fixture |
| [Очистка/состояние](https://github.com/Xrenus1991/TelegramMonitor/actions/runs/42171870888) | Хеш кандидата совпал; временных контейнеров/сетей0; AUTONOMY_OK |

Image stage sha256:65605715e293c5a3668857cc381ffed553be76dc63767b36c0ea73ac26dc43ae.
Candidate SHA256de2e2c874a5c9b87e61c66ed7f8c6868f0e70bca02cad95e9bbf3d2e03b9fa62.

Зависимости работающего worker сопоставлены с git blobs: db.py c5fe71e8c94aa553248d57669f460dc7e2b637eb и worker.py7aec9769bd26c7eb0f2a9ab77677a598ef3b0ea6. Различия db.py с main — только dashboard read helpers/filter; cache и бюджетный код совпадают.

Первоначальные stage-запуски останавливались проверкой несовпадающего baseline и затем правами исходников внутри тестового образа. Baseline установлен по реальному fingerprint и проверенному diff; публичные исходники внутри образа получили0644. Секреты/рабочие права не менялись. Неудачные проверки не выдаются за успешные, финальный результат подтверждён отдельным запуском.

На06:01UTC/10:01Армения5561сообщений done, обычная очередь и expiredleases0; последнее сохранение06:00:54UTC. Локальный cron05:52UTCexit0/roles_exit0, SCRAM proof свежий. Deep32ожидают при выполненных3за сутки и лимите3; месячный предел$5 не изменён.

## Ограничения и следующий пункт

В production адаптер не установлен: API/UI и автоматическая очередь ещё не подключены. Нет расширения прав dashboard и передачи ему API-ключа. Качество внешнего перевода этим stage не измерялось: настоящий внешний синтетический RU/AM/EN вызов подтверждён [предыдущим отчётом](FIVE_POINT_VERIFICATION_20261106_RU.md).11skips не считать успешными сценариями.

Следующий пункт: authenticated on-demand API/UI, безопасная доставка задания существующему worker, reconciliation failed/busy без повторного списания. Затем проверка stage на копии реальных данных, конкретные образы/config/backup и отрепетированный откат приложения. Этот отчёт не заменяет исполнимый rollback и не закрывает весь этап2.
