"""Chromium regression for manual Unicode inspection; synthetic API only."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
HTML=Path("telegram_monitor/cloud/dashboard_v8_3.html").read_text()
with sync_playwright() as p:
    browser=p.chromium.launch()
    context=browser.new_context()
    calls=[];failure=[False]
    source="Гpaницa <img src=x onerror=alert(1)>"
    def handler(route):
        path=route.request.url.split("http://tm.test",1)[-1]
        if path=="/":route.fulfill(content_type="text/html",body=HTML);return
        if path.startswith("/api/text/variants"):
            calls.append(path)
            if failure[0]:route.fulfill(status=503,content_type="application/json",body='{}');return
            data={"key":"123:1","original_excerpt":source,"normalized_excerpt":"граница <img src=x onerror=alert(1)>","version":"ru-context-v1","truncated":True,"source_length":5000,"persisted":False,"findings":[{"start":0,"end":7,"suggestion":"граница"}]}
        elif path.startswith("/api/events"):
            data={"items":[{"key":"123:1","source":"Synthetic","timestamp":"2026-11-05T00:00:00Z","classification":{"summary_ru":"Synthetic summary","priority":"low"}}],"total":1,"has_more":False}
        elif path.startswith("/api/filters"):data={"sources":[],"topics":[]}
        elif path.startswith("/api/sources"):data={"enabled":False}
        elif path.startswith("/api/comments/overview"):data={"enabled":False}
        else:data={}
        route.fulfill(content_type="application/json",body=json.dumps(data))
    context.route("**/*",handler)
    page=context.new_page();errors=[]
    page.on("pageerror",lambda e:errors.append(str(e)))
    page.goto("http://tm.test/")
    button=page.get_by_role("button",name="Проверить подмены букв")
    expect(button).to_be_visible();assert calls==[]
    button.click()
    area=page.locator(".text-variant-result")
    expect(area).to_contain_text(source)
    expect(area).to_contain_text("граница")
    expect(area).to_contain_text("Версия проверки: ru-context-v1")
    expect(area).to_contain_text("до 4000 символов")
    assert page.locator(".text-variant-result img").count()==0
    expect(page.locator(".event .summary")).to_have_text("Synthetic summary")
    page.get_by_role("button",name="Скрыть проверку").click()
    expect(area).to_be_hidden()
    page.get_by_role("button",name="Проверить подмены букв").click()
    expect(area).to_be_visible();assert len(calls)==1
    failure[0]=True
    page.reload()
    page.get_by_role("button",name="Проверить подмены букв").click()
    expect(page.locator(".text-variant-result")).to_contain_text("временно недоступна")
    failure[0]=False
    page.get_by_role("button",name="Скрыть проверку").click()
    page.get_by_role("button",name="Проверить подмены букв").click()
    expect(page.locator(".text-variant-result")).to_contain_text("Версия проверки: ru-context-v1")
    assert len(calls)==3 and not errors,errors
    browser.close()
print("UNICODE_UI_BROWSER_OK")
