# Этап 1: контекстное обнаружение Unicode-подмен

Статус: детектор подключён к on-demand API dashboard. Автоматический поиск/классификация и индекс не используют его.
Модуль telegram_monitor/cloud/unicode_context.py, версия ru-context-v1.
Опциональная зависимость confusable_homoglyphs==3.3.1, MIT, источник:
https://github.com/vhf/confusable_homoglyphs
API и таблицы проверены по исходному confusables.py; пакет установлен и испытан в CI и изолированном образе на сервере.

## Поведение

inspect_unicode(text, enabled=True) возвращает original, normalized, findings с позициями в оригинале, version, available и truncated. По умолчанию выключен. Если зависимость отсутствует, оригинал возвращается без изменений. Модуль ничего не сохраняет, не вызывает сеть/модели, не повышает оценку угрозы.

Контекст: минимум две русские буквы в токене, длина токена до64, обработка первых4000символов; хвост и токен на границе не переписываются. Уникальная кириллическая замена должна дать слово из небольшого проверенного списка. Greek→Latin→Cyrillic разрешён только через aceopxy и только с теми же ограничениями. Обычные английские и армянские слова сохраняются. URL, домены .com/.org/.net/.ru/.am, username и email защищены от замены. Это консервативный перечень, не универсальный разбор всех URI/идентификаторов.

Невидимые символы U+200B/U+2060/U+FEFF учитываются только внутри допустимого русского слова. ZWJ/ZWNJ, значимые для других письменностей, не удаляются автоматически.

Ограничения: словарь небольшой, возможны пропуски; не устанавливает намерение обхода и не доказывает угрозу. Нужен контрольный размеченный набор большего размера перед подключением к поиску/классификации. Нынешний smart_text не заменён: поведение production остаётся прежним. Отдельное хранение normalized/version и интеграция поиска ещё не реализованы. enabled — параметр функции. Отдельный CLOUD_TEXT_VARIANTS_ENABLED управляет GET /api/text/variants; в новом production dashboard включён.

## Оценка huntglyph

Источник https://github.com/gregory-chatelier/huntglyph (MIT по README).
Go CLI, поддерживает text/inspect JSON, mixed scripts, invisibles и собственный cumulative risk score. go.mod требует Go1.24.3. Дополнительная польза для нашего русского контекста пока эмпирически не доказана. Не установлен на сервер в runtime; установщик curl|sh не выполнялся. Кандидат для будущего offline-сравнения на том же размеченном наборе, не замена нашей контекстной политике. Его score нельзя напрямую трактовать как угрозу Telegram-сообщения.

## Доказательства

- 15 целевых тестов: https://github.com/Xrenus1991/TelegramMonitor/actions/runs/41994823064
- Общая регрессия:312passed/19skipped;13из этих skipped относятся к отсутствующему optional extra и отдельно прошли выше;6—старые пустые конфигурационные примеры. https://github.com/Xrenus1991/TelegramMonitor/actions/runs/41994823210
- Сервер: https://github.com/Xrenus1991/TelegramMonitor/actions/runs/41995024977
- В отдельном image, networknone при запуске, безсессий/БД:4positive+4preservationcases,20вызовов по4000символов,12.075сек на0.5CPU с tracemalloc,89893байта peak после импорта. Это микротест под tracing, не production latency и не полный RSS процесса. Временный контейнер удалён.

Следующий шаг: кэширование/профилирование отображений символов при необходимости, расширение набора, затем opt-in интеграция в derived search/metadata с отдельной версией и изолированной проверкой; production — только после backup/rollback.

## API

GET /api/text/variants?key=channel:message — только с dashboard auth. Поля original_excerpt, normalized_excerpt, source_length, truncated, findings, version, persisted=false. До4000символов, одинSQLзапрос поPK, рольтолькочтение; производные данные не сохраняются. При disabled/отсутствиибиблиотеки503. Исходники/анализ вБДнепереписываются. Production image6c546079, проверка https://github.com/Xrenus1991/TelegramMonitor/actions/runs/41996074755 . Для отката —сохранённыйпредыдущийdashboard; БДнеоткатывать.
