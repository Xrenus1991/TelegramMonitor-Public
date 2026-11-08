# Реестр автономной интеграции Telegram Monitor

Обновлено: 2026-11-05 UTC. Это журнал доказательств, а не заявление о завершении проекта.
Источники требований: docs/INTEGRATIONS_RU.md в docs/integration-roadmap;
docs/IMPLEMENTATION_STATUS_RU.md в feature/sherlock-user-detail-tab и PR #13.
Порядок: этап 0, затем контекстные подмены букв, затем остальные зависимости плана.

## Production update 2026-11-05 07:56 UTC (11:56 Армения)

- Исправление счётчика комментариев установлено в tm-v861-live-collector. Git blob comments.py: 3df6dcc8251036566c34be953cdee13145d9892b, совпадает с протестированным кандидатом.
- Перед установкой сохранены актуальный inspect и прежний модуль, закреплён образ. Выполнены свежие backup и isolated restore (07:55 UTC). Дампы и защищённые снимки остались на сервере.
- Конкретный rollback.py сохранён в /var/lib/tm-actions/monitor-autonomy/deployments/20261105T075530Z. Замена/восстановление файла с перезапуском проверены на изолированном клоне: run41988840090. Production rollback не потребовался.
- Collector перезапущен в рамках установки, через20 секунд файл и Running повторно проверены. Сообщений4688, все done, pending/processing/expired leases=0. Последнее поступление до установки07:54 UTC; последующее движение подтверждено в07:57 UTC: 4689 done, новый приём07:57:06, последний scan07:56:08, 82 scans; очередь пуста. AUTONOMY_OK, Sherlock public control passed. Проверка: https://github.com/Xrenus1991/TelegramMonitor/actions/runs/41990851796
- Важно: это адресное обновление файла в writable layer текущего контейнера, НЕ новый образ. Пересоздание из старого образа потеряет исправление. Следующий релиз должен включить модуль в immutable image и воспроизводимый deploy; существующую Telegram-сессию не копировать и не менять.
- Проверка: https://github.com/Xrenus1991/TelegramMonitor/actions/runs/41990578922
- Этап0 пока не завершён: нужен полный Sherlock API job/proxy/UI контроль и воспроизводимый выпуск. PR12/13 и этап1 следуют далее.

## Обновление 2026-11-05 07:32 UTC (11:32 Армения)

- Причина ошибки stage установлена: CommentStore.save_thread суммировал INSERT rowcount; драйвер вернул -1 при фактической вставке. Исправлено через RETURNING ключа, без изменения схемы и правил сбора.
- Кандидат b5d98a20dd7550476e4dc798dc4ebac59ebea1de прошёл 304 теста (6 skipped, 5 subtests), Sherlock image smoke и cloud dependency smoke Python 3.11/3.12. Исправление перенесено в main; на рабочий сервер ещё НЕ установлено.
- PostgreSQL stage прошёл: восстановление 4500 строк, 12 HTTP маршрутов, 401/422, synthetic contracts sender backfill без сброса анализа, анонимный sender=NULL, checkpoint, дедупликация, пустое обсуждение, отсутствие коммита при ошибке транспорта, API значения и маскирование. Для синтетики использовалась отдельная contract БД, тестовые контейнеры удалены. Telegram/OpenAI не вызывались.
- Production 07:31 UTC: 4648 сообщений обработано, pending/processing/expired leases=0; 344 строки с sender ID; 10 комментариев, 81 scan, последний scan 07:22 UTC. Это подтверждает фактические сохранения и сканирование, но не полный Telegram end-to-end тест всех ветвей.
- Dashboard8098/Sherlock8099 health200; collector/worker running, PostgreSQL healthy, restartcount=0. Локальный cron 07:22 завершился exit0; отдельная проверка 07:31 AUTONOMY_OK. Свежесть меньше90мин. Последний GitHub autonomy run02:34 старше90мин — локальный fallback и свежий maintenance подтверждены, GitHub cron не считать исправленным.
- Проверенный дамп от04.11 22:13 UTC повторно восстановлен на stage; роли не восстановлены и не протестированы.
- Sherlock CLI из рабочего sidecar успешно нашёл публичный контроль octocat только на GitHub. Учитывается канонический вариант www.github.com. Это НЕ полный тест UI→proxy→jobs→history и не подтверждение личности.
- Открытые PR #12/#13 остались неизменны, в production не установлены. Внешние источники телефона/email/ФИО не подключены.

Доказательства:
- CI: https://github.com/Xrenus1991/TelegramMonitor/actions/runs/41987797090
- Stage и сервер: https://github.com/Xrenus1991/TelegramMonitor/actions/runs/41987803026
- Репродукция старой ошибки (-1,1): https://github.com/Xrenus1991/TelegramMonitor/actions/runs/41980568845

Следующий шаг: подготовить для конкретного обновления collector сохранение образа/защищённой конфигурации, свежий backup и исполнимый откат; проверить откат в изоляции до переключения production. Этап0 ещё не объявлен завершённым. Не менять Telegram-сессию и не восстанавливать production БД из дампа.

## Предыдущий запуск: импорт baseline

- Зафиксировано отсутствие свежих запусков GitHub cron (последний успешный результат был старше 90 минут). Причина пропуска внутри GitHub не установлена.
- Добавлено независимое локальное расписание от tm-actions, каждые 15 минут. Сохранены прежний crontab и копии скриптов только на сервере. Общие flock/state, дедупликация циклов в пределах 5 минут; лимиты восстановления не увеличены. 15 unit-тестов прошли.
- Свежие проверки: dashboard 8098 и Sherlock 8099 /health=200, четыре процесса running без рестартов, PostgreSQL healthy. На 01:27 UTC 4532 сообщения обработаны, очередь и просроченные lease пусты; последний приём 01:25 UTC. Это доказательство движения счётчиков, а не полного теста Telegram/OpenAI.
- Резервная копия от 2026-11-04 22:13 UTC проверена восстановлением в изолированный PostgreSQL: 4500 сообщений. Роли сохранены, их восстановление не проверено. Все дампы остались на сервере.
- Сопоставлены Git blob SHA работающего Python-кода и трёх HTML-файлов. Dashboard/worker и bridge Sherlock соответствуют audit/stage1-sherlock-stability-20261104 (89f0df2). Collector отличается исправленным comments.py из fix/v861-comment-discussion-gate (c2bde01).
- Собрана ветка audit/runtime-baseline-20261105, commit b89038b6b2e98edcb0d841d6a5345eee55ad6537: стабильная база плюс исправление comments и соответствующие тесты. CI: 303 passed, 6 skipped (пустые наборы конфигурационных примеров), 5 subtests. Сборка Sherlock и импорт cloud-зависимостей Python 3.11/3.12 прошли.
- Приложение production не обновлялось. Исправлена доступность корня тестового bind mount при umask 077. Повторный stage прошёл: 12 маршрутов, 401 без авторизации, 422 при неправильном лимите, заголовки no-store, чтение комментариев и источников; восстановлено 4500 строк, количество не изменилось. БД read-only, сеть internal, временные контейнеры удалены.
- Первый фактический запуск локального cron: 01:37:01–01:37:02 UTC, exit_code=0, ok=true. На 01:38 UTC уже 4534 обработанных сообщения, очереди и просроченных lease нет.
- Проверенная исходная база импортирована в main без перезаписи существующих ops и без развёртывания приложения.

Доказательства:
- CI main после импорта: https://github.com/Xrenus1991/TelegramMonitor/actions/runs/41950093446
- Финальная диагностика без одноразовых задач: https://github.com/Xrenus1991/TelegramMonitor/actions/runs/41950103000
- CI baseline: https://github.com/Xrenus1991/TelegramMonitor/actions/runs/41949049139
- Свежая диагностика: https://github.com/Xrenus1991/TelegramMonitor/actions/runs/41948670359
- Установка локального расписания: https://github.com/Xrenus1991/TelegramMonitor/actions/runs/41948954590
- Успешный stage и подтверждение cron: https://github.com/Xrenus1991/TelegramMonitor/actions/runs/41949831861
- Первый stage (неуспешный): https://github.com/Xrenus1991/TelegramMonitor/actions/runs/41949367254

## Матрица требований

«Частично» означает ограниченное доказательство, а не готовность. Общий набор из 303 тестов не подтверждает автоматически каждый сценарий. Production «код сопоставлен» не означает end-to-end тест. Откат модуля collector проверен в изоляции; для image promotion сохранён прежний остановленный контейнер. Для остальных компонентов откат отмечается отдельно, универсального механизма нет.

| Требование | Код | Интеграция | Автотесты | Stage | Production | Откат |
|---|---|---|---|---|---|---|
| Этап 0: импорт действующего кода | baseline b89038b | импортирован в main | 303 passed/6 skipped | изолированный API пройден | Python+HTML SHA сопоставлены | нет |
| Сбор Telegram групп/каналов | в baseline | collector работает | общий CI, нужен перечень сценариев | live Telegram не проверен | счётчики растут | нет |
| Автообнаружение источников/новостных каналов | source registry в baseline | нужно сверить флаги и цикл | нужен адресный аудит | не подтверждено | не подтверждено | нет |
| Выбор групп для мониторинга | PR12 объединён с main | dashboard/collector selection и отдельная ограниченная source-control роль установлены; запись UI включена | 310 tests + Chromium; колонковые права и source decisions на stage | PostgreSQL synthetic decisions/API и least-privilege grants пройдены | GET/POST готовность/права подтверждены 09:41 UTC; реальное переключение конкретной группы и отсутствие будущих сообщений ещё не проверены | сохранён прежний dashboard + rollback.sh; collector rollback отдельно |
| sender_user_id | код и колонка присутствуют | заполнение подтверждено | общий CI + PostgreSQL contract | sender/backfill/API пройдены | 344 строки с ID; API проверен на synthetic stage | нет |
| Сбор/хранение/просмотр комментариев | baseline + исправление c2bde01 | collector исправлен; UI/таблицы присутствуют | регрессия comments + driver rowcount | PostgreSQL insert/dedup/failure/API пройдены | полный проход не подтверждён | нет |
| Дедупликация/статистика/дневная сводка/сигналы | API в baseline | нужна проверка полезных данных | общий CI | изолированный API пройден | не подтверждено | нет |
| OpenAI, бюджет и глубокий анализ | код в baseline | бюджет не изменять | нужны адресные сценарии | без платных запросов | обработка растёт; deep не доказан | нет |
| Устойчивость очереди/lease/retry | код в baseline | worker работает | нужен адресный аудит отказов | не подтверждено | просроченных lease нет | нет |
| Sherlock username/авторизация/сеть/история | main PR17/18, runtime blob68e9b311 | расширенный sidecar и proxy установлены 13:46 UTC | 332 passed/21 skipped; offline auth/queue/report | свежий v2 job, реальный GitHub, история, legacy recent | свежий v2 job → done → контрольный профиль + история; legacy recent200 | конкретный image rollback отрепетирован; старые контейнеры сохранены |
| Sherlock новая вкладка/Enter/history/popup | PR13 объединён с main | новый dashboard установлен | Chromium: Enter/popup fallback/history/card пройдены | API + Chromium fixtures | auth HTML + result/history API пройдены; browser production не проверен | прежний stopped container + rollback.sh |
| username/телефон/email/ФИО/текст: ввод | PR #13 | классификация ввода ≠ источник поиска | перепроверить | не подтверждено | не подтверждено | нет |
| Телефон/email/ФИО/текст: серверные публичные источники | не реализовано по реестру PR #13 | ссылки Google/Bing не интеграция | нет | нет | нет | нет |
| Применимые CLI-опции Sherlock | PR17 merged319c85b + fix PR18 merged207bd64 | /sherlock подключён к расширенному sidecar | flags/валидация/proxy/catalog unit checks; Chromium | site GitHub/timeout5, batch и {?} проверены; NSFW/exclusions не проверялись на внешних сайтах | одиночный GitHub поиск/экспорты проверены; остальные опции не объявлены полностью проверенными | rollback.sh + retained IDs, репетиция на изолированных контейнерах |
| Этап 1: контекстные подмены confusable_homoglyphs/huntglyph | ru-context-v1 + authenticated API | on-demand API и ручная UI проверка установлены; индекса поиска ещё нет | 19 целевых passed | PostgreSQL original-preservation/API пройдены | API проверен на существующем сообщении | прежний dashboard + rollback.sh |
| Этап 2: Lingua/LibreTranslate/Argos | кандидаты | Armenian и ресурсы не подтверждены | нет | нет | нет | нет |
| Этап 3: Uptime Kuma/Apprise | кандидаты при необходимости | локальный контроль есть; оповещения не подключать автоматически | нет | нет | нет | нет |
| Этап 4: PaddleOCR/ImageHash, затем Docling | кандидаты | ресурсы/данные не оценены | нет | нет | нет | нет |
| Этап 5: pgvector/SentenceTransformers, затем BERTopic | кандидаты | после оценки ресурсов | нет | нет | нет | нет |
| Этап 6: Trafilatura, затем RSSHub; TGStat | кандидаты | TGStat доступ/стоимость не подтверждены | нет | нет | нет | нет |
| Этап 7: changedetection.io | кандидат | не подключён | нет | нет | нет | нет |
| Этап 8: faster-whisper | кандидат | ресурсы не оценены | нет | нет | нет | нет |
| Этап 9: GLiNER | кандидат | ресурсы не оценены | нет | нет | нет | нет |
| Этап 10: Cytoscape.js | кандидат | связи только с доказательствами, не подтверждение личности | нет | нет | нет | нет |
| Этап 11: Maigret | кандидат | только после измерения пользы поверх Sherlock | нет | нет | нет | нет |
| Promptfoo / Langfuse | кандидаты | Langfuse после стабилизации; не добавлять аккаунты автоматически | нет | нет | нет | нет |
| Приложения Mac/Windows | запланировано | после стабилизации API | нет | нет | нет | нет |
| Ручной анализ последних сообщений до1000 | PR14/15 в main | production включено по журналу 11:40 | Python/Chromium из runs42018789298 | PostgreSQL CHECK1000/HTTP202/1001 rejection пройдены | flags/ACL/лимит подтверждены; новый реальный запрос1000 не создавался | retained containers + deployment rollback |
| Sherlock расширенный web/batch/export | PR17+18 в main; /sherlock | установлен 13:46 UTC | Chromium +332 passed/21 skipped | job/история/4exports/auth/CSRF/batch/{?} пройдены | auth page/capabilities/job/история/4exports пройдены; production browser отдельно не эмулировался | исполнимый откат подготовлен до замены и отрепетирован |
| Общая регрессия всех функций | не завершено | зависит от строк выше | не выполнена | нет | нет | нет |


| Sherlock шаблон {?} и перекрывающиеся варианты | единое expanded_usernames в PR18 | CLI и чтение CSV используют один список, исходный ввод сохранён | регрессия задания целиком: 3 конкретных CSV, без двойного подсчёта | реальные pattern+literal задания прошли, ≥4 concrete usernames | исправленный blob совпадает с main; отдельный live wildcard job не запускался | общий откат двух компонентов отрепетирован |
| Sherlock JSON/TXT/CSV/XLSX | серверные экспорты и формульная защита | proxy и browser download подключены | unit + Chromium 4 download | все4 непустые, JSON done/XLSX ZIP | все4 получены после свежего production job | прежние образы и история сохранены |
| Sherlock admin proxy/local catalog | параметры и fail-closed валидация реализованы | без новых прокси/аккаунтов/каталогов | unit positive/negative paths | текущие admin настройки не включались | реальный proxy/custom catalog не проверен, ресурсы/конфигурация не подтверждены | текущая конфигурация сохранена |

## Следующие действия и ограничения

1. Завершены подтверждение локального расписания и isolated stage. Одноразовые шаги удалены из maintenance workflow; повторный stage требует явно закреплённого SHA кандидата.
2. Baseline импортирован с сохранением актуальных ops. CI main после импорта прошёл полностью: 303 passed, 6 skipped, сборка Sherlock и cloud dependency smoke на Python 3.11/3.12 успешны. Не подменять рабочее развёртывание новым Compose.
3. Для будущего обновления приложения сохранить текущие образы и защищённую конфигурацию, сделать свежую резервную копию; подготовить и проверить конкретный исполнимый откат приложения. Старый дамп поверх production не восстанавливать.
4. Проверить полное прохождение комментариев и sender ID; выполнить ограниченный Sherlock username поиск с проверкой результата, не только HTTP health.
5. Затем интегрировать PR #12/#13 по зависимостям и контекстное обнаружение подмен. Уточнять матрицу доказательствами каждого сценария.

Внешние блокеры: для дополнительных источников телефона/email/ФИО пока нет подтверждённого источника/API/авторизации; нельзя объявлять поисковые ссылки реализацией или совпадения подтверждением личности. Тяжёлые модели и платные сервисы требуют подтверждения ресурсов/авторизации. Это не блокирует независимую работу над baseline и тестами.

## Durable image candidate 2026-11-05 08:01 UTC

На сервере собран tm-v861-collector-count-fix:20261105, image sha256:e86d8a1118bd0919223278aab3af61a0ec65f6f3bb1467a6c6d8eb010f42d999. Base закреплён прежним image ID; Docker build не скачивал зависимости. В образ добавлен только проверенный comments.py. Import+SHA256 проверены в read-only контейнере без сети/сессий/БД, временный контейнер удалён. Production на новый образ ещё не переключён; текущий collector уже содержит адресное исправление. Следующий шаг — воспроизводимое пересоздание с сохранением точной конфигурации и проверкой возврата прежнего контейнера.

Доказательство: https://github.com/Xrenus1991/TelegramMonitor/actions/runs/41991278559

## Production durable image 2026-11-05 08:05 UTC

Collector пересоздан на image sha256:e86d8a1118bd0919223278aab3af61a0ec65f6f3bb1467a6c6d8eb010f42d999. Предварительно проверена конфигурация stopped-контейнера (Env/Cmd/Entrypoint/User/WorkingDir, mounts), затем сделаны свежий backup и isolated restore. Прежний collector сохранён остановленным для возврата; rollback.sh и защищённые снимки: /var/lib/tm-actions/monitor-autonomy/deployments/image-20261105T080417Z. Новая Telegram-сессия не создавалась; одновременно работал только один collector. После переключения проверены image ID, исходный модуль, Running повторно через25секунд; новое сообщение сохранено08:04:54 UTC, итог4697 done, pending/processing/expired leases=0. Исправление теперь входит в image, а не только writable layer. Compose-путь автоматического пересоздания исходного ручного контейнера отдельно не установлен; image/config/rollback зафиксированы локально.

Доказательство: https://github.com/Xrenus1991/TelegramMonitor/actions/runs/41991649100

Следующий пункт этапа0: полный ручной job Sherlock через dashboard proxy с проверкой результата/history, затем интеграция PR12/13 по зависимостям. Этап1 confusables пока не объявлен выполненным.

## Sherlock production API E2E 2026-11-05 08:07 UTC

Через работающий dashboard8098 и его proxy создано новое задание octocat (initial queued), получено done, 137 результатов; контрольный публичный GitHub профиль найден. Повторный GET recent содержит тот же job ID и state done. Проверены401 без dashboard auth,403 без same-origin action headers,422 для неправильного username. Секреты использованы только внутри dashboard; job IDs/история других запросов/полные результаты не опубликованы. Изменений приложения не было. Это подтверждает dashboard API→proxy→bridge→внешний поиск→result→history; не подтверждает браузерные клики, popup/Enter/новую вкладку из PR13 или личность человека.

Доказательство: https://github.com/Xrenus1991/TelegramMonitor/actions/runs/41992024218

Следующий шаг: сверить PR12/PR13 с main, собрать совместный кандидат и проверить browser/UI и stage без изменения production. Дополнительные источники телефона/email/ФИО пока не реализованы.

## Combined groups and Sherlock 2026-11-05 08:14 UTC

Кандидат4410ee0 из audit/combined-groups-sherlock-20261105 объединяет изменения PR12/13 поверх актуального main и сохраняет исправление comments.py.310 passed,6 skipped,5 subtests; Sherlock build и cloud dependency smoke3.11/3.12 пройдены. Chromium с синтетическим API подтвердил фильтры источников, Enter, blocked-popup fallback, открытие history card, detail route и отсутствие запросов к Sherlock при email input. Это браузерный функциональный тест с fixtures, не production-browser проверка.

Stage на сервере: восстановлено4696 сообщений,12 HTTP маршрутов,401/422, PostgreSQL contracts sender/comments и source selection persistence/API прошли. Synthetic source control использует admin только в disposable contract DB; отдельная ограниченная production роль ещё не проверена/не создана. Production доступы не менялись. Свежая диагностика08:13:4710done, очередь/expired leases0, HTTPhealth200 и Sherlock CLIcontrolpassed. GitHub cron остаётся ненадёжным; локальные проверки/maintenance свежие.

Код перенесён в main после этих проверок; production новых функций ещё не обновлён. API source control выключен без явно настроенного отдельного URL; не выдавать наличия кода за доступность кнопок production. Телефон/email/ФИО/текст пока только поисковые ссылки; серверная интеграция отсутствует.

Доказательства:
- CI https://github.com/Xrenus1991/TelegramMonitor/actions/runs/41992621344
- Chromium https://github.com/Xrenus1991/TelegramMonitor/actions/runs/41992621382
- Server stage https://github.com/Xrenus1991/TelegramMonitor/actions/runs/41992803308

Следующий шаг: новый образ dashboard, snapshot/backup и проверяемый rollback; затем изолированная проверка новой версии и установка без изменения production ролей. Для включения source-control необходима существующая разрешённая роль либо отдельно разрешённое изменение доступов; этого не предполагать.

## Dashboard production release 2026-11-05 08:18 UTC (12:18 Армения)

Объединённый dashboard установлен на image sha256:ae2972061dfb06a8bf987385ba3f05c125fc3d7064c7ca260e305ae4cba7ef30 из кандидата4410ee0. Исходный read-only bind /stage заменён кодом внутри immutable image; Env/Cmd/Entrypoint/User/WorkingDir сохранены и проверены preflight. Образ прошёл isolated import без сети. Перед переключением выполнены свежие backup/isolated restore, прежний dashboard сохранён остановленным; исполнимый rollback.sh и защищённая конфигурация лежат в /var/lib/tm-actions/monitor-autonomy/deployments/image-20261105T081731Z. Автоматический возврат при неудачной проверке был предусмотрен, не потребовался.

Production проверка: authenticated HTML200, health200, наличие source-search/sherlock-type/detail card code, повторная проверка после20сек. Sherlock proxy result/history passed,137results, контрольGitHub найден; initialstate done означает reuse результата прежнего контрольного job, не новый внешний запуск. Проверены401/403/422. Браузерные проверки сfixtures были наGitHub-hosted Chromium; реальные browserclicks production отдельно не проверены.

Новые доступные UI возможности: фильтры/поиск источников; Sherlock username detailcard/newtab/Enter/history/popupfallback; выбор иных типов ввода с ЧЕСТНО обозначенными ссылками публичного поиска. Телефон/email/ФИО/текст серверным поиском не стали. Source-control production доступы не менялись, роль/URL не создавались; не утверждать, что кнопки изменения уже разрешены. Collector пока образ count-fix с прежним алгоритмом отбора источников, новая active_entity_map изPR12 ещё не установлена — полная функция выбора групп остаётся частичной.

Доказательство: https://github.com/Xrenus1991/TelegramMonitor/actions/runs/41993279006

Следующий шаг: проверить наличие уже разрешённой роли source-control без вывода credentials; при отсутствии остановить только production запись и явно зафиксировать блокер. Независимо можно подготовить collector source-selection image и изолированную проверку, затем этап1 confusables после закрытия основных критериев этапа0. Все прочие этапы матрицы остаются незавершёнными.

## Collector source selection production 2026-11-05 08:22 UTC

Собран и установлен image sha256:0d83d4c77060f6f6024f98a10c6dd5fc55f051f552d708ce82ff3bb0a5b64912. Поверх прежнего count-fix образа добавлены только collector.py/source_registry.py из проверенного кандидата4410ee0. Изолированный тест без сети подтвердил исключение blocked и opt-in strict allowlist. До переключения: точная конфигурация preflight, fresh backup/restore, прежний контейнер сохранён stopped, rollback.sh в /var/lib/tm-actions/monitor-autonomy/deployments/image-20261105T082137Z. После: image ID/collector blob проверены дважды, новое поступление08:21:54,4723done, pending/processing/expired leases0. Никакие source statuses, права, Telegram-сессии и лимиты не изменялись. Проверка не переключала реальные группы для теста.

Подтверждённый блокер production UI write: control_url_configured=false, source_writes_enabled=false. Отдельное подключение CLOUD_SOURCE_CONTROL_DATABASE_URL отсутствует. Для пользовательских кнопок требуется отдельно разрешённая ограниченная роль: SELECT IDs/status, UPDATE толькоstatus, INSERT audit; не использовать analytics credentials. Создание/выдача прав не входит в текущую установку с запретом изменения доступов. Не считать полную интерактивную функцию завершённой, несмотря на пройденные stage API tests.

Доказательство: https://github.com/Xrenus1991/TelegramMonitor/actions/runs/41993804998

Следующий независимый пункт: подготовить спецификацию/оценку этапа1 confusable_homoglyphs/huntglyph с исходным текстом и защитой URL/username; не блокировать её только из-за UI control role. Для управления источниками подготовлена документация docs/GROUP_MONITORING_SELECTION_RU.md, production grants автоматически не выполнять.

## Contextual Unicode prototype 2026-11-05 08:31 UTC

Добавлен optional модуль unicode_context, confusable_homoglyphs3.3.1; производственный smart_text не заменён, библиотека в live контейнеры не установлена.15 целевых тестов пройдены после исправления отсутствующей прямой Greek→Cyrillic связи. Общая регрессия312passed/19skipped, из13optional skipped все отдельно проверены. Изолированный server image:4positive/4preservation cases,20вызовов*4000символов,12.075сек с tracemalloc на0.5CPU,peak89893байта послеимпорта. Подробности/ограничения: docs/integrations/CONTEXTUAL_UNICODE_RU.md. Huntglyph оценён по первичным исходникам как дополнительный CLI, эмпирическая польза пока не доказана; runtime не подключён.

Сервер08:30UTC:4744done, pending/processing/expired leases0, последнее поступление08:29:49; dashboard/Sherlock health200, external control passed, source control URL по-прежнему отсутствует. Требование новых production доступов остаётся блокером только записи UI, независимая разработка продолжается.

Доказательства: https://github.com/Xrenus1991/TelegramMonitor/actions/runs/41994823064 ; https://github.com/Xrenus1991/TelegramMonitor/actions/runs/41994823210 ; https://github.com/Xrenus1991/TelegramMonitor/actions/runs/41995024977

Следующий шаг: расширить контрольный набор и подключить detector opt-in к производным данным поиска без изменения оригиналов. Этап1 целиком НЕ завершён: нет appintegration/production/отката нового обработчика.

## Unicode derived API production 2026-11-05 08:39 UTC

Добавлен и установлен GET /api/text/variants?key=channel:message. Авторизация dashboard обязательна. Функция включена CLOUD_TEXT_VARIANTS_ENABLED=true только в новом dashboard; библиотекаconfusable_homoglyphs3.3.1 входит в image sha256:6c546079fb450fc039ae72a341424bf798152064e32c30694016fec9da11797b. Кандидат3619201 прошёл19целевых tests (15detector+4API), общая регрессия314passed/21skipped; optional cases отдельно пройдены.

SQL возвращает только первые4001символ и полную длину одной записи поPK, без записи/сканирования всейтаблицы. API возвращает original_excerpt/normalized_excerpt до4000позиции, source_length,truncated,findings,version,persisted=false. Раздельные производные данные считаются при запросе, не сохраняются и не меняют исходный текст, классификацию или бюджет. При отсутствии модуля или disabled —503; безauth401, неизвестныйkey404, неверныйkey422. Граница длинного токена не нормализуется. Это пока НЕ полнотекстовый поиск по вариантам и НЕ браузерный элемент UI.

Stage: restored4722строки;12existingHTTPмаршрутов, отдельный PostgreSQL contract проверил новуюнормализацию и совпадение оригинала после вызова. Production: freshbackup/restore08:39; прежний контейнер остановлен и сохранён с rollback.sh /var/lib/tm-actions/monitor-autonomy/deployments/image-20261105T083904Z. Новый dashboard дважды проверен по image/health/authHTML/API: выбрано существующее сообщение под read-onlyролью, APIвернул версию,persistedfalse и ограниченнуюдлину; исходник/ключ не опубликованы.4750done, очередь и expiredleases0. Не изменены collector/worker/Telegramсессии/DBправа/лимиты.

Доказательства:
-19tests https://github.com/Xrenus1991/TelegramMonitor/actions/runs/41995653317
-regression https://github.com/Xrenus1991/TelegramMonitor/actions/runs/41995653177
-stage https://github.com/Xrenus1991/TelegramMonitor/actions/runs/41995889251
-install https://github.com/Xrenus1991/TelegramMonitor/actions/runs/41996074755

Следующий шаг: показать производные данные в UI на ручной запрос и проверить browserUX; отдельно проектировать индекс вариантов с сохранением версии без перезаписиоригинала. Не объявлять весь этап1завершённым: huntglyphэмпирически не сравнен, нетиндекса/поисковой интеграции. Блокер source-control отдельнаярольпо-прежнемунеустранён.

## Source-control production activation 2026-11-05 09:41 UTC (13:41 Армения)

Управление группами через UI включено в production workflow `tm-server-maintenance.yml`, run42004272729 (commit dbbbbe1536abaecdc637ae7776d3c20d4a035a0f), jobs validate/inspect-server success.

- Изолированная восстановленная PostgreSQL прошла 12 HTTP проверок (4750 исторических записей) и synthetic contract: source_selection_persistence_and_api=true, source_control_least_privilege=true. Admin URL использовался только для одноразового создания тестовой роли в disposable contract DB; проверка POST проводилась с ограниченной ролью.
- В production выполнен свежий backup + проверка isolated restore, создана роль `tm_v861_source_controller` с SELECT `peer_id/status`, UPDATE только `status`, INSERT ограниченных колонок аудита. Проверка login/SQL privileges успешна. Credentials и конфигурационные снимки остаются в защищённом каталоге сервера, в GitHub не выгружались.
- Контейнер Dashboard пересоздан с `CLOUD_DISCOVERY_UI_WRITE=true` и отдельным `CLOUD_SOURCE_CONTROL_DATABASE_URL`. Подтверждено `SOURCE_CONTROL_ACTIVE=true`, `SOURCE_MANAGEMENT_EDITABLE=true`; повторная проверка `control_url_configured=true`, `source_writes_enabled=true`, `can_update_source_status=true`, `can_update_messages=false`.
- Старый Dashboard остановлен и сохранён для rollback; скрипт отката сформирован в серверном защищённом deployment-каталоге. Telegram Collector/worker, сессии, бюджет OpenAI и RentWeeklySender не изменялись этим запуском.
- **Ограничение доказательств:** live-тест выключения выбранного Telegram-источника и отсутствия его новых сообщений ещё не выполнен; реальные статусы групп не изменялись во время автоматического теста. Наличие кнопок и успешная проверка роли не доказывают этот end-to-end сценарий. Проверять на явно выбранной тестовой группе, сохранив прежний статус, затем восстановить его.
- Строгий режим `CLOUD_SOURCE_SELECTION_STRICT` автоматически не включён — чтобы не остановить мониторинг ранее настроенных групп.

Доказательство: https://github.com/Xrenus1991/TelegramMonitor/actions/runs/42004272729

Следующий шаг: провести безопасную ручную проверку выбранной группы через UI (сохранение статуса/аудит, затем наблюдение Collector за новыми сообщениями без отправки сообщений от имени аккаунта), после чего обновить матрицу end-to-end.

## Operator UI confirmation 2026-11-05

Пользователь подтвердил в текущем диалоге, что кнопки включения и отключения мониторинга в рабочем Dashboard работают, а выбранный статус **сохраняется после обновления страницы**. Это дополнительное подтверждение UI→API→persist→GET, в дополнение к успешному CI/production readiness run42004272729; не содержит названия группы, ID или чувствительных данных.

Аудит актуального collector.py (main): активные источники пересчитываются с `registry.selection_ids()`; статусы disabled/left исключаются функцией `active_entity_map` и из статических источников; `entities` изменяется на месте. Исходный код и регрессионные тесты это подтверждают, но **не доказывают** live end-to-end отсутствие новых сообщений в конкретной группе после отключения.

Остаток проверки: на разрешённой тестовой группе с ожидаемой активностью сверить время/ID последнего нового сообщения до отключения и после, дождаться очередного цикла Collector, проверить отсутствие новых сохранений и затем восстановить исходное состояние. Не посылать тестовые сообщения от имени пользователя и не выключать без его выбора реальные значимые группы. Строгий режим автоматически не включать.

## Ручной анализ последних N сообщений по группе — production 2026-11-05 10:55 UTC

Реализована кнопка `Последние [1–200] / Анализировать` рядом с управлением мониторингом группы. Отдельная кнопка создаёт одну `pending` заявку на группу; Collector читает максимум N последних сообщений, не сбрасывает checkpoint и не переанализирует уже классифицированные записи. Вставка только новых сообщений в обычную очередь Worker; его бюджет OpenAI не менялся. Статус `done` относится к завершению досканирования, не к завершению OpenAI классификации.

Код `telegram_monitor/cloud/{source_registry.py,collector.py,api.py,dashboard_v8_3.html}`, таблица `tm_source_recent_requests` и миграция `sql/source_recent_requests.sql`, docs `RECENT_GROUP_ANALYSIS_RU.md`. Вливание PR14 в main: fc8184e9a9735f6fe143fe97fa26e5dd9d4151f3. Флаг `CLOUD_SOURCE_RECENT_ENABLED=true` включён только в обновлённых runtime Collector и Dashboard после сохранения старых контейнеров для отката.

Проверки:
- 11 Python тестов и настоящий Chromium `RECENT_GROUP_UI_CHROMIUM_OK`: https://github.com/Xrenus1991/TelegramMonitor/actions/runs/42011640119
- PostgreSQL stage, изолированный network, восстановлено 4844 исторических сообщения: schema+ACL+HTTP202+request lifecycle+unchanged messages, `STAGE_RECENT_POSTGRES_OK`: https://github.com/Xrenus1991/TelegramMonitor/actions/runs/42013182998
- Production run validate / postgres-stage / production success: https://github.com/Xrenus1991/TelegramMonitor/actions/runs/42014280266. Контрольные метки: `RECENT_BOTH_IMAGES_PREFLIGHT_OK`, `RECENT_FRESH_BACKUP_AND_ISOLATED_RESTORE_OK`, `RECENT_PG_ADDITIVE_MIGRATION_OK`, `RECENT_SOURCE_ANALYSIS_ACTIVE=true`, `RECENT_ROLLBACK_AVAILABLE=true`. Старая source-control роль осталась ограниченной (`can_update_messages=false`, `can_update_source_status=true`).
- Запуск реального запроса к Telegram из UI с изменением счётчиков N **пока не выполнялся**; отсутствие 0 новых записей не означает ошибку: если последние N сообщений уже есть в базе, очередь останется без изменений. Строгий выбор групп не включался автоматически.

Следующий этап: через пользовательский Dashboard выбрать разрешённую тестовую группу, запросить последние 20–50 сообщений, проверить статус `done` и числа просмотрено/новых в очереди, отдельно проверить штатную обработку новых через Worker. Не отправлять тестовые сообщения или не переанализировать уже done автоматически. Не изменять RentWeeklySender.

## Повторная проверка развёртывания recent N — 2026-11-05 11:01 UTC

Проверена идемпотентность повторного deployment workflow: https://github.com/Xrenus1991/TelegramMonitor/actions/runs/42015078011 . Все три job (validate, postgres-stage, production) завершены success. Production не пересоздавал контейнеры: `RECENT_ALREADY_ACTIVE_AND_VERIFIED=true` после сверки файлов с pinned candidate, действующих флагов, работоспособности Collector и HTTP API Dashboard. Дополнительная проверка роли: `can_update_source_status=true`, `can_update_messages=false`. Ранее успешное первичное развёртывание: run42014280266. Промежуточные fail-closed попытки повторной установки были остановлены защитой версии; повторная проверка теперь идемпотентна. Реальный ручной запрос последних N Telegram сообщений ещё не выполнялся в рамках этих автоматических smoke тестов.

## Расширение ручного анализа последних сообщений до 1000 — production 2026-11-05 11:40 UTC

Лимит запроса последних Telegram-сообщений для отдельной включённой группы повышен с 200 до **1000** (UI поле 1–1000, FastAPI StrictInt максимум 1000, SourceRegistry.RECENT_MAX=1000, Collector 1000, PostgreSQL CHECK message_count/scanned_count/saved_count 1000). Все ограничения существующего бюджета, ограничения на число заявок и повторное использование ранее классифицированных сообщений сохранены.

Код: PR #15 (squash merge в main `12a1859355b328494e0f91cf1aff07afa3f79749`). CI на feature `42018789298`: Python tests + Chromium success. Реставрационный stage `42020468735`: `STAGE_RECENT_1000_POSTGRES_OK`, 4874 сообщений из изолированной копии БД не изменены; migration проверена с имитацией старого CHECK 200, включая повторный запуск и HTTP 202 для `count=1000`, отклонение 1001, защиту ролей.

Production `42020468735` (all validate / postgres-stage / production success): `RECENT_BOTH_IMAGES_PREFLIGHT_OK`, `RECENT_FRESH_BACKUP_AND_ISOLATED_RESTORE_OK`, `RECENT_1000_PG_CONSTRAINT_MIGRATION_OK`, `RECENT_SOURCE_ANALYSIS_LIMIT=1000`, `RECENT_1000_ROLLBACK_AVAILABLE=true`. После обновления Dashboard и Collector проверены дважды; права DB source-controller по-прежнему `can_update_messages=false`, `can_update_source_status=true`. Новый реальный запрос 1000 Telegram-сообщений через UI в этой автоматической проверке **не создавался** (чтобы исключить неожиданные расходы OpenAI). Для пользовательского теста обновить Dashboard и выполнить «Последние → 1000 → Анализировать» на выбранном источнике, затем смотреть `scanned_count`/`saved_count` и состояние Worker.

Доказательства:
- GitHub source: https://github.com/Xrenus1991/TelegramMonitor/pull/15
- GitHub Actions stage + production: https://github.com/Xrenus1991/TelegramMonitor/actions/runs/42020468735

## Unicode manual UI production 2026-11-05 12:31 UTC (16:31 Армения)

Завершена ранее подготовленная, но не установленная UI часть этапа1. Старый кандидатf91ec69 содержал HTML до недавнего управления группами; он НЕ развёртывался. Его минимальные вставки перенесены на актуальный mainHTML fa422e9 и проверены в новом кандидате3ce72fd. Сопоставление runtime: API1a6f11a и HTMLfa422e9 совпали с актуальным репозиторием, base dashboard image93bd0a6. Новый image7e0590084089f9391ccc230289c6387de3f81b568b4c3e119f23d4a7f9826a81 добавляет толькоHTML, остальные компоненты production сохраняются.

Новая кнопка «Проверить подмены букв» в карточке сообщения вызывает авторизованный GET только понажатию. Показывает original/normalized excerpts, findings,version,ограничение4000; сводка модели не меняется. Collapse/reopen повторно не запрашивает уже полученные данные в той же карточке. Ошибка503 обрабатывается без утверждения об отсутствииподмен; возможна явная повторнаяпопытка. Text nodes защищают выводотHTMLinjection.

Проверки:
- Chromium с fixtures: manualrequest/noautoquery,collapse/cache,retry503,original/normalized/version/truncation,нетисполняемогоimgHTML,сводканеизменна. Дополнительно прошли source filters иSherlock Enter/history/popup tests: https://github.com/Xrenus1991/TelegramMonitor/actions/runs/42027130368
- Общаярегрессия323passed/21skipped (Unicode optional cases отдельно ранее19passed), Sherlock image/import3.11/3.12: https://github.com/Xrenus1991/TelegramMonitor/actions/runs/42027130131
- Serverstage:4904 restored rows,12HTTPмаршрутов,restrictedsourcecontrol+originalpreservation contracts пройдены: https://github.com/Xrenus1991/TelegramMonitor/actions/runs/42027474506
- Production: freshbackup+isolatedrestore,preflight config,двапроверочныхцикла,image/health/authHTML/UnicodeAPI. Дополнительно sourceeditable=true,recentenabled=true,Requestcount1000валиден/1001отклонён; реальныйзапросTelegram1000неотправлялся. limitedrolecan_update_messages=falseпослеустановки. https://github.com/Xrenus1991/TelegramMonitor/actions/runs/42027769776

Rollback: прежний dashboard сохранён stopped, исполнимыйrollback.sh иprivateinspect лежат /var/lib/tm-actions/monitor-autonomy/deployments/image-20261105T123110Z. Newimageactivationнепотребовалаотката; production browserclick не эмулировался, толькоauthHTTP/API+проверенныеChromiumfixtures.

Сервер12:31UTC:4912done,новыйприём12:31:25,queue/expiredleases0;collector/worker/database healthy, dashboard/Sherlockhealth200 иboundedexternalGitHubcontrolpassed. Локальныйcron12:22exit0; свежийmaintenanceAUTONOMY_OK. GitHubscheduledautonomyпоследнийуспех09:49UTC — старше90мин, fallbackлокальногоcronработает; не объявлятьGitHubcronисправленным. Дампы/credentials/source statuses/лимиты иTelegramсессии не публиковались/не менялисьэтимвыпуском.

Актуализацияпрочихтребований: управлениеисточниками ужеактивированоразрешённымотдельнымзапуском09:41 и подтвержденопользователем; прежний blockerотсутствияроли устранён. Лимитmanualrecent1000установлен11:40. PR17 расширенногоSherlock открыт, sha64e945f, вproductionнеустановлен; требует отдельногоbrowser/sidecarstage/rollback, ссылкипоphone/email/nameнеявляютсяserversearch.

Следующий шаг поэтапу1: bounded поиск по производным вариантам с версионированием без перезаписиоригиналов; huntglyph остаётсякандидатомдоэмпирическогосравнения. СогласованныйPR17учтёнвматрице, не считать реализованнымпоналичиюкласса/CI.

## Адресная проверка комментариев — 2026-11-05 12:35 UTC

Старое время последнего scan проверено без чтения/публикации сообщений и без изменения source statuses. Из двух каналов в comments allowlist оба сейчас имеют disabled/left в реестре выбора; активных каналов для comments нет. Остановка comment scans соответствует пользовательскому выбору, а не является доказательством зависания. Сборщик не перезапускался. На момент проверки 4914 сообщений done, pending/processing/expired leases=0, последнее поступление 12:34:54 UTC. Свежий AUTONOMY_OK, source-control ограниченная роль и Sherlock control исправны.

Доказательство: https://github.com/Xrenus1991/TelegramMonitor/actions/runs/42028384562


## Расширенный Sherlock установлен — 2026-11-05 13:46 UTC (17:46 Армения)

Прочитаны актуальные ops инструкции/main и maintenance workflow, дорожная карта, этот журнал, IMPLEMENTATION_STATUS_RU.md из feature/sherlock-user-detail-tab, статусы PR13/17/18 и последние безопасные результаты Actions. PR17 уже merged319c85b; PR13 остаётся open, хотя его проверенные UI изменения ранее импортированы в main. Старые статусы «PR17 кандидат» выше являются историей, а не текущим состоянием.

Устранены подтверждённые ошибки:
- Deployment probe ошибочно обращался к route.path у lazy FastAPI _IncludedRouter. Заменён на безопасную проверку getattr; добавлены unit-тесты. Ранее failed deployment42031441553 остановился до изменения приложения.
- Дополнительный реальный stage42037671107 воспроизвёл отказ username шаблона {?}: CLI формировал конкретные CSV, сервер ожидал буквальное имя шаблона. PR18 раскрывает варианты _, - и . единым списком для CLI и чтения отчётов, удаляет перекрытия, сохраняет исходный ввод в истории. Не подменяет username подтверждённой личностью.
- При усилении preflight мной была допущена ошибка сравнения tuple3/tuple4; она исправлена до установки. Три адресных теста проверяют совпадение исходного диска истории, отклонение другого диска и закрепление имени volume. Фактический source диска при диагностике совпал; рабочие контейнеры в failed prepare не менялись.

Код: PR18 https://github.com/Xrenus1991/TelegramMonitor/pull/18 , candidate071fbc0945966a0c29d7bf4fad20c67287c5a8c0, merged main207bd64f488b0358f4ccd832513cb189aea232d0.
Проверки:
- CI332 passed,21 skipped,5subtests; реальные cloud imports3.11/3.12 и image smoke success: https://github.com/Xrenus1991/TelegramMonitor/actions/runs/42038019574
- Advanced Docker contract success: https://github.com/Xrenus1991/TelegramMonitor/actions/runs/42038019930
- Chromium: batch guard, параметры запроса, добавление сайта без дубля, история, статусы/фильтр, безопасные text nodes/URL, 4downloads и retry503; success: https://github.com/Xrenus1991/TelegramMonitor/actions/runs/42037886631
- Server stage с отдельной SQLite из копии истории, синтетическими credentials, отдельной сетью, без production DB/Telegram/OpenAI: свежий v2 job/GitHub control, история, 4exports,401/403/422; дополнительно реальные batch + wildcard с конкретными отчётами. Подготовка+stage+rehearsal success: https://github.com/Xrenus1991/TelegramMonitor/actions/runs/42037963379

До замены приложения:
- Установлено соответствие прежних image IDs и актуального runtime fingerprint; candidate api/HTML сохраняют группу/лимит1000/Unicode.
- Сохранены protected before.inspect.json, payloads, SQLite history backup, прежние образы закреплены tags; полные конфигурации и дампы остались только на сервере.
- Свежий PostgreSQL dump1303085bytes восстановлен в изолированном PG:4970messages. Роли сохранены, восстановление ролей НЕ проверено.
- rollback-manifest.json, rollback.py и исполнимый rollback.sh созданы ДО остановки сервисов; алгоритм возврата retained container ID отрепетирован на временных реальных контейнерах. Stopped preflight проверил Env/User/Entrypoint/Cmd/WorkingDir, exact mount source/RW, readonly rootfs и отсутствие published ports.
- Конкретная процедура: /var/lib/tm-actions/monitor-autonomy/deployments/sherlock-advanced-20261105T134559Z/rollback.sh . Возвращает только два приложения к retained IDs; PostgreSQL не восстанавливает. Это процедура данного выпуска, не универсальный откат.

Production release success: https://github.com/Xrenus1991/TelegramMonitor/actions/runs/42038616205 .
Установлены dashboard image72644742a0db592815ce30767509dd68fbac11927ea4d582110f996705321c5b и sidecar imaged95f8fcfdeabc5b545b04dbe1db99f643bc2ac99c86bfbb800c885376612b372.
После замены выполнен НОВЫЙ реальный v2 поиск только публичного контрольного username/GitHub: done, ожидаемый профиль найден, history совпала, JSON/CSV/TXT/XLSX получены. Проверены401 без auth,403 без origin,422 для6 usernames, legacy recent200, stats/events/sources200. После15сек оба сервиса проверены повторно. Коллектор ID не менялся.
Дополнительно sourceeditable/recentenabled=true,1000валиден/1001отклонён, Unicode button сохранена. Реальные заявки Telegram1000 не создавались. История Sherlock сохранилась в прежнем volume. Новые tokens/аккаунты/платные источники/прокси не подключались.

Финальная read-only проверка13:49UTC:4971messages done, pending/processing/old_pending/expiredleases=0, последнее поступление13:48:27; collector/worker running,PGhealthy, HTTPhealth dashboard/Sherlock прошли при выпуске. Локальный cron13:37exit0 и свежий AUTONOMY_OK. GitHub scheduled autonomy последний известный успех09:49UTC остаётся старше90мин; локальный fallback и maintenance работают, GitHubcron не объявляется исправленным.
После установки current images и advanced blob68e9b311b532faa7caadb15b53c3fab7e1705b06 сверены с main. Предыдущие контейнеры на месте stopped, executable rollback/private config/SQLite copy/Postgres restore proof подтверждены:
https://github.com/Xrenus1991/TelegramMonitor/actions/runs/42039143402 .
Одноразовая активация удалена из maintenance workflow; остались read-only проверки и безопасный отчёт readiness отката.

Ограничения: wildcard/batch проверены реальными заданиями на stage и установлен соответствующий код, но отдельный production wildcard job пока не запускался; production browser не эмулировался. NSFW/exclusions/proxy/custom catalogs не объявляются полностью проверенными внешними интеграциями. Телефон/email/ФИО/текст по-прежнему только подготовка поисковых ссылок, серверных источников нет. Комментарии не сканируются по текущему пользовательскому выбору: оба разрешённых канала disabled/left, без их автоматического включения.

Текущий этап: закрыто развёртывание расширения Sherlock с конкретным откатом; этап1 контекстных подмен продолжается. Следующий небольшой пункт — bounded поиск по производным вариантам, без переписывания оригиналов и без платных API. Остальная полная матрица выше не объявлена завершённой; общая end-to-end регрессия всех согласованных интеграций остаётся впереди.


## LANG-01: адаптер определения языка и перевода — 06.11.2026 10:02 Армения

[PR33](https://github.com/Xrenus1991/TelegramMonitor/pull/33) объединён в main (8760ade86aaaef1dbeb30b9e6e0fe307c7f457e6). Реализован выключенный по умолчанию TranslationService: Lingua2.2.0 RU/HY/EN, unknown для неоднозначных вводов, оригинал отдельно от перевода, существующий paid_call и общий месячный ledger, версионированный кэш. Основная таблица messages не меняется; schema создаётся только отдельным явным действием. Pending/failed не повторяются автоматически после потенциально оплаченного запроса.

Проверки: 24 адресных локальных теста; [полный cloud CI](https://github.com/Xrenus1991/TelegramMonitor/actions/runs/42171123070) —415passed/11skipped/5subtests. [Серверный stage](https://github.com/Xrenus1991/TelegramMonitor/actions/runs/42171694049) — настоящий Lingua и пустая изолированная PostgreSQL, четыре конкурентных claim/один владелец, два cache hit, отказ бюджета, сохранённый оригинал. Peak RSS62.7MiB; платных внешних вызовов0, backend синтетический. Рабочая БД/сессии/доступы/лимиты не менялись.

Точный worker baseline сверён: db.py blob c5fe71e8c94aa553248d57669f460dc7e2b637eb, worker.py blob7aec9769bd26c7eb0f2a9ab77677a598ef3b0ea6. Различия db.py с main относятся только к чтению прогресса/фильтру source_id; резервирование, settlement и insert-ignore совпадают. Candidate SHA256de2e2c874a5c9b87e61c66ed7f8c6868f0e70bca02cad95e9bbf3d2e03b9fa62.

[Заключительный maintenance](https://github.com/Xrenus1991/TelegramMonitor/actions/runs/42171870888): AUTONOMY_OK,5561done,pending/processing/expiredleases0, последнее сохранение06:00:54UTC. Хеш stage-кандидата подтверждён; временных DB/app/network осталось0, одноразовый запуск удалён. Тестовый образ сохранён как доказательство, в production не установлен.

**Текущий статус:** foundation и server stage выполнены; этап2 целиком не закрыт. API/UI, подключение выбранных сообщений к рабочей очереди, восстановление failed/busy с ledger reconciliation и конкретный production rollback ещё впереди. Не выдавать синтетический backend за новую проверку качества внешнего перевода. Один реальный внешний RU/AM/EN прогон подтверждён предыдущим отчётом.

[Подробный отчёт](reports/TRANSLATION_ADAPTER_STAGE_20261106_RU.md). Следующий небольшой пункт — authenticated on-demand API/UI и безопасный путь задания существующему worker без расширения прав dashboard и без новых ключей. После этого unit/API, stage на изолированной копии и конкретная процедура выпуска/отката перед установкой.


## LANG-02: ручной API и интерфейс перевода — 06.11.2026 10:31 Армения

[PR34](https://github.com/Xrenus1991/TelegramMonitor/pull/34) объединён ca07bfb1ae50305c78786c6c7def1a0a3b6a38c3. Кнопка выбранного сообщения передаёт только key через authenticated same-origin API в существующий worker. Worker подтверждает HTTP Basic через отдельный endpoint самого dashboard; копия его пароля/новые API-ключи/роли не создаются. CLOUD_TRANSLATION_ENABLED выключена по умолчанию; основной цикл классификации сохраняется, HTTP-задача подключается отдельно и без подмены signal handlers.

[Cloud CI](https://github.com/Xrenus1991/TelegramMonitor/actions/runs/42174199914):432passed/11skipped/5subtests. Локально41адресный тест. [Server stage](https://github.com/Xrenus1991/TelegramMonitor/actions/runs/42174573363): настоящий worker.run и браузер, отдельная PostgreSQL, EN/HY synthetic backend, unknown, cache hit,401/403/404/422, запрет произвольного текста, инертный script-текст. Два обращения к синтетическому backend и две ledgerзаписи; внешних платных вызовов0. Stage_reader не имеет UPDATEmessages и SELECTtranslationcache. Это не проверка нового реального внешнего перевода.

[Заключительный maintenance](https://github.com/Xrenus1991/TelegramMonitor/actions/runs/42174926908):AUTONOMY_OK,5574done,pending/processing/expiredleases0; последнее сохранение06:30:50UTC. Runner отработал успешно; локальный cron06:22exit0/roles_exit0, SCRAMproof свежий. Deep32pending при3завершённых за сутки/лимите3. Временных stageконтейнеров/сетей0; одноразовый запуск удалён. Исправлен выбор foundationproof: каталог translation-ui больше не принимается за результат предыдущего adapterstage.

**Установка:** рабочие worker/dashboard образы не менялись, кэш в рабочей БД не создавался. Этап2 целиком не закрыт. Следующий пункт — два точных productionобраза, protected config и свежая копия, изолированная проверка точных образов, исполнимый откат обоих приложений с репетицией, затем установка и liveпроверка. Универсальный откат не предполагается. Ограничения:11skips, качество внешнего перевода на реальной переписке и разбор abandoned/failed claims не объявлены готовыми.

[Полный отчёт](reports/TRANSLATION_API_UI_STAGE_20261106_RU.md).


## LANG-03: перевод установлен, откат и регулярный контроль проверены — 06.11.2026

**Установлено:** ручной перевод выбранного сообщения на русский, Lingua RU/HY/EN, отдельный кэш и API/UI из PR33/34. Dashboard image c17a9c8110ad277367aac8cd36288e8c8601b2bf4e0c688db77beb0f45818941; worker image94d935a28b25655ea3f4d30396d1ed85ec002cf2a7c11eeb21786ee545f833a3. Код совпал с прошедшим server stage кандидатом. Конфигурация воспроизведена; основные роли/ключи/Telegram-сессии и лимиты не менялись.

Доказательства:
- [Подготовка](https://github.com/Xrenus1991/TelegramMonitor/actions/runs/42179324739): точные образы, protected before.inspect/payloads, fresh backup и изолированное восстановление5582сообщений; возврат двух контейнеров отрепетирован.
- [Stage точных образов](https://github.com/Xrenus1991/TelegramMonitor/actions/runs/42180716026): браузер→dashboard→worker.run→отдельная PostgreSQL; synthetic backend, EN/HY, кэш и ошибки; затем14маршрутов нового dashboard на реальной восстановленной копии с SELECT-only подключением, число сообщений сохранено.
- [Установка](https://github.com/Xrenus1991/TelegramMonitor/actions/runs/42181008497):16боевых маршрутов,401/422, реальный EN→RU через существующий OpenAI, повтор из кэша, RU без платного вызова, original-preservation. Качество реальной переписки этим единичным прогоном не измерено.
- [Production browser](https://github.com/Xrenus1991/TelegramMonitor/actions/runs/42193811909): настоящий сохранённый event и штатный renderer, кнопка, раздельный оригинал/перевод, cache result, collapse/expand. Нового платного перевода не запускалось.
- [Установка локального контроля](https://github.com/Xrenus1991/TelegramMonitor/actions/runs/42193093750): PR35; installer выполняется перед проверкой хешей. Новый HTTP8097 проверяется только при CLOUD_TRANSLATION_ENABLED, в прежних пределах2сбоя/часовой интервал/3попытки в сутки. Прежние скрипты/crontab сохранены для отката.
- [Фактический cron и контроль после выпуска](https://github.com/Xrenus1991/TelegramMonitor/actions/runs/42194477374):09:22UTC/13:22Армения,exit0,roles_exit0,worker8097HTTP200;хеши3установленныхскриптовсовпали.5651done,pending/processing/expiredleases0. Deep33pending при3doneза сутки/лимите3,месячный предел$5. Кэш переводов1done/0pending/0failed.

**Исполнимый откат этого выпуска:** /var/lib/tm-actions/monitor-autonomy/deployments/translation-20261106T071241Z/rollback.sh . Предыдущие worker/dashboard контейнеры сохранены остановленными, protected конфигурация и резервная копия только на сервере. Возврат приложения не восстанавливает старый дамп рабочей БД. Эта процедура не универсальна для будущих выпусков.

Существующее ежечасное задание ChatGPT развития Monitor/отчётов было выключено и возобновлено по поручению пользователя; новый дубликат не создавался. Включение подтверждено сервисом, доставка следующего отчёта ещё не подтверждена. Локальный15-минутныйcronпроверен фактически. Последняя обнаруженная GitHub schedule-попытка03:22UTC была неуспешной; регулярность именно GitHub schedule не объявляется исправленной, OPS-01 остаётся в очереди.

**Границы:** ручной перевод выпущен; массовый автоперевод не подключён. 11skips и расширенная оценка качества, reconciliation abandoned/failed cache claims остаются отдельными пунктами. Новые внешние источники поиска по телефону/email/ФИО, QR Telegram/WhatsApp и прочие кандидаты не объявляются готовыми. Следующий независимый пункт — оставшиеся проверки устойчивости и своевременных сигналов об остановке обработки; перед установкой Uptime Kuma/Apprise оценить, какие реальные пробелы остаются у существующего контроля. Исторические этапы выше сохранены как история, не как текущая матрица.

[Полный протокол выпуска](reports/TRANSLATION_PRODUCTION_20261106_RU.md).
