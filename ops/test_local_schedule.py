import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from install_local_schedule import BEGIN, END, LINE, managed_crontab
from autonomy import cycle


class ScheduleTests(unittest.TestCase):
    def test_preserves_other_jobs_and_idempotent(self):
        old='MAILTO=""\n5 * * * * /other/job\n'
        new=managed_crontab(old)
        self.assertTrue(new.startswith(old))
        self.assertEqual(managed_crontab(new),new)
        self.assertEqual(new.count(LINE),1)

    def test_rejects_broken_blocks(self):
        for text in [BEGIN,END,END+'\n'+BEGIN,BEGIN+'\n'+BEGIN+'\n'+END]:
            with self.assertRaises(ValueError):managed_crontab(text)

    def test_duplicate_check_keeps_original_timestamp_and_failure(self):
        with tempfile.TemporaryDirectory() as d, patch('autonomy.run') as run:
            root=Path(d)
            (root/'state.json').write_text(json.dumps({'_last_cycle_at':time.time()}))
            (root/'latest.json').write_text(json.dumps({'ok':False,'checked_at_utc':'original'}))
            result=cycle(True,root)
            self.assertFalse(result['ok'])
            self.assertEqual(result['checked_at_utc'],'original')
            self.assertTrue(result['skipped_duplicate_cycle'])
            run.assert_not_called()


if __name__=='__main__':unittest.main()
