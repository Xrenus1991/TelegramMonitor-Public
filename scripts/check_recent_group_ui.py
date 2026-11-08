"""Synthetic Chromium check for the manual per-group recent-message action."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

HTML = Path("telegram_monitor/cloud/dashboard_v8_3.html").read_text(encoding="utf-8")
source = {"peer_id": 111, "title": "Synthetic Group", "username": "synthetic",
          "kind": "megagroup", "visibility": "public", "status": "approved",
          "recent_request": None}
disabled = {"peer_id": 222, "title": "Disabled Group", "username": "disabled",
            "kind": "megagroup", "visibility": "public", "status": "disabled",
            "recent_request": None}
data = {"enabled": True, "initialized": True, "editable": True,
        "recent_enabled": True, "items": [source, disabled]}
posts = []
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    page.on("dialog", lambda dialog: dialog.accept())

    def handler(route):
        url = route.request.url
        path = url.split("http://tm.test", 1)[-1].split("#")[0]
        if path == "/":
            route.fulfill(content_type="text/html", body=HTML)
            return
        if path.startswith("/api/sources/111/recent"):
            assert route.request.method == "POST"
            payload = json.loads(route.request.post_data)
            posts.append(payload)
            assert route.request.headers.get("x-tm-action") == "1"
            source["recent_request"] = {
                "id": "synthetic", "status": "pending", "count": payload["count"],
                "requested_at": "2026-11-05T00:00:00Z", "finished_at": None,
                "saved_count": None, "scanned_count": None, "error_kind": None,
            }
            route.fulfill(status=202, content_type="application/json",
                          body=json.dumps({"id": "synthetic", "status": "pending", **payload}))
            return
        if path.startswith("/api/sources/audit"):
            response = {"items": []}
        elif path.startswith("/api/sources"):
            response = data
        elif path.startswith("/api/events"):
            response = {"items": [], "total": 0, "has_more": False}
        elif path.startswith("/api/filters"):
            response = {"sources": [], "topics": []}
        elif path.startswith("/api/comments/overview"):
            response = {"enabled": True, "initialized": True, "threads": [], "total_comments": 0}
        else:
            response = {}
        route.fulfill(content_type="application/json", body=json.dumps(response))

    page.route("**/*", handler)
    page.goto("http://tm.test/")
    expect(page.locator("#sources-list .source-row")).to_have_count(2)
    start = page.locator('button[data-source-id="111"][data-action="recent"]')
    expect(start).to_be_visible()
    expect(page.locator('button[data-source-id="222"][data-action="recent"]')).to_have_count(0)
    field = page.locator('input[data-recent-count="111"]')
    field.fill("1001")
    start.click()
    expect(page.locator("#error")).to_contain_text("от 1 до 1000")
    assert not posts
    field.fill("1000")
    start.click()
    expect(page.locator("#sources-list")).to_contain_text("ожидает Collector")
    assert posts == [{"count": 1000}], posts
    expect(page.locator('button[data-source-id="111"][data-action="recent"]')).to_be_disabled()
    assert not errors, errors
    browser.close()
print("RECENT_GROUP_UI_CHROMIUM_OK")
