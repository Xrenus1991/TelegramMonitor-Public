"""Chromium smoke for the opt-in bounded variant search UI; synthetic fixtures only."""
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright

HTML = Path("telegram_monitor/cloud/dashboard_v8_3.html").read_text(encoding="utf-8")
RAW = "пoжар <img src=x onerror=alert(1)>"
with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome")
    context = browser.new_context()
    observed = []
    disabled = [False]
    errors = []

    def handler(route):
        url = urlsplit(route.request.url)
        path = url.path
        if path == "/":
            route.fulfill(content_type="text/html", body=HTML)
            return
        if path == "/api/text/search-variants":
            observed.append(parse_qs(url.query))
            if disabled[0]:
                route.fulfill(status=503, content_type="application/json", body='{"detail":"disabled"}')
                return
            data = {
                "scope": "latest_candidate_window", "scanned": 100,
                "candidate_limit": 100, "candidate_truncated": True,
                "text_truncated": True, "version": "ru-context-v1",
                "next_before": None if "before" in parse_qs(url.query) else "2026-11-05T12:00:00+00:00",
                "next_before_key": None if "before" in parse_qs(url.query) else "11:1",
                "matches": [
                    {"key": "11:2", "matched_on": "normalized", "version": "ru-context-v1"},
                    {"key": "11:1", "matched_on": "original", "version": "ru-context-v1"},
                ],
            }
        elif path == "/api/text/variants":
            assert parse_qs(url.query)["key"] == ["11:2"]
            data = {
                "key": "11:2", "original_excerpt": RAW,
                "normalized_excerpt": "пожар <img src=x onerror=alert(1)>",
                "findings": [{"start": 0, "end": 5, "suggestion": "пожар"}],
                "version": "ru-context-v1", "truncated": False,
            }
        elif path.startswith("/api/events"):
            data = {"items": [], "total": 0, "has_more": False}
        elif path.startswith("/api/filters"):
            data = {"sources": [], "topics": []}
        elif path.startswith("/api/sources"):
            data = {"enabled": False}
        elif path.startswith("/api/comments/overview"):
            data = {"enabled": False}
        else:
            data = {}
        route.fulfill(content_type="application/json", body=json.dumps(data))

    context.route("**/*", handler)
    page = context.new_page()
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto("http://tm.test/")
    assert observed == []
    page.locator("#variant-q").fill("пожар")
    page.locator("#variant-source-id").fill("11")
    page.get_by_role("button", name="Найти варианты").click()
    expect(page.locator("#variant-search-results .analytics-card")).to_have_count(2)
    expect(page.locator("#variant-search-status")).to_contain_text("ВНИМАНИЕ: есть более старые сообщения")
    expect(page.locator("#variant-search-status")).to_contain_text("длинные сообщения проверены частично")
    assert observed == [{"q": ["пожар"], "source_id": ["11"]}]
    page.locator("#variant-search-results .analytics-card").first.get_by_role("button", name="Проверить подмены букв").click()
    expect(page.locator("#variant-search-results")).to_contain_text(RAW)
    assert page.locator("#variant-search-results img").count() == 0

    page.locator("#variant-search-older").click()
    expect(page.locator("#variant-search-older")).to_be_hidden()
    assert observed[1] == {"q":["пожар"],"source_id":["11"],
                           "before":["2026-11-05T12:00:00+00:00"],"before_key":["11:1"]}
    page.get_by_role("button", name="Найти варианты").click()
    expect(page.locator("#variant-search-older")).to_be_visible()
    assert "before" not in observed[2]
    page.locator("#variant-q").fill("протест")
    expect(page.locator("#variant-search-older")).to_be_hidden()
    page.locator("#variant-q").fill("пожар")

    # Invalid source IDs must not lead to a network search.
    page.locator("#variant-source-id").fill("abc")
    page.get_by_role("button", name="Найти варианты").click()
    expect(page.locator("#variant-search-status")).to_contain_text("положительный числовой ID")
    assert len(observed) == 3

    # Disabled feature is reported as disabled, not as an empty full-history result.
    disabled[0] = True
    page.locator("#variant-source-id").fill("")
    page.get_by_role("button", name="Найти варианты").click()
    expect(page.locator("#variant-search-status")).to_contain_text("выключен или временно недоступен")
    assert observed[3] == {"q": ["пожар"]}
    assert not errors, errors
    browser.close()
print("BOUNDED_VARIANT_SEARCH_UI_BROWSER_OK")
