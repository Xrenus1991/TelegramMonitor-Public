"""Verify advanced Sherlock inside real isolated Docker image, no outbound traffic.

Run after docker build -t tm-sherlock-advanced:ci -f deploy/sherlock/Dockerfile deploy/sherlock
Only a disposable SQLite database and synthetic records are used.
"""
import json
import os
import subprocess
import textwrap
import time
from uuid import uuid4

IMAGE = os.environ.get("TM_SHERLOCK_TEST_IMAGE", "tm-sherlock-advanced:ci")
NAME = "tm-sherlock-stage-" + uuid4().hex[:10]
TOKEN = "synthetic-stage-token-not-a-real-credential-1234567890"


def run(args, *, timeout=60):
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          check=True, text=True, timeout=timeout).stdout


CHECK_SCRIPT = r'''
import base64
import io
import json
import os
import sqlite3
import time
import urllib.error
import urllib.request
from openpyxl import load_workbook

BASE = "http://127.0.0.1:8099"
TOKEN = os.environ["SHERLOCK_WORKER_TOKEN"]
AUTH = {"Authorization": "Bearer " + TOKEN}
ID = "a" * 32
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def request(path, *, body=None, authenticated=True):
    headers = dict(AUTH) if authenticated else {}
    data = json.dumps(body).encode() if body is not None else None
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, headers=headers, data=data)
    try:
        with opener.open(req, timeout=8) as result:
            return result.status, result.read(), result.headers
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers


def get_json(path):
    code, raw, _ = request(path)
    assert code == 200, (path, code)
    return json.loads(raw)


assert request("/capabilities", authenticated=False)[0] == 401
assert request("/v2/recent", authenticated=False)[0] == 401
assert request("/v2/jobs", body={"usernames":["octocat"]}, authenticated=False)[0] == 401
caps = get_json("/capabilities")
assert caps["version"] == "0.16.2", caps["version"]
assert caps["max_usernames"] == 5 and caps["max_sites"] == 50
assert "xlsx" in caps["formats"]
assert request("/v2/jobs", body={"usernames":["--dump-response"]})[0] == 422
assert request("/v2/jobs", body={"usernames":["example"],"sites":["bad;rm"]})[0] == 422
assert request("/v2/jobs/" + "not-a-uuid")[0] == 422
assert get_json("/v2/recent") == {"items":[]}

db_path = os.environ["SHERLOCK_DB"]
db = sqlite3.connect(db_path)
db.execute("""INSERT INTO sherlock_jobs
    (id,username,state,created_at,finished_at,results_json,error)
    VALUES (?,?,'done',?,?,?,?)""",
    (ID,"synthetic_user","2026-11-05T00:00:00+00:00",
     "2026-11-05T00:01:00+00:00",
     json.dumps([{"site":"Example","url":"https://example.com/user"}]),""))
checks = [
 {"username":"synthetic_user","site":"=TEST()","status":"claimed",
  "url":"https://example.com/user","site_url":"https://example.com",
  "http_status":200,"response_time_s":0.15},
 {"username":"synthetic_user","site":"Other","status":"available"},
]
db.execute("""INSERT INTO sherlock_advanced
    (job_id,usernames_json,options_json,checks_json,summary_json)
    VALUES (?,?,?,?,?)""",
    (ID,json.dumps(["synthetic_user"]),json.dumps({"timeout":8}),
     json.dumps(checks),json.dumps({"checked":2,"claimed":1,"available":1})))
db.commit()
db.close()
job = get_json("/v2/jobs/" + ID)
assert job["state"] == "done" and len(job["checks"]) == 2
assert len(get_json("/v2/recent")["items"]) == 1
for fmt in ("json","csv","txt","xlsx"):
    status, payload, headers = request("/v2/jobs/" + ID + "/export?format=" + fmt)
    assert status == 200 and len(payload) > 5, (fmt,status)
    assert "attachment" in headers.get("Content-Disposition","")
    if fmt == "xlsx":
        book=load_workbook(io.BytesIO(payload),read_only=True)
        assert book.active.cell(2,2).value == "'=TEST()"
        book.close()
    elif fmt == "csv":
        assert b"'=TEST()" in payload
assert request("/v2/jobs/" + ID + "/export?format=html")[0] == 422
assert request("/v2/jobs/" + ID + "/export?format=csv", authenticated=False)[0] == 401
assert request("/jobs/" + "short")[0] == 422
print("SHERLOCK_ADVANCED_CONTAINER_API_EXPORTS_OK")
'''


def main():
    try:
        run(["docker", "run", "-d", "--name", NAME, "--network", "none",
             "--read-only", "--cap-drop", "ALL", "--security-opt",
             "no-new-privileges:true", "--memory", "256m", "--cpus", "0.5",
             "--tmpfs", "/tmp:rw,size=32m,uid=10001,gid=10001,mode=1777",
             "--tmpfs", "/var/lib/sherlock:rw,size=32m,uid=10001,gid=10001,mode=0700",
             "-e", "SHERLOCK_WORKER_TOKEN=" + TOKEN,
             "-e", "SHERLOCK_DB=/var/lib/sherlock/history.sqlite3",
             IMAGE], timeout=40)
        for retry in range(25):
            try:
                run(["docker", "exec", NAME, "python", "-c",
                     "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8099/health',timeout=1).close()"],
                    timeout=4)
                break
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                time.sleep(1)
        else:
            raise RuntimeError("Sherlock container health unavailable")
        result = run(["docker", "exec", NAME, "python", "-c", CHECK_SCRIPT], timeout=40)
        assert "SHERLOCK_ADVANCED_CONTAINER_API_EXPORTS_OK" in result
        print("SHERLOCK_ADVANCED_DOCKER_NETWORK_NONE_OK")
        print("SHERLOCK_ADVANCED_CONTAINER_API_EXPORTS_OK")
    finally:
        subprocess.run(["docker","rm","-f","-v",NAME], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=False, timeout=25)


if __name__ == "__main__":
    main()
