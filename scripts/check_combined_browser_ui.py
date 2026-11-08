"""Real Chromium checks with synthetic API responses; no external search or production access."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
HTML=Path('telegram_monitor/cloud/dashboard_v8_3.html').read_text()
JOB={'id':'a'*32,'username':'octocat','state':'done','created_at':'2026-11-05T00:00:00Z','finished_at':'2026-11-05T00:00:01Z','results':[{'site':'GitHub','url':'https://github.com/octocat'}],'error':''}
sources={'enabled':True,'initialized':True,'editable':True,'items':[
 {'peer_id':11,'title':'Selected group','username':'selected','kind':'megagroup','visibility':'public','status':'approved'},
 {'peer_id':12,'title':'Disabled channel','username':'disabled','kind':'channel','visibility':'public','status':'disabled'}]}
with sync_playwright() as p:
    browser=p.chromium.launch()
    context=browser.new_context()
    posts=[]
    def handler(route):
        path=route.request.url.split('http://tm.test',1)[-1].split('#')[0]
        if path=='/':route.fulfill(content_type='text/html',body=HTML);return
        if path.startswith('/api/osint/sherlock/jobs'):
            if route.request.method=='POST':posts.append(json.loads(route.request.post_data))
            data=JOB
        elif path.startswith('/api/osint/sherlock/recent'):data={'items':[JOB]}
        elif path.startswith('/api/sources/audit'):data={'items':[]}
        elif path.startswith('/api/sources'):data=sources
        elif path.startswith('/api/filters'):data={'sources':[],'topics':[]}
        elif path.startswith('/api/events'):data={'items':[],'total':0,'has_more':False}
        elif path.startswith('/api/comments/overview'):data={'enabled':True,'initialized':True,'threads':[],'total_comments':0}
        else:data={}
        route.fulfill(content_type='application/json',body=json.dumps(data))
    context.route('**/*',handler)
    page=context.new_page()
    errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto('http://tm.test/')
    expect(page.locator('#sources-list .source-row')).to_have_count(2)
    page.locator('#source-search').fill('Disabled')
    expect(page.locator('#sources-list .source-row')).to_have_count(1)
    expect(page.locator('#sources-list')).to_contain_text('Disabled channel')
    page.locator('#source-search').fill('')
    page.locator('#source-filter').select_option('approved')
    expect(page.locator('#sources-list .source-row')).to_have_count(1)
    expect(page.locator('#sources-list')).to_contain_text('Selected group')
    # Force popup blocking and confirm the visible recovery link.
    page.evaluate('window.open=()=>null')
    page.locator('#sherlock-username').fill('octocat')
    page.locator('#sherlock-username').press('Enter')
    expect(page.locator('#sherlock-results')).to_contain_text('Найдено публичных профилей: 1')
    link=page.locator('#sherlock-results a').filter(has_text='Открыть карточку')
    expect(link).to_have_attribute('href','http://tm.test/#sherlock-job='+'a'*32)
    assert posts==[{'username':'octocat'}]
    # History provides a card link when popups are blocked.
    page.locator('#sherlock-history').click()
    page.locator('#sherlock-results button').first.click()
    expect(page.locator('#sherlock-results a').filter(has_text='Открыть карточку')).to_be_visible()
    detail=context.new_page()
    detail.goto('http://tm.test/#sherlock-job='+'a'*32)
    expect(detail.locator('#sherlock-results')).to_contain_text('GitHub')
    expect(detail.locator('#sherlock-results')).to_contain_text('Завершено')
    # Other input types only prepare links and must never submit to Sherlock.
    page.locator('#sherlock-type').select_option('email')
    page.locator('#sherlock-username').fill('synthetic@example.com')
    page.locator('#sherlock-start').click()
    expect(page.locator('#sherlock-results')).to_contain_text('Автоматическая загрузка результатов в карточку пока не подключена')
    assert len(posts)==1
    assert not errors,errors
    browser.close()
print('COMBINED_BROWSER_UI_OK')
