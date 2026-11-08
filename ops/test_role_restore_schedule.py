import json,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from check_roles_restore import due_decision
import cron_entry
class RoleScheduleTests(unittest.TestCase):
    def test_daily_success_and_six_hour_failure_limit(self):
        at=1000000
        self.assertEqual(due_decision({'password_auth_tested':True,'verified_at':at-1},{},at),'fresh')
        self.assertEqual(due_decision({'password_auth_tested':False},{'attempted_at':at-1},at),'retry_limited')
        self.assertEqual(due_decision({'password_auth_tested':True,'verified_at':at-86400},{'attempted_at':at-21600},at),'run')
    def test_local_cron_invokes_both_and_records_roles_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(cron_entry,'ROOT',Path(tmp)),patch.object(cron_entry.subprocess,'run',
                 side_effect=[SimpleNamespace(returncode=0,stdout=b'AUTONOMY_OK\n'),
                              SimpleNamespace(returncode=1,stdout=b'ROLE_CHECK_FAILED\n')]) as run:
                cron_entry.main()
                status=json.loads((Path(tmp)/'cron-status.json').read_text())
                self.assertEqual(run.call_count,2)
                self.assertEqual(status['roles_exit_code'],1)
                self.assertEqual(status['exit_code'],1)
    def test_pause_never_runs_role_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'PAUSE').touch()
            with patch.object(cron_entry,'ROOT',root),patch.object(cron_entry.subprocess,'run',
                 return_value=SimpleNamespace(returncode=0,stdout=b'paused')) as run:
                cron_entry.main()
                self.assertEqual(run.call_count,1)

class ProcessingRoleIndependenceTests(unittest.TestCase):
    def test_processing_attention_preserves_role_check_and_failure_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(cron_entry,'ROOT',Path(tmp)),patch.object(cron_entry.subprocess,'run',
                 side_effect=[SimpleNamespace(returncode=2,stdout=b'AUTONOMY_PROCESSING_ATTENTION_REQUIRED\n'),
                              SimpleNamespace(returncode=0,stdout=b'ROLES_FRESH\n')]) as command:
                cron_entry.main()
                status=json.loads((Path(tmp)/'cron-status.json').read_text())
                self.assertEqual(command.call_count,2)
                self.assertEqual(status['roles_exit_code'],0)
                self.assertEqual(status['exit_code'],2)
    def test_infrastructure_failure_does_not_start_roles(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(cron_entry,'ROOT',Path(tmp)),patch.object(cron_entry.subprocess,'run',
                 return_value=SimpleNamespace(returncode=1,stdout=b'AUTONOMY_ATTENTION_REQUIRED\n')) as command:
                cron_entry.main()
                self.assertEqual(command.call_count,1)
