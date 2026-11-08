"""Sherlock bridge tests: all mock data, no website requests or Telegram access."""
from pathlib import Path
import csv
import importlib.util
import os
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]


def load_bridge():
    spec=importlib.util.spec_from_file_location("sherlock_bridge_offline", ROOT/"deploy/sherlock/bridge.py")
    bridge=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    return bridge


def test_csv_accepts_claimed_public_urls_only(tmp_path):
    bridge=load_bridge()
    path=tmp_path/"results.csv"
    with path.open("w",newline="") as stream:
        writer=csv.writer(stream)
        writer.writerow(["name","url_user","exists"])
        writer.writerow(["GitHub","https://github.com/test_user","Claimed"])
        writer.writerow(["Not found","https://example.com/test_user","Unclaimed"])
        writer.writerow(["Internal","http://127.0.0.1/private","Claimed"])
        writer.writerow(["Invalid","javascript:alert(1)","Claimed"])
        writer.writerow(["Private network","http://10.1.2.3/test_user","Claimed"])
    assert bridge.parse_csv(path)==[{"site":"GitHub","url":"https://github.com/test_user"}]


def test_local_worker_auth_concurrency_and_cache():
    bridge=load_bridge()
    with TemporaryDirectory() as root:
        with patch.object(bridge,"DB",Path(root)/"jobs.sqlite3"), \
             patch.object(bridge,"_POOL",MagicMock()), \
             patch.dict(os.environ,{"SHERLOCK_WORKER_TOKEN":"x"*64}):
            with TestClient(bridge.app) as client:
                assert client.get("/recent").status_code==401
                auth={"Authorization":"Bearer "+"x"*64}
                assert client.get("/recent",headers=auth).status_code==200
                created=client.post("/jobs",headers=auth,json={"username":"@test_public"})
                assert created.status_code==200
                assert created.json()["state"]=="queued"
                cached=client.post("/jobs",headers=auth,json={"username":"test_public"})
                assert cached.json()["id"]==created.json()["id"]
                assert client.post("/jobs",headers=auth,json={"username":"another_user"}).status_code==429
                assert client.get("/jobs/"+created.json()["id"],headers=auth).json()["username"]=="test_public"
                assert client.get("/jobs/"+"bad",headers=auth).status_code==422


def test_worker_restart_marks_aborted_jobs(tmp_path):
    bridge=load_bridge()
    with patch.object(bridge,"DB",tmp_path/"jobs.sqlite3"):
        bridge.init_db()
        with bridge.conn() as db:
            db.execute("INSERT INTO sherlock_jobs(id,username,state,created_at) VALUES (?,?,?,?)",
                       ("f"*32,"example","running",bridge.now()))
        bridge.init_db()
        with bridge.conn() as db:
            assert db.execute("SELECT state FROM sherlock_jobs").fetchone()["state"]=="failed"


def test_scheduler_failure_does_not_leave_permanent_queue_block(tmp_path):
    """A rejected executor submission must not block every subsequent job."""
    bridge = load_bridge()
    pool = MagicMock()
    pool.submit.side_effect = RuntimeError("executor shutting down")
    with patch.object(bridge, "DB", tmp_path / "worker.sqlite3"), \
         patch.object(bridge, "_POOL", pool), \
         patch.dict(os.environ, {"SHERLOCK_WORKER_TOKEN": "x" * 64}):
        with TestClient(bridge.app) as client:
            headers = {"Authorization": "Bearer " + "x" * 64}
            for name in ("alice_public", "bob_public"):
                response = client.post("/jobs", headers=headers, json={"username": name})
                assert response.status_code == 503
                assert response.json()["detail"] == "Sherlock scheduler unavailable"
            with bridge.conn() as db:
                records = db.execute(
                    "SELECT username, state FROM sherlock_jobs ORDER BY username"
                ).fetchall()
            assert [(x["username"], x["state"]) for x in records] == [
                ("alice_public", "failed"), ("bob_public", "failed")
            ]


def _create_queued_job(bridge, username='test_public'):
    bridge.init_db()
    job_id = 'a' * 32
    with bridge.conn() as db:
        db.execute(
            "INSERT INTO sherlock_jobs(id,username,state,created_at) VALUES (?,?,?,?)",
            (job_id, username, 'queued', bridge.now()),
        )
    return job_id


def test_worker_success_uses_discarded_subprocess_output(tmp_path):
    """Mocked CLI produces a CSV; no network, no Telegram and no user secrets."""
    bridge = load_bridge()
    def write_report(command, **kwargs):
        assert kwargs['stdout'] == bridge.subprocess.DEVNULL
        assert kwargs['stderr'] == bridge.subprocess.DEVNULL
        assert kwargs['timeout'] == 180
        assert kwargs['check'] is False
        assert '--print-all' in command
        assert '--print-found' not in command
        folder = Path(command[command.index('--folderoutput') + 1])
        with (folder / 'test_public.csv').open('w', newline='') as f:
            out = csv.writer(f)
            out.writerow(['name', 'url_user', 'exists'])
            out.writerow(['Example', 'https://example.com/test_public', 'Claimed'])
        return bridge.subprocess.CompletedProcess(command, 0)

    with patch.object(bridge, 'DB', tmp_path / 'sherlock.sqlite3'), \
         patch.object(bridge.subprocess, 'run', side_effect=write_report):
        job_id = _create_queued_job(bridge)
        bridge.run_job(job_id, 'test_public')
        with bridge.conn() as db:
            row = db.execute('SELECT state, results_json, error FROM sherlock_jobs WHERE id=?', (job_id,)).fetchone()
    assert row['state'] == 'done'
    assert row['error'] == ''
    assert 'https://example.com/test_public' in row['results_json']


def test_worker_nonzero_exit_has_safe_diagnostic(tmp_path, caplog):
    bridge = load_bridge()
    with patch.object(bridge, 'DB', tmp_path / 'sherlock.sqlite3'), \
         patch.object(bridge.subprocess, 'run', return_value=bridge.subprocess.CompletedProcess([], 17)):
        job_id = _create_queued_job(bridge)
        bridge.run_job(job_id, 'test_public')
        with bridge.conn() as db:
            row = db.execute('SELECT state, error FROM sherlock_jobs WHERE id=?', (job_id,)).fetchone()
    assert row['state'] == 'failed'
    assert row['error'] == 'Sherlock subprocess returned an error'
    assert 'code=17' in caplog.text


def test_worker_zero_exit_missing_csv_is_not_marked_done(tmp_path):
    bridge = load_bridge()
    with patch.object(bridge, 'DB', tmp_path / 'sherlock.sqlite3'), \
         patch.object(bridge.subprocess, 'run', return_value=bridge.subprocess.CompletedProcess([], 0)):
        job_id = _create_queued_job(bridge)
        bridge.run_job(job_id, 'test_public')
        with bridge.conn() as db:
            row = db.execute('SELECT state, error FROM sherlock_jobs WHERE id=?', (job_id,)).fetchone()
    assert row['state'] == 'failed'
    assert row['error'] == 'Sherlock CSV report missing'


def test_worker_timeout_does_not_leave_running_job(tmp_path):
    bridge = load_bridge()
    def raise_timeout(command, **kwargs):
        raise bridge.subprocess.TimeoutExpired(command, 180)
    with patch.object(bridge, 'DB', tmp_path / 'sherlock.sqlite3'), \
         patch.object(bridge.subprocess, 'run', side_effect=raise_timeout):
        job_id = _create_queued_job(bridge)
        bridge.run_job(job_id, 'test_public')
        with bridge.conn() as db:
            row = db.execute('SELECT state, error FROM sherlock_jobs WHERE id=?', (job_id,)).fetchone()
    assert row['state'] == 'failed'
    assert '180 seconds' in row['error']


def test_worker_unknown_and_waf_are_not_reported_as_valid_empty_search(tmp_path):
    """Unknown and WAF mean no reliable site verdict, not username absence."""
    bridge = load_bridge()

    def write_inconclusive_csv(command, **kwargs):
        folder = Path(command[command.index('--folderoutput') + 1])
        with (folder / 'test_public.csv').open('w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['name', 'url_user', 'exists'])
            writer.writerow(['GitHub', 'https://github.com/test_public', 'Unknown'])
            writer.writerow(['Example', 'https://example.com/test_public', 'WAF'])
        return bridge.subprocess.CompletedProcess(command, 0)

    with patch.object(bridge, 'DB', tmp_path / 'jobs.sqlite'), \
         patch.object(bridge.subprocess, 'run', side_effect=write_inconclusive_csv):
        job_id = _create_queued_job(bridge)
        bridge.run_job(job_id, 'test_public')
        with bridge.conn() as db:
            row = db.execute('SELECT state, error, results_json FROM sherlock_jobs WHERE id=?',
                             (job_id,)).fetchone()
    assert row['state'] == 'failed'
    assert 'site checks unavailable' in row['error']
    assert row['results_json'] == '[]'


def test_worker_available_is_valid_zero_found(tmp_path):
    """An explicit Available is a completed search with no matches."""
    bridge = load_bridge()

    def write_available_csv(command, **kwargs):
        folder = Path(command[command.index('--folderoutput') + 1])
        with (folder / 'test_public.csv').open('w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['name', 'url_user', 'exists'])
            writer.writerow(['GitHub', 'https://github.com/test_public', 'Available'])
        return bridge.subprocess.CompletedProcess(command, 0)

    with patch.object(bridge, 'DB', tmp_path / 'jobs.sqlite'), \
         patch.object(bridge.subprocess, 'run', side_effect=write_available_csv):
        job_id = _create_queued_job(bridge)
        bridge.run_job(job_id, 'test_public')
        with bridge.conn() as db:
            row = db.execute('SELECT state, results_json, error FROM sherlock_jobs WHERE id=?',
                             (job_id,)).fetchone()
    assert row['state'] == 'done'
    assert row['results_json'] == '[]'
    assert row['error'] == ''


def test_worker_missing_site_rows_is_failure(tmp_path):
    bridge = load_bridge()

    def write_headers_only(command, **kwargs):
        folder = Path(command[command.index('--folderoutput') + 1])
        with (folder / 'test_public.csv').open('w', newline='') as f:
            csv.writer(f).writerow(['name', 'url_user', 'exists'])
        return bridge.subprocess.CompletedProcess(command, 0)

    with patch.object(bridge, 'DB', tmp_path / 'jobs.sqlite'), \
         patch.object(bridge.subprocess, 'run', side_effect=write_headers_only):
        job_id = _create_queued_job(bridge)
        bridge.run_job(job_id, 'test_public')
        with bridge.conn() as db:
            row = db.execute('SELECT state, error FROM sherlock_jobs WHERE id=?',
                             (job_id,)).fetchone()
    assert row['state'] == 'failed'
    assert row['error'] == 'Sherlock report contains no site checks'


def test_csv_verdict_counts_claimed_available_and_unavailable(tmp_path):
    bridge = load_bridge()
    report = tmp_path / 'verdicts.csv'
    with report.open('w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['name', 'url_user', 'exists'])
        writer.writerow(['A', 'https://example.com/a', 'Claimed'])
        writer.writerow(['B', 'https://example.com/b', 'Available'])
        writer.writerow(['C', 'https://example.com/c', 'Unknown'])
        writer.writerow(['D', 'https://example.com/d', 'WAF'])
    assert bridge.csv_verdict(report) == (4, 2, 2)
