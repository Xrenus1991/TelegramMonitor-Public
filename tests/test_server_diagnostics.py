"""Offline checks for SSH diagnostic setup. These never contact a server."""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_readonly_snapshot_bash_syntax_and_output():
    path = ROOT / "scripts" / "diagnostics" / "server_probe.sh"
    subprocess.run(["bash", "-n", str(path)], check=True)
    process = subprocess.run(["bash", str(path)], capture_output=True, text=True,
                             timeout=12, check=True)
    rows = process.stdout.strip().splitlines()
    keys = [row.split("=", 1)[0] for row in rows]
    assert "schema_version" in keys
    assert "telegram_live_verified" in keys
    assert "backup_restore_verified" in keys
    assert len(keys) == len(set(keys)), "Duplicate report fields"
    assert all(re.fullmatch(r"[a-z][a-z0-9_]*", key) for key in keys)
    assert all("=" in row for row in rows)
    assert "PRIVATE KEY" not in process.stdout
    assert "OPENAI_API_KEY" not in process.stdout


def test_private_ssh_workflow_excluded_from_public_ci():
    """Public CI must not contain the private Hetzner SSH workflow."""
    workflow = ROOT / ".github/workflows/hetzner-readonly-diagnostics.yml"
    assert not workflow.exists(), "Private Hetzner workflow must not be public"


def test_snapshot_cannot_change_server_or_read_secret_files():
    script = (ROOT / "scripts/diagnostics/server_probe.sh").read_text()
    for disallowed in ("sudo ", "docker exec", "docker compose up", "systemctl restart",
                       "cat .env", "cat \"$ROOT/deploy/.env\"", "curl -X POST"):
        assert disallowed not in script
    assert "backup_restore_verified no" in script
