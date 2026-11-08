#!/usr/bin/env bash
# Read-only, fixed-schema snapshot for a forced-command SSH key.
# Never print .env, SSH keys, session files, database contents, raw logs or IPs.
set -euo pipefail
export LC_ALL=C
umask 077
ROOT=/opt/telegram-monitor

out() { printf '%s=%s\n' "$1" "$2"; }
binary() { if command -v "$1" >/dev/null 2>&1; then printf yes; else printf no; fi; }
ynfile() { if [[ -f "$1" ]]; then printf yes; else printf no; fi; }
http_status() {
  if command -v curl >/dev/null 2>&1; then
    local code
    code="$(curl --noproxy '*' -sS -o /dev/null --connect-timeout 2 --max-time 3 -w '%{http_code}' "$1" 2>/dev/null || true)"
    if [[ "$code" =~ ^[0-9]{3}$ ]]; then printf '%s' "$code"; else printf unknown; fi
  else
    printf unavailable
  fi
}

out schema_version 1
out collected_at_utc "$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
out project_directory_present "$([[ -d "$ROOT" ]] && echo yes || echo no)"
out runtime_compose_present "$(
  if [[ -f "$ROOT/deploy/compose.yml" || -f "$ROOT/deploy/compose.yaml" || -f "$ROOT/deploy/docker-compose.yml" || -f "$ROOT/compose.yml" || -f "$ROOT/docker-compose.yml" ]]; then
    echo yes
  else
    echo no
  fi
)"
# Presence only. Values are intentionally never opened or printed.
out env_present "$(
  if [[ -f "$ROOT/deploy/.env" || -f "$ROOT/.env" ]]; then echo yes; else echo no; fi
)"
out server_probe_installed yes
if [[ -r /proc/uptime ]]; then
  out host_uptime_seconds "$(awk '{printf "%.0f", $1}' /proc/uptime)"
else
  out host_uptime_seconds unavailable
fi
if command -v df >/dev/null 2>&1; then
  out disk_used_percent "$(df -P "$ROOT" 2>/dev/null | awk 'NR==2 {gsub(/%/,"",$5); print $5}' | head -n1 || true)"
else
  out disk_used_percent unavailable
fi
if command -v free >/dev/null 2>&1; then
  out memory_available_mb "$(free -m | awk '$1=="Mem:" {print $7}' | head -n1)"
else
  out memory_available_mb unavailable
fi
if command -v systemctl >/dev/null 2>&1; then
  out docker_service_state "$(systemctl is-active docker 2>/dev/null || true)"
else
  out docker_service_state unavailable
fi
if command -v docker >/dev/null 2>&1 && docker ps -q >/dev/null 2>&1; then
  # Only counts: no image, command, environment, volume, process or container names.
  out docker_running_containers "$(docker ps -q 2>/dev/null | wc -l | tr -d '[:space:]')"
  out docker_total_containers "$(docker ps -aq 2>/dev/null | wc -l | tr -d '[:space:]')"
else
  out docker_running_containers unavailable
  out docker_total_containers unavailable
fi
out api_health_http_status "$(http_status 'http://127.0.0.1:8080/health')"
out dashboard_unauth_http_status "$(http_status 'http://127.0.0.1:8080/')"
# A backup *filename count* does not confirm a valid or restorable backup.
BACKUP_DIR=""
for folder in "$ROOT/deploy/backups" "$ROOT/backups"; do
  if [[ -d "$folder" ]]; then BACKUP_DIR="$folder"; break; fi
done
if [[ -n "$BACKUP_DIR" && -r "$BACKUP_DIR" ]]; then
  out backup_file_count "$(find "$BACKUP_DIR" -maxdepth 1 -type f 2>/dev/null | wc -l | tr -d '[:space:]')"
  out backup_files_last_24h "$(find "$BACKUP_DIR" -maxdepth 1 -type f -mmin -1440 2>/dev/null | wc -l | tr -d '[:space:]')"
else
  out backup_file_count unavailable
  out backup_files_last_24h unavailable
fi
out postgres_verified no
out telegram_live_verified no
out openai_live_verified no
out backup_restore_verified no
