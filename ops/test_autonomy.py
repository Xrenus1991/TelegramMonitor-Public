import time
import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from autonomy import recovery_decision, check_container, cycle, SafeError, TARGETS, DB


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.now = time.time()
        self.item = {'State': {'Running': False, 'Status': 'exited'}}
        self.entry = {'failures': 2, 'attempts': []}

    def decide(self, name=None, entry=None, db=True, paused=False, item=None):
        return recovery_decision(name or TARGETS[0], item or self.item,
                                 entry or self.entry, self.now, db, paused)

    def test_requires_two_failures(self):
        self.assertFalse(self.decide(entry={'failures': 1}))
        self.assertTrue(self.decide())

    def test_never_database_or_sender(self):
        self.assertFalse(self.decide(name=DB))
        self.assertFalse(self.decide(name='rentweeklysender_v1-rent-sender-1'))

    def test_dependency_and_pause_block_recovery(self):
        self.assertFalse(self.decide(db=False))
        self.assertFalse(self.decide(paused=True))
        self.assertFalse(self.decide(item={'State': {'Paused': True}}))
        self.assertFalse(self.decide(item={'State': {'Restarting': True}}))

    def test_hourly_cooldown(self):
        self.assertFalse(self.decide(entry={'failures': 2, 'attempts': [self.now-100]}))
        self.assertTrue(self.decide(entry={'failures': 2, 'attempts': [self.now-3700]}))

    def test_daily_limit(self):
        self.assertFalse(self.decide(entry={'failures': 2, 'attempts': [self.now-4000, self.now-8000, self.now-12000]}))
        self.assertTrue(self.decide(entry={'failures': 2, 'attempts': [self.now-90000]}))

    def test_health_requires_200(self):
        item = {'State': {'Running': True}}
        for code in (0, 401, 403, 404, 500, 503):
            with patch('autonomy.run', return_value=json.dumps({'status': code}).encode()):
                self.assertFalse(check_container(TARGETS[0], item)['ok'])
        with patch('autonomy.run', return_value=b'{"status":200}'):
            self.assertTrue(check_container(TARGETS[0], item)['ok'])

    def test_translation_worker_health_is_opt_in(self):
        running = {'State': {'Running': True}}
        with patch('autonomy.run') as command:
            self.assertTrue(check_container(TARGETS[2], running)['ok'])
            command.assert_not_called()
        running['Config'] = {'Env': ['CLOUD_TRANSLATION_ENABLED=true']}
        with patch('autonomy.run', return_value=b'{"status":200}') as command:
            self.assertTrue(check_container(TARGETS[2], running)['ok'])
            self.assertEqual(command.call_args.args[0][-1], '8097')
        with patch('autonomy.run', return_value=b'{"status":503}'):
            self.assertFalse(check_container(TARGETS[2], running)['ok'])

    def test_pause_prevents_all_docker_operations(self):
        with tempfile.TemporaryDirectory() as d, patch('autonomy.run') as run:
            root = Path(d)
            (root / 'PAUSE').touch()
            self.assertEqual(cycle(True, root)['status'], 'paused')
            run.assert_not_called()

    def test_failed_restart_consumes_attempt_without_touching_other_services(self):
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            if args[1] == 'restart':
                raise SafeError('command_failed')
            return b'4500'
        def check(name, item):
            return {'name': name, 'ok': name != TARGETS[0]}
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'state.json').write_text(json.dumps({
                TARGETS[0]: {'failures': 1, 'checked_at': self.now},
                'last_backup': {'verified_at': self.now},
            }))
            with patch('autonomy.inspect', return_value={'State': {'Running': True}}), \
                 patch('autonomy.check_container', side_effect=check), patch('autonomy.run', side_effect=run):
                self.assertFalse(cycle(True, root)['ok'])
            state = json.loads((root / 'state.json').read_text())
            self.assertEqual(len(state[TARGETS[0]]['attempts']), 1)
            self.assertEqual([x for x in calls if x[1] == 'restart'],
                             [['docker', 'restart', '--time', '30', TARGETS[0]]])


if __name__ == '__main__':
    unittest.main()
