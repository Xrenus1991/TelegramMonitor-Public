"""Guard against silently dropping Sherlock tests from public CI.

This test intentionally does not import the private Sherlock bridge.
"""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "public-ci.yml"
BRIDGE = ROOT / "deploy" / "sherlock" / "bridge.py"
EXCLUDED = (
    "tests/test_sherlock_advanced_offline.py",
    "tests/test_sherlock_bridge_offline.py",
)


class PublicSherlockAuditTests(unittest.TestCase):
    def test_excluded_suites_exist_and_have_tests(self):
        for name in EXCLUDED:
            with self.subTest(name=name):
                path = ROOT / name
                self.assertTrue(path.is_file(), f"Missing test suite: {name}")
                self.assertRegex(path.read_text(encoding="utf-8"), r"(?m)^def test_")

    def test_missing_bridge_must_be_explicitly_reported(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("if test -e deploy/sherlock/bridge.py; then", workflow)
        self.assertIn("::warning::Sherlock bridge absent", workflow)
        for name in EXCLUDED:
            self.assertIn("--ignore=" + name, workflow)

    def test_bridge_present_must_execute_full_test_suite(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertRegex(
            workflow,
            r"if test -e deploy/sherlock/bridge\.py; then\s+python -m pytest -q -p pytest_asyncio\.plugin tests",
        )

    def test_no_global_pytest_skip_or_success_masking(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("|| true", workflow)
        self.assertNotIn("continue-on-error: true", workflow)
        self.assertNotIn("--ignore=tests", workflow.split("if test -e deploy/sherlock/bridge.py; then")[0])


if __name__ == "__main__":
    unittest.main()
