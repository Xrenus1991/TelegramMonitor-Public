"""Chromium: distinct source stats, navigation and visible auto-refresh."""
import json
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from playwright.sync_api import sync_playwright, expect

html=Path("telegram_monitor/cloud/dashboard_v8_3.html").read_text(encoding="utf-8")
sources=[
    {"peer_id":101,"title":"Renamed Group","username":"foo","kind":"megagroup",
     "visibility":"public","status":"approved",
     "analysis_progress":{"total":5,"done":2,"pending":1,"processing":1,"failed":1,"other":0,"percent":40.0},
     "recent_request":{"id":"r1","count":1000,"status":"done",
                       "requested_at":"2026-11-05T10:00:00Z","finished_at":"2026-11-05T10:01:00Z",
                       "scanned_count":1000,"saved_count":2,"error_kind":None}},
    {"peer_id":102,"title":"Renamed Group","username":"bar","kind":"megagroup",
     "visibility":"public","status":"approved",
     "analysis_progress":{"total":1,"done":1,"pending":0,"processing":0,"failed":0,"other":0,"percent":100.0},
     "recent_request":None},
]
queries=[]
with sync_playwright() as p:
    browser=p.chromium.launch()
    page=browser.new_page()
    errors=[]
    page.on("pageerror",lambda error:errors.append(str(error)))
    def handler(route):
        path=urlsplit(route.request.url)
        if path.path=="/":
            route.fulfill(content_type="text/html",body=html)
            return
        if path.path=="/api/sources":
            response={"enabled":True,"initialized":True,"editable":True,"recent_enabled":True,
                      "progress_available":True,"items":sources}
        elif path.path=="/api/events":
            args=parse_qs(path.query)
            queries.append(args)
            response={"total":1,"items":[{"key":"101:1","source":"Previous name",
                      "timestamp":"2026-11-05T12:00:00Z",
                      "classification":{"summary_ru":"Synthetic result"},
                      "sender_user_id":123}],"has_more":False}
        elif path.path=="/api/filters":response={"sources":[],"topics":[]}
        elif path.path=="/api/sources/audit":response={"items":[]}
        else:response={}
        route.fulfill(content_type="application/json",body=json.dumps(response))
    page.route("**/*",handler)
    page.goto("http://monitor.test/")
    expect(page.locator("#sources-list .source-row")).to_have_count(2)
    expect(page.locator("#sources-list .source-row").first).to_contain_text("Общий прогресс: 40%")
    expect(page.locator("#sources-list .source-row").first).to_contain_text("ожидают: 1")
    expect(page.locator("#sources-list .source-row").first).to_contain_text("Последний запрос")
    expect(page.locator("#sources-list .source-row").first).to_contain_text("просмотрено 1")
    field=page.locator('input[data-recent-count="101"]')
    field.fill("845")
    page.locator('button[data-source-id="101"][data-action="results"]').click()
    expect(page.locator("#events")).to_contain_text("Synthetic result")
    assert any(x.get("source_id")==["101"] for x in queries),queries
    assert page.locator("#source").input_value()==""
    page.locator("#refresh").click()
    expect(page.locator('input[data-recent-count="101"]')).to_have_value("845")
    page.locator("#reset").click()
    # Verify the filter state synchronously; requests may coalesce with refresh.
    assert page.evaluate("sourceResultId") is None
    assert page.evaluate("sourceResultTitle") == ""
    assert not errors,errors
    browser.close()
print("SOURCE_PROGRESS_CHROMIUM_OK")
