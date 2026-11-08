"""Never load secret contents or run Docker while checking release readiness."""
import importlib.util
import json
from pathlib import Path


def _mod():
    path = Path(__file__).resolve().parents[1] / "scripts/preflight_deploy.py"
    spec = importlib.util.spec_from_file_location("preflight_deploy", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_incomplete_snapshot_is_not_marked_deployable(tmp_path, capsys):
    check = _mod()
    result = check.assess(tmp_path)
    assert result["ready_for_manual_review"] is False
    assert result["deployment_tested"] is False
    assert result["compose_present"] is False
    assert check.main(["--root", str(tmp_path), "--strict"]) == 2
    assert "No live Compose file included" in capsys.readouterr().out


def test_presence_only_with_secrets_not_echoed(tmp_path, capsys):
    check = _mod()
    for rel in ("telegram_monitor/cloud/collector.py", "telegram_monitor/cloud/worker.py",
                "telegram_monitor/cloud/api.py", "telegram_monitor/cloud/db.py",
                "telegram_monitor/cloud/comments.py", "telegram_monitor/cloud/sherlock_proxy.py",
                "deploy/compose.yml", "deploy/backup_loop.sh"):
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("# placeholder", encoding="utf-8")
    secret = "TEST_SECRET_SHOULD_NOT_BE_PRINTED_12345"
    (tmp_path / "deploy/.env").write_text("API_KEY=" + secret, encoding="utf-8")
    assert check.main(["--root", str(tmp_path), "--strict"]) == 0
    output = capsys.readouterr().out
    assert secret not in output
    obj = json.loads(output)
    assert obj["ready_for_manual_review"] is True
    assert obj["deployment_tested"] is False
    assert obj["env_file_present"] is True


def test_reference_compose_is_not_treated_as_live(tmp_path):
    check = _mod()
    reference = tmp_path / "deploy/compose.reference.yml"
    reference.parent.mkdir(parents=True)
    reference.write_text("services: {}", encoding="utf-8")
    assert check.assess(tmp_path)["compose_present"] is False
