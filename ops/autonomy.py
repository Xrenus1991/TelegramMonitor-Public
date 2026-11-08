#!/usr/bin/env python3
"""Bounded Monitor maintenance: inspect, HTTP checks, backups, isolated restores.

Never delete production data, restart PostgreSQL, or operate on RentWeeklySender.
No application image updates or database migrations are performed by this module.
"""
import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid

TARGETS = ('tm-dashboard-v861-live-readonly', 'tm-v861-live-collector',
           'tm-v861-live-worker', 'tm-sherlock-stage')
HTTP_TARGETS = {TARGETS[0]: 8098, TARGETS[3]: 8099}
DB = 'telegram-monitor-v7-postgres-1'
ROOT = Path('/var/lib/tm-actions/monitor-autonomy')

HTTP_PROBE = r'''
import json, sys, urllib.request, urllib.error
port=int(sys.argv[1])
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    with opener.open('http://127.0.0.1:%d/health'%port,timeout=5) as r:
        code=r.status
        if port==8097 and json.loads(r.read(4096)).get('translation_enabled') is not True: code=0
except urllib.error.HTTPError as e: code=e.code
except Exception: code=0
print(json.dumps({'port':port,'path':'/health','status':code}))
'''


class SafeError(Exception):
    pass


def run(args, *, timeout=45, stdin=None, stdout=None):
    try:
        return subprocess.run(args, check=True, stdin=stdin, stdout=stdout or subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        # Do not expose subprocess args, environment, stderr or SQL credentials.
        raise SafeError('command_failed') from None


def inspect(name):
    return json.loads(run(['docker', 'inspect', name]))[0]


def atomic_json(path, value):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, indent=2) + '\n')
    tmp.chmod(0o600)
    tmp.replace(path)


def recovery_decision(name, item, entry, now, db_ok, paused):
    if name not in TARGETS or paused or not db_ok:
        return False
    state = item.get('State', {})
    if state.get('Paused') or state.get('Restarting') or state.get('Status') in ('dead', 'removing'):
        return False
    if entry.get('failures', 0) < 2:
        return False
    history = [x for x in entry.get('attempts', []) if now - x < 86400]
    return len(history) < 3 and (not history or now - max(history) >= 3600)


def check_container(name, item):
    row = {'name': name, 'running': item['State'].get('Running', False),
           'restart_count': item.get('RestartCount', 0)}
    row['health_status'] = item['State'].get('Health', {}).get('Status', 'not_configured')
    row['ok'] = row['running'] and row['health_status'] != 'unhealthy'
    port = HTTP_TARGETS.get(name)
    if name == TARGETS[2] and any(value.lower() in ('cloud_translation_enabled=true', 'cloud_translation_enabled=1', 'cloud_translation_enabled=yes', 'cloud_translation_enabled=on') for value in item.get('Config', {}).get('Env', [])):
        port = 8097
    if port is not None and row['running']:
        try:
            probes = json.loads(run(['docker', 'exec', name, 'python', '-c', HTTP_PROBE,
                                     str(port)], timeout=15))
            row['http'] = probes
            health = probes['status'] == 200
            row['http_level'] = 'health_endpoint' if health else 'failed'
            row['ok'] = row['ok'] and health
        except SafeError:
            row.update(ok=False, http_level='probe_failed')
    return row


def backup_and_restore(root, db_image):
    folder = root / 'backups' / dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    folder.mkdir(parents=True, mode=0o700)
    if shutil.disk_usage(root).free < 1024 ** 3:
        raise SafeError('backup_disk_space_below_1GiB')
    dump = folder / 'database.dump'
    roles = folder / 'roles.sql'
    for path, command in [
        (dump, 'exec pg_dump -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-${POSTGRES_USER:-postgres}}" --format=custom'),
        (roles, 'exec pg_dumpall -U "${POSTGRES_USER:-postgres}" --globals-only'),
    ]:
        partial = path.with_suffix(path.suffix + '.partial')
        with partial.open('wb') as output:
            run(['docker', 'exec', DB, 'sh', '-ec', command], timeout=180, stdout=output)
        if not partial.stat().st_size:
            raise SafeError('empty_backup')
        partial.replace(path)
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (dump, roles)}
    atomic_json(folder / 'sha256.json', hashes)
    with dump.open('rb') as data:
        run(['docker', 'exec', '-i', DB, 'pg_restore', '--list'], stdin=data)

    # Avoid a memory-intensive restore test when the production host is pressured.
    available = next(int(l.split()[1]) * 1024 for l in Path('/proc/meminfo').read_text().splitlines()
                     if l.startswith('MemAvailable:'))
    if available < 512 * 1024 ** 2:
        raise SafeError('backup_saved_restore_deferred_low_memory')
    test_name = 'tm-autonomy-restore-' + uuid.uuid4().hex[:12]
    created = False
    try:
        run(['docker', 'create', '--name', test_name, '--label', 'tm.autonomy.restore=true',
             '--network', 'none', '--restart', 'no', '--memory', '256m', '--cpus', '0.5',
             '--tmpfs', '/var/lib/postgresql/data:rw,size=128m',
             '-e', 'POSTGRES_HOST_AUTH_METHOD=trust', '-e', 'POSTGRES_DB=restore_check', db_image])
        created = True
        run(['docker', 'start', test_name])
        for attempt in range(60):
            try:
                run(['docker', 'exec', test_name, 'sh', '-ec',
                     'test "$(cat /proc/1/comm)" = postgres && pg_isready -U postgres -d restore_check'], timeout=5)
                break
            except SafeError:
                time.sleep(1)
        else:
            raise SafeError('restore_start_timeout')
        with dump.open('rb') as data:
            run(['docker', 'exec', '-i', test_name, 'pg_restore', '-U', 'postgres', '-d', 'restore_check',
                 '--exit-on-error', '--no-owner', '--no-privileges'], stdin=data, timeout=180)
        count = int(run(['docker', 'exec', test_name, 'psql', '-U', 'postgres', '-d', 'restore_check',
                         '-v', 'ON_ERROR_STOP=1', '-Atc', 'SELECT count(*) FROM public.messages;']).strip())
        result = {'verified_at': time.time(), 'messages': count, 'bytes': dump.stat().st_size,
                  'roles_saved': True, 'roles_restore_verified': False}
        atomic_json(folder / 'verified.json', result)
        return result
    finally:
        if created:
            run(['docker', 'rm', '-f', '-v', test_name])



PROCESSING_SQL = """
BEGIN READ ONLY;
SET LOCAL statement_timeout='10s';
SELECT json_build_object(
 'messages',count(*),'last_created_at',max(created_at),
 'pending',count(*) FILTER (WHERE state='pending'),
 'processing',count(*) FILTER (WHERE state='processing'),
 'failed',count(*) FILTER (WHERE state='failed'),
 'ready_pending_over_30m',count(*) FILTER (WHERE state='pending'
    AND created_at<now()-interval '30 minutes' AND (retry_after IS NULL OR retry_after<=now())),
 'deferred',count(*) FILTER (WHERE state='pending' AND retry_after>now()),
 'expired_processing_over_5m',count(*) FILTER (WHERE state='processing'
    AND (lease_until IS NULL OR lease_until<now()-interval '5 minutes')),
 'deep_pending',count(*) FILTER (WHERE deep_state='pending'),
 'deep_done_today',count(*) FILTER (WHERE deep_state='done' AND
    deep_completed_at >= (date_trunc('day',now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')),
 'expired_deep_over_5m',count(*) FILTER (WHERE deep_state='processing'
    AND (lease_until IS NULL OR lease_until<now()-interval '5 minutes')),
 'budget_used_micro_usd',COALESCE((SELECT used_micro_usd FROM public.budgets
    WHERE month=to_char(now() AT TIME ZONE 'UTC','YYYY-MM')),0))
FROM public.messages;
COMMIT;
"""


def assess_processing(snapshot, budget_usd, deep_limit):
    """A backlog is a reason to investigate, not proof the worker is hung."""
    budget_state = ('unknown' if budget_usd is None else
                    'exhausted' if snapshot['budget_used_micro_usd'] >= budget_usd * 1000000 else 'available')
    deep_state = ('unknown' if deep_limit is None else 'disabled' if deep_limit == 0 else
                  'daily_limited' if snapshot['deep_done_today'] >= deep_limit else 'within_limit')
    codes = []
    if snapshot['ready_pending_over_30m'] and budget_state != 'exhausted':
        codes.append('classification_due_backlog')
    if snapshot['expired_processing_over_5m']:
        codes.append('expired_classification_claim')
    if snapshot['expired_deep_over_5m']:
        codes.append('expired_deep_claim')
    return {'requires_attention': bool(codes), 'codes': codes,
            'budget_state': budget_state, 'deep_state': deep_state,
            'restart_requested': False, 'snapshot': snapshot}


def processing_snapshot():
    item = inspect(TARGETS[2])
    env = dict(row.split('=', 1) for row in item.get('Config', {}).get('Env', []) if '=' in row)
    def limit(name, low, high):
        try:
            value = int(env.get(name, ''))
            return value if low <= value <= high else None
        except ValueError:
            return None
    command = ('exec psql -U "${POSTGRES_USER:-postgres}" '
               '-d "${POSTGRES_DB:-${POSTGRES_USER:-postgres}}" -v ON_ERROR_STOP=1 -Atq -c "$1"')
    snapshot = json.loads(run(['docker', 'exec', DB, 'sh', '-ec', command,
                               'processing-health', PROCESSING_SQL], timeout=20))
    return assess_processing(snapshot, limit('CLOUD_BUDGET_USD', 1, 10000),
                             limit('CLOUD_DEEP_MAX_PER_DAY', 0, 2000))


def health_exit_code(result):
    if result['ok']:
        return 0
    # Code 2 preserves independent daily role checks while still failing the run.
    if (result.get('processing', {}).get('requires_attention') and
            result.get('database', {}).get('ok') and len(result.get('containers', [])) == len(TARGETS) and
            all(row.get('ok') for row in result['containers']) and
            not result.get('backup_error') and not result.get('backup_stale')):
        return 2
    return 1


def cycle(repair=False, root=ROOT):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    with (root / 'lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'status': 'already_running', 'ok': True}
        now = time.time()
        state_file = root / 'state.json'
        state = json.loads(state_file.read_text()) if state_file.exists() else {}
        result = {'checked_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
                  'repair_enabled': repair, 'paused': (root / 'PAUSE').exists(),
                  'containers': [], 'ok': True}
        if result['paused']:
            result['status'] = 'paused'
            atomic_json(root / 'latest.json', result)
            return result
        # GitHub and the host cron can fire in the same slot. Count at most one
        # real health sample in five minutes; do not accelerate recovery limits.
        if 0 <= now - state.get('_last_cycle_at', 0) < 300 and (root / 'latest.json').exists():
            previous_result = json.loads((root / 'latest.json').read_text())
            previous_result['skipped_duplicate_cycle'] = True
            return previous_result
        try:
            db = inspect(DB)
            db_ok = db['State'].get('Running', False) and db['State'].get('Health', {}).get('Status') != 'unhealthy'
            count = int(run(['docker', 'exec', DB, 'sh', '-ec',
                'exec psql -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-${POSTGRES_USER:-postgres}}" '
                '-v ON_ERROR_STOP=1 -Atc "SELECT count(*) FROM public.messages;"']).strip())
            result['database'] = {'ok': db_ok, 'messages': count}
        except (SafeError, ValueError):
            db_ok = False
            result['database'] = {'ok': False}
        result['ok'] = db_ok
        for name in TARGETS:
            entry = state.setdefault(name, {})
            try:
                item = inspect(name)
                row = check_container(name, item)
                # Keep failures consecutive; stale checks do not justify a restart.
                previous = entry.get('failures', 0) if now - entry.get('checked_at', 0) < 3600 else 0
                entry.update(failures=0 if row['ok'] else previous + 1, checked_at=now)
                if not row['ok'] and repair and recovery_decision(name, item, entry, now, db_ok, False):
                    entry['attempts'] = [t for t in entry.get('attempts', []) if now-t < 86400] + [now]
                    # Persist the attempt BEFORE mutation, even if the process dies.
                    atomic_json(state_file, state)
                    run(['docker', 'restart', '--time', '30', name], timeout=60)
                    time.sleep(5)
                    row = check_container(name, inspect(name))
                    row['action'] = 'restart'
                    if row['ok']:
                        entry['failures'] = 0
                elif not row['ok']:
                    row['action'] = 'observe_or_rate_limited'
            except (SafeError, ValueError, KeyError, IndexError):
                row = {'name': name, 'ok': False, 'error': 'check_failed'}
            result['containers'].append(row)
            result['ok'] = result['ok'] and row['ok']
        if db_ok:
            try:
                result['processing'] = processing_snapshot()
            except (SafeError, ValueError, KeyError, TypeError):
                result['processing'] = {'requires_attention': True, 'codes': ['processing_check_unavailable'],
                                        'restart_requested': False}
            result['ok'] = result['ok'] and not result['processing']['requires_attention']
        last_backup = state.get('last_backup', {})
        if db_ok and now - last_backup.get('verified_at', 0) >= 86400 and now - state.get('backup_attempt', 0) >= 21600:
            state['backup_attempt'] = now
            atomic_json(state_file, state)
            try:
                state['last_backup'] = backup_and_restore(root, db['Image'])
            except SafeError as exc:
                result['backup_error'] = str(exc)
                result['ok'] = False
        result['backup'] = state.get('last_backup', {})
        if now - result['backup'].get('verified_at', 0) > 172800:
            result['ok'] = False
            result['backup_stale'] = True
        state['_last_cycle_at'] = now
        atomic_json(state_file, state)
        atomic_json(root / 'latest.json', result)
        return result


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument('--repair', action='store_true')
    args = parser.parse_args()
    try:
        result = cycle(args.repair)
    except Exception:
        print('AUTONOMY_INTERNAL_ERROR')
        return 1
    print(json.dumps(result, indent=2))
    code = health_exit_code(result)
    print('AUTONOMY_OK' if code == 0 else 'AUTONOMY_PROCESSING_ATTENTION_REQUIRED' if code == 2 else 'AUTONOMY_ATTENTION_REQUIRED')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
