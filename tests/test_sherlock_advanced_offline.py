"""Advanced Sherlock offline tests. No network access to social media."""
import csv
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch
import importlib.util
import io
import os

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]


def bridge_module():
    spec = importlib.util.spec_from_file_location(
        "advanced_sherlock_test", ROOT / "deploy/sherlock/bridge.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_options_validation_and_no_injection():
    b = bridge_module()
    a = b._tm_advanced
    req = a.AdvancedSearch(
        usernames=["@alice{?}", "bob_user"], sites=["GitHub", "Reddit"],
        timeout=15, include_nsfw=True, ignore_exclusions=True)
    command = a.build_command(req, Path("/tmp/reports"))
    assert command[-4:] == ["alice_", "alice-", "alice.", "bob_user"]
    for flag in ("--local", "--csv", "--print-all", "--nsfw",
                 "--ignore-exclusions", "--site", "--timeout"):
        assert flag in command
    assert "--proxy" not in command and "--json" not in command
    for name in ("--help", "bob;rm -rf /", "a\nb"):
        with pytest.raises(ValueError):
            a.AdvancedSearch(usernames=[name])
    with pytest.raises(ValueError):
        a.AdvancedSearch(usernames=["alice", "alice"])
    with pytest.raises(ValueError):
        a.AdvancedSearch(usernames=["alice"], sites=["Example;ls"])
    with pytest.raises(ValueError):
        a.AdvancedSearch(usernames=["alice"], timeout=999)
    with pytest.raises(ValueError):
        a.AdvancedSearch(usernames=["alice"], catalog="https://example.com/x.json")


def test_proxy_and_catalog_admin_only(tmp_path):
    b = bridge_module()
    a = b._tm_advanced
    req = a.AdvancedSearch(usernames=["alice"], use_proxy=True)
    with patch.dict(os.environ, {"SHERLOCK_PROXY_URL": ""}):
        with pytest.raises(ValueError):
            a.build_command(req, tmp_path)
    (tmp_path / "custom.json").write_text("{}")
    with patch.dict(os.environ, {"SHERLOCK_CATALOG_DIR": str(tmp_path),
                                  "SHERLOCK_PROXY_URL": "socks5://127.0.0.1:1080"}):
        assert a.catalogs() == ["custom.json"]
        req = a.AdvancedSearch(usernames=["alice"], use_proxy=True,
                               catalog="custom.json")
        cmd = a.build_command(req, tmp_path)
        assert "--proxy" in cmd and "--json" in cmd and "--local" not in cmd
    with patch.dict(os.environ, {"SHERLOCK_CATALOG_DIR": ""}):
        with pytest.raises(ValueError):
            a.build_command(a.AdvancedSearch(usernames=["alice"],
                                             catalog="custom.json"), tmp_path)


def test_csv_statuses_and_spreadsheet_formula_defense(tmp_path):
    b = bridge_module()
    a = b._tm_advanced
    file = tmp_path / "alice.csv"
    with file.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["name", "url_user", "exists"])
        writer.writerow(["GitHub", "https://github.com/alice", "Claimed"])
        writer.writerow(["Bad", "http://127.0.0.1/x", "Claimed"])
        writer.writerow(["Other", "https://other.example/a", "Available"])
        writer.writerow(["=FORMULA()", "https://bad.example", "WAF"])
    checks, hits, definitive = a.parse_checks(file, "alice", vars(b))
    assert definitive == 2
    assert len(hits) == 1
    assert checks[1]["status"] == "unknown"
    assert checks[3]["status"] == "waf"
    job = {"checks": checks}
    data, mime = a.exports(job, "csv")
    assert b"'=FORMULA()" in data
    assert "text/csv" in mime
    data, mime = a.exports(job, "xlsx")
    sheet = load_workbook(io.BytesIO(data), read_only=True).active
    assert sheet.cell(5, 2).value == "'=FORMULA()"
    assert "spreadsheetml" in mime
    assert a.exports(job, "txt")[0]
    assert a.exports(job, "json")[0]


def test_advanced_api_auth_queue_export_and_history(tmp_path):
    b = bridge_module()
    pool = MagicMock()
    with patch.object(b, "DB", tmp_path / "db.sqlite3"), \
         patch.object(b, "_POOL", pool), \
         patch.dict(os.environ, {"SHERLOCK_WORKER_TOKEN": "x" * 64}):
        with TestClient(b.app) as client:
            headers = {"Authorization": "Bearer " + "x" * 64}
            assert client.get("/capabilities").status_code == 401
            assert client.get("/capabilities", headers=headers).status_code == 200
            assert client.post("/v2/jobs", headers=headers, json={
                "usernames": ["alice", "bob"], "sites": ["GitHub"]}).status_code == 200
            assert client.post("/v2/jobs", headers=headers, json={
                "usernames": ["another"]}).status_code == 429
            job = client.get("/v2/recent", headers=headers).json()["items"][0]
            assert job["usernames"] == ["alice", "bob"]
            assert job["state"] == "queued"
            assert client.get("/v2/jobs/" + job["id"], headers=headers).status_code == 200
            assert client.get("/v2/jobs/bad", headers=headers).status_code == 422
            assert client.get("/v2/jobs/" + job["id"] + "/export",
                              headers=headers).status_code == 409
            assert client.post("/v2/jobs", headers=headers, json={
                "usernames": ["bad;command"]}).status_code == 422


def test_advanced_cli_mock_creates_found_and_statuses(tmp_path):
    b = bridge_module()
    a = b._tm_advanced
    def fake_run(command, **kwargs):
        assert kwargs["stdout"] == b.subprocess.DEVNULL
        assert kwargs["stderr"] == b.subprocess.DEVNULL
        assert "--print-all" in command
        folder = Path(command[command.index("--folderoutput") + 1])
        for name in ("alice", "bob"):
            with (folder / (name + ".csv")).open("w", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["name", "url_user", "exists"])
                writer.writerow(["GitHub", "https://github.com/" + name, "Claimed"])
                writer.writerow(["Reddit", "https://reddit.com/u/" + name, "Available"])
        return b.subprocess.CompletedProcess(command, 0)

    pool = MagicMock()
    with patch.object(b, "DB", tmp_path / "db.sqlite3"), \
         patch.object(b, "_POOL", pool), \
         patch.object(a.subprocess, "run", side_effect=fake_run), \
         patch.dict(os.environ, {"SHERLOCK_WORKER_TOKEN": "x" * 64}):
        with TestClient(b.app) as client:
            headers = {"Authorization": "Bearer " + "x" * 64}
            result = client.post("/v2/jobs", headers=headers, json={
                "usernames": ["alice", "bob"], "timeout": 8}).json()
            runner, job_id, request = pool.submit.call_args.args
            runner(job_id, request)
            final = client.get("/v2/jobs/" + result["id"], headers=headers).json()
            assert final["state"] == "done"
            assert len(final["results"]) == 2
            assert final["summary"] == {"claimed": 2, "available": 2,
                                        "unknown": 0, "waf": 0, "illegal": 0,
                                        "checked": 4}
            assert client.get("/v2/jobs/" + job_id + "/export?format=csv",
                              headers=headers).status_code == 200
            assert client.get("/v2/jobs/" + job_id + "/export?format=xlsx",
                              headers=headers).status_code == 200


def test_wildcard_job_reads_concrete_reports_and_deduplicates(tmp_path):
    b = bridge_module()
    a = b._tm_advanced
    assert a.expanded_usernames(["alice{?}", "alice_"]) == ["alice_", "alice-", "alice."]
    def fake_run(command, **kwargs):
        assert command[-3:] == ["alice_", "alice-", "alice."]
        assert not any("{?}" in arg for arg in command)
        folder = Path(command[command.index("--folderoutput") + 1])
        for name in ("alice_", "alice-", "alice."):
            with (folder / (name + ".csv")).open("w", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["name", "url_user", "exists"])
                writer.writerow(["GitHub", "https://github.com/" + name, "Available"])
        return b.subprocess.CompletedProcess(command, 0)
    pool = MagicMock()
    with patch.object(b, "DB", tmp_path / "db.sqlite3"), \
         patch.object(b, "_POOL", pool), \
         patch.object(a.subprocess, "run", side_effect=fake_run), \
         patch.dict(os.environ, {"SHERLOCK_WORKER_TOKEN": "x" * 64}):
        with TestClient(b.app) as client:
            headers = {"Authorization": "Bearer " + "x" * 64}
            result = client.post("/v2/jobs", headers=headers, json={
                "usernames": ["alice{?}", "alice_"], "sites": ["GitHub"]}).json()
            runner, job_id, request = pool.submit.call_args.args
            runner(job_id, request)
            final = client.get("/v2/jobs/" + job_id, headers=headers).json()
            assert final["state"] == "done"
            assert final["usernames"] == ["alice{?}", "alice_"]
            assert [row["username"] for row in final["checks"]] == ["alice_", "alice-", "alice."]
            assert final["summary"]["checked"] == 3
            assert final["summary"]["available"] == 3
