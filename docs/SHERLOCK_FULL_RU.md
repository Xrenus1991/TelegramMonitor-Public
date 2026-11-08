# Расширенный Sherlock в Telegram Monitor

**Состояние:** код PR #17. Развёртывание и работа на боевом сервере ещё не подтверждены.
Не заменяйте действующий Docker Compose справочным deploy/compose.reference.yml.

## Назначение
Отдельная защищённая страница /sherlock (в том числе для телефона).
Запуск только вручную. Проверяет публичные username по базе Sherlock 0.16.2.
Не определяет личность, не принимает Telegram ID, телефон, email или ФИО.
Старые методы /api/osint/sherlock/jobs сохранены.

## Возможности
- До 5 username в одной проверке; шаблон {?} означает варианты с _, - и .;
- Все поддерживаемые сайты или до 50 выбранных, с автоподсказкой;
- Тайм-аут 2–60 секунд, NSFW, игнорирование исключений;
- Только администраторский прокси (не URL из браузера);
- Встроенный каталог сайтов или доверенный локальный JSON;
- Статусы Claimed, Available, Unknown, WAF, Illegal, HTTP-код и время ответа;
- История SQLite (30 дней), статусы заданий, экспорт JSON, CSV, XLSX, TXT;
- Защита CSV/Excel от выполнения формул из названий площадок.

## Соответствие CLI и панели
| Sherlock CLI | Реализация |
| --- | --- |
| USERNAMES | одно или несколько username |
| --site | фильтр сайтов |
| --timeout | поле тайм-аута |
| --nsfw | переключатель |
| --ignore-exclusions | переключатель |
| --proxy | только серверный SHERLOCK_PROXY_URL |
| --json | выбранный JSON из SHERLOCK_CATALOG_DIR |
| --local | встроенный каталог, без --json |
| --print-all | сохранение всех статусов |
| --print-found | фильтр найденных результатов |
| --csv / --xlsx / --txt | скачиваемые отчёты |
| --output / --folderoutput | загрузка на устройство пользователя |
| --browse | открытие результатов в браузере |
| --version | поле version в /capabilities |
| --no-color | нет терминальных управляющих кодов |
| --verbose / --dump-response | намеренно отключены, поскольку могут раскрывать внешние страницы и секреты |

## Конфигурация после сверки действующего Compose
- SHERLOCK_OSINT_ENABLED=true — разрешение функции в Dashboard.
- SHERLOCK_WORKER_TOKEN — одинаковый случайный токен от 32 символов в dashboard и sidecar.
- SHERLOCK_DB=/var/lib/sherlock/history.sqlite3 — отдельная сохраняемая база истории.
- SHERLOCK_PROXY_URL — необязательный URL HTTP/HTTPS/SOCKS5, только от администратора.
- SHERLOCK_CATALOG_DIR=/etc/sherlock/catalogs — папка разрешённых локальных JSON, смонтированная read-only.
- Контейнер не имеет доступа к секретам Telegram/OpenAI; пользователь без root, read-only FS.

## API
- GET /api/osint/sherlock/capabilities
- POST /api/osint/sherlock/v2/jobs
- GET /api/osint/sherlock/v2/jobs/{id}
- GET /api/osint/sherlock/v2/recent
- GET /api/osint/sherlock/v2/jobs/{id}/export?format=json|csv|xlsx|txt

POST: действующая Basic-авторизация, same-origin, заголовок x-tm-action: 1 и application/json.

## Тесты и выпуск
1. Офлайн: pytest -q tests/test_sherlock_bridge_offline.py tests/test_sherlock_advanced_offline.py.
2. Синтаксис UI: python scripts/check_dashboard_syntax.py.
3. Docker smoke: docker build -f deploy/sherlock/Dockerfile -t tm-sherlock:ci deploy/sherlock.
4. На изолированном STAGE проверить тестовый публичный username, пакетный запуск, отчёты и историю.
5. Перед production — бэкап, проверка фактического Compose, секретов, сети и процедуры отката.
6. Развёртывать после проверки STAGE, не подменяя существующие конфигурации.

Совпадения по username — кандидаты, а не доказательство принадлежности аккаунтов одному человеку.
