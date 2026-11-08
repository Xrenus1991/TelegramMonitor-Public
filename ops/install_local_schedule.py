#!/usr/bin/env python3
"""Install only tm-actions' managed cron block. Preserve unrelated entries."""
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import subprocess

ROOT = Path('/var/lib/tm-actions/monitor-autonomy')
BEGIN = '# BEGIN TELEGRAM_MONITOR_AUTONOMY'
END = '# END TELEGRAM_MONITOR_AUTONOMY'
LINE = '7,22,37,52 * * * * /usr/bin/python3 /var/lib/tm-actions/monitor-autonomy/bin/cron_entry.py'


def managed_crontab(old):
    lines = old.splitlines()
    if BEGIN in lines or END in lines:
        if lines.count(BEGIN) != 1 or lines.count(END) != 1 or lines.index(BEGIN) > lines.index(END):
            raise ValueError('malformed_managed_block')
        start, stop = lines.index(BEGIN), lines.index(END)
        lines = lines[:start] + lines[stop+1:]
    return '\n'.join(lines + [BEGIN, LINE, END]) + '\n'


def command(args, **kwargs):
    return subprocess.run(args, capture_output=True, timeout=20, **kwargs)


def main():
    os.umask(0o077)
    if pwd.getpwuid(os.geteuid()).pw_name != 'tm-actions':
        raise ValueError('wrong_account')
    if command(['systemctl','is-active','cron']).stdout.strip() != b'active':
        raise ValueError('cron_not_active')
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    if ROOT.is_symlink() or ROOT.stat().st_uid != os.geteuid():
        raise ValueError('unsafe_state_path')
    with (ROOT/'install-schedule.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        env = dict(os.environ, LC_ALL='C')
        read = command(['crontab','-l'],env=env)
        if read.returncode == 0:
            old = read.stdout.decode()
        elif read.returncode == 1 and b'no crontab for' in read.stderr:
            old = ''
        else:
            raise ValueError('cannot_read_crontab')
        new = managed_crontab(old)
        stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        backup = ROOT/('schedule-before-'+stamp+'.json')
        bindir = ROOT/'bin'
        bindir.mkdir(mode=0o700, exist_ok=True)
        if bindir.is_symlink():
            raise ValueError('unsafe_bin_path')
        sources = {name:(Path(__file__).parent/name).read_bytes() for name in ('autonomy.py','cron_entry.py','check_roles_restore.py')}
        previous = {name:(bindir/name).read_text() if (bindir/name).exists() else None for name in sources}
        backup.write_text(json.dumps({'crontab':old,'files':previous}))
        hashes = {}
        for name, data in sources.items():
            tmp = bindir/(name+'.tmp')
            tmp.write_bytes(data)
            tmp.chmod(0o700)
            tmp.replace(bindir/name)
            hashes[name] = hashlib.sha256(data).hexdigest()
        try:
            write = command(['crontab','-'], input=new.encode(),env=env)
            if write.returncode != 0:
                raise ValueError('cannot_install_crontab')
            check = command(['crontab','-l'],env=env)
            if check.returncode != 0 or check.stdout.decode() != new:
                raise ValueError('crontab_verification_failed')
        except Exception:
            command(['crontab','-'],input=old.encode(),env=env)
            for name, data in previous.items():
                if data is not None:
                    (bindir/name).write_text(data)
            raise
        print('LOCAL_SCHEDULE_INSTALLED='+json.dumps(hashes,sort_keys=True))
        print('SCHEDULE_ROLLBACK_SNAPSHOT='+str(backup))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('LOCAL_SCHEDULE_INSTALL_FAILED')
        raise SystemExit(1)
