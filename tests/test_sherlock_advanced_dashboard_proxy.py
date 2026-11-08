"""Dashboard-side advanced Sherlock: auth, origin, export and page safety."""
from fastapi.testclient import TestClient
import pytest

from telegram_monitor.cloud import api, sherlock_proxy


@pytest.fixture
def dashboard(monkeypatch):
    monkeypatch.setenv("DASHBOARD_USER","operator")
    monkeypatch.setenv("DASHBOARD_PASSWORD","synthetic-stage-password-very-long")
    monkeypatch.setenv("SHERLOCK_OSINT_ENABLED","true")
    monkeypatch.setenv("SHERLOCK_WORKER_TOKEN","test-stage-token-not-a-secret-0000000000000000")
    seen = []
    def fake_request(method, path, payload=None):
        seen.append((method, path, payload))
        if method == "POST":
            return {"id": "a"*32, "state": "queued"}
        return {"items": []}
    monkeypatch.setattr(sherlock_proxy,"_request",fake_request)
    yield TestClient(api.app), ("operator","synthetic-stage-password-very-long"), seen


def test_advanced_page_and_endpoints_require_auth(dashboard):
    client, auth, seen = dashboard
    for path in ("/sherlock","/api/osint/sherlock/capabilities",
                 "/api/osint/sherlock/v2/recent",
                 "/api/osint/sherlock/v2/jobs/" + "a"*32):
        assert client.get(path).status_code == 401
    assert client.post("/api/osint/sherlock/v2/jobs",json={"usernames":["alice"]}).status_code == 401
    response=client.get("/sherlock",auth=auth)
    assert response.status_code==200
    assert "Sherlock · расширенный поиск" in response.text
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"].startswith("no-store")
    assert client.get("/api/osint/sherlock/capabilities",auth=auth).status_code==200
    assert client.get("/api/osint/sherlock/v2/recent",auth=auth).status_code==200
    assert not any(m=="POST" for m,_,_ in seen)


def test_advanced_post_requires_same_origin_json_and_does_not_allow_unbounded_requests(dashboard):
    client, auth, seen = dashboard
    path="/api/osint/sherlock/v2/jobs"
    good={"Origin":"http://testserver","X-TM-Action":"1",
          "Content-Type":"application/json"}
    bad_origin={**good,"Origin":"http://evil.example"}
    assert client.post(path,auth=auth,headers=bad_origin,
                       json={"usernames":["alice"]}).status_code==403
    assert client.post(path,auth=auth,
                       json={"usernames":["alice"]}).status_code==403
    assert client.post(path,auth=auth,headers=good,
                       json={"usernames":["a"*11995]}).status_code==413
    ok=client.post(path,auth=auth,headers=good,
                   json={"usernames":["octocat"],"sites":["GitHub"]})
    assert ok.status_code==200 and ok.json()["state"]=="queued"
    assert seen==[("POST","/v2/jobs",{"usernames":["octocat"],"sites":["GitHub"]})]


def test_advanced_job_routes_validate_ids_and_export(dashboard,monkeypatch):
    client, auth, seen = dashboard
    assert client.get("/api/osint/sherlock/v2/jobs/bad",auth=auth).status_code==422
    assert client.get("/api/osint/sherlock/v2/jobs/" + "a"*32,auth=auth).status_code==200
    assert seen[-1]==("GET","/v2/jobs/"+"a"*32,None)
    assert client.get("/api/osint/sherlock/v2/jobs/"+"a"*32+"/export?format=html",auth=auth).status_code==422
    def fake_urlopen(request,timeout=30):
        raise RuntimeError("must not contact remote worker in validation test")
    monkeypatch.setattr(sherlock_proxy.urllib.request,"urlopen",fake_urlopen)
    assert client.get("/api/osint/sherlock/v2/jobs/invalid/export?format=csv",auth=auth).status_code==422
