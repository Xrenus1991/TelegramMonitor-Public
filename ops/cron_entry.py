#!/usr/bin/env python3
"""Silent cron entry: keep only the latest private local report, no email."""
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path('/var/lib/tm-actions/monitor-autonomy')


def main():
    os.umask(0o077)
    result = {'started_at_utc': dt.datetime.now(dt.timezone.utc).isoformat()}
    try:
        proc = subprocess.run([sys.executable, str(ROOT/'bin/autonomy.py'), '--repair'],
                              capture_output=True, timeout=660)
        result['exit_code'] = proc.returncode
        # autonomy.py only emits allowlisted statuses; never forward stderr.
        log = proc.stdout[:262144].decode('utf-8', errors='replace')
        if proc.returncode in (0, 2) and not (ROOT/'PAUSE').exists():
            roles=subprocess.run([sys.executable,str(ROOT/'bin/check_roles_restore.py')],
                                 capture_output=True,timeout=600)
            result['roles_exit_code']=roles.returncode
            if roles.returncode:result['exit_code']=1
            log += roles.stdout[:262144].decode('utf-8',errors='replace')
    except Exception:
        result['exit_code'] = 1
        log = 'LOCAL_AUTONOMY_INVOCATION_FAILED\n'
    result['completed_at_utc'] = dt.datetime.now(dt.timezone.utc).isoformat()
    for filename, content in [('cron-last.log', log), ('cron-status.json', json.dumps(result))]:
        tmp = ROOT/(filename+'.tmp')
        tmp.write_text(content)
        tmp.chmod(0o600)
        tmp.replace(ROOT/filename)


if __name__ == '__main__':
    main()
