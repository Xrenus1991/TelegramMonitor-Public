# Самостоятельная диагностика Hetzner через GitHub Actions

**Статус: включение через отдельный PR в main; первое подключение сначала только вручную.**

Смысл: GitHub Actions по ручному запуску выполняет одну заранее зафиксированную,
безопасную команду через SSH с отдельным ключом. Ключ **не даёт shell-доступа**:
в `authorized_keys` сервер принудительно запускает только
`/usr/local/sbin/tm-readonly-diagnostic`. Я могу прочитать результат через
подключённый GitHub и анализировать состояние без повторного набора команд
на вашем ноутбуке. Это **не** прямой интерактивный доступ ChatGPT к серверу.

## Что проверяет (без персональных данных)

- Наличие рабочего каталога, реального Compose и .env (только факт наличия).
- Время работы Linux, использование диска, доступную RAM, статус службы Docker.
- Число Docker-контейнеров, **если отдельному пользователю доступны команды
  `docker ps`**. По умолчанию новый пользователь не в группе `docker`; при
  недостатке прав поле будет `unavailable`. **Не добавляйте** пользователя в
  docker-группу: она даёт чрезмерные права.
- HTTP-статусы локальной `/health` и запроса к веб-панели без авторизации
  (для защищённой панели ожидается 401).
- Число файлов резервных копий и новых файлов за 24 часа (без названий,
  содержимого или доступа к БД). Это **не** проверка восстановления бэкапа.

**Не выполняет:** `docker exec`, изменения контейнеров, чтение env,
просмотр Telegram-данных, обращение к OpenAI, проверку паролей и токенов,
миграции PostgreSQL, деплой, реконструкцию из бэкапа, полный аудит безопасности.

## Что уже находится в репозитории

- `scripts/diagnostics/server_probe.sh` — только чтение, фиксированный
  набор строк `name=value`.
- `.github/workflows/hetzner-readonly-diagnostics.yml` — GitHub-hosted
  Linux runner, ручной запуск или расписание **ежедневно 04:41 UTC**.
- `tests/test_server_diagnostics.py` — автономная проверка скрипта и
  ограничений workflow; не подключается к Hetzner.

## Однократная настройка (потребуется владелец сервера)

**Сначала проверьте содержимое скрипта и убедитесь, что хотите его установить.
Не используйте рабочий личный SSH-ключ в GitHub Actions.**

1. На Mac создайте отдельный ключ (сохраните приватный файл локально):

   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/tm_github_diagnostics -C "tm-readonly-diagnostics" -N ""
   ```

2. Скачайте `scripts/diagnostics/server_probe.sh` из этой **приватной**
   ветки GitHub на Mac. Передайте на сервер скрипт и **только публичный**
   ключ вашим уже рабочим SSH-подключением:

   ```bash
   scp -i ~/.ssh/hetzner_telegram_monitor ./server_probe.sh monitor@YOUR_SERVER_IP:/tmp/tm-server-probe.sh
   scp -i ~/.ssh/hetzner_telegram_monitor ~/.ssh/tm_github_diagnostics.pub monitor@YOUR_SERVER_IP:/tmp/tm-gh-diag.pub
   ```

3. Подключитесь на сервер обычным личным ключом. Выполните после проверки:

   ```bash
   sudo useradd --system --create-home --user-group --shell /bin/bash tm-diagnostic
   sudo install -o root -g root -m 0755 /tmp/tm-server-probe.sh /usr/local/sbin/tm-readonly-diagnostic
   sudo install -d -o tm-diagnostic -g tm-diagnostic -m 0700 /home/tm-diagnostic/.ssh
   sudo sh -c 'printf "restrict,command=\"/usr/local/sbin/tm-readonly-diagnostic\" %s\n" "$(cat /tmp/tm-gh-diag.pub)" > /home/tm-diagnostic/.ssh/authorized_keys'
   sudo chown tm-diagnostic:tm-diagnostic /home/tm-diagnostic/.ssh/authorized_keys
   sudo chmod 0600 /home/tm-diagnostic/.ssh/authorized_keys
   sudo rm -f /tmp/tm-server-probe.sh /tmp/tm-gh-diag.pub
   ```

   Важно: это **отдельная системная учётная запись** без sudo и без
   добавления в `docker`-группу. Приватный ключ на сервер не копируется.

4. Сравните SSH host fingerprint, полученный через ваш уже защищённый вход:

   ```bash
   sudo ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
   ```

   На Mac сравните с отпечатком публичного ключа хоста, который вы намерены
   закрепить в GitHub. Не отключайте `StrictHostKeyChecking`.

5. В приватном GitHub репозитории откройте **Settings → Secrets and
   variables → Actions → New repository secret** и создайте четыре секрета:

   - `TM_DIAG_HOST`: IP или DNS сервера, проверенного в пункте 4.
   - `TM_DIAG_USER`: `tm-diagnostic`.
   - `TM_DIAG_SSH_PRIVATE_KEY`: содержимое **нового** приватного ключа
     `~/.ssh/tm_github_diagnostics` (НЕ присылайте сюда).
   - `TM_DIAG_KNOWN_HOSTS`: проверенная строка known_hosts с публичным
     ключом сервера, собранная на Mac командой `ssh-keyscan -t ed25519
     YOUR_SERVER_IP` и сверенная с пунктом 4.

   **Не** добавляйте SSH-ключ от существующего пользователя `monitor`:
   он не ограничен принудительной диагностической командой.

6. После ревью и объединения workflow с веткой `main` откройте GitHub
   **Actions → Hetzner read-only diagnostics → Run workflow**. До успешного первого запуска расписание намеренно выключено. Пока файл
   находится в рабочей ветке, на `main` нет активного расписания.

## Ограничения и безопасность

- Вы можете отозвать доступ, удалив **этот** публичный ключ на сервере.
- Репозиторий приватный, но отчёты GitHub Actions видны всем, кто имеет
  соответствующий доступ к CI. Мы выводим только фиксированные метрики.
- GitHub Actions может не подключиться, если SSH на Hetzner ограничен по
  внешнему IP; тогда не открывайте порт миру без отдельного анализа.
- Изменение workflow в `main` может повлиять на использование GitHub Secrets.
  Защитите `main` проверкой pull request перед слиянием.
- Приватный ключ без пароля допустим **только** из-за принудительной
  команды на сервере. Для полной защиты храните его лишь в GitHub Secrets.
- Если нужен реальный просмотр Docker-состояния, БД и журналов, добавим
  **отдельные конкретные read-only проверки** через контролируемый интерфейс;
  не давайте диагностическому ключу root/sudo/docker-права.
- Не считайте автоматический отчёт разрешением на изменения и обновления
  сервера. Любое развёртывание делается отдельно и только после согласования.
