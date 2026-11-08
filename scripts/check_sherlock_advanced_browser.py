"""Chromium advanced-panel regression with synthetic responses only."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
HTML=Path('telegram_monitor/cloud/sherlock_full.html').read_text()
JOB={'id':'b'*32,'usernames':['octocat','example'],'state':'done','summary':{'claimed':1,'available':1,'waf':1},
'checks':[{'username':'octocat','site':'GitHub','status':'claimed','url':'https://github.com/octocat','http_status':200},
{'username':'example','site':'<img src=x onerror=alert(1)>','status':'available','url':'javascript:alert(1)'},
{'username':'example','site':'Guard','status':'waf'}]}
with sync_playwright() as p:
    browser=p.chromium.launch()
    context=browser.new_context(accept_downloads=True)
    posts=[];exports=[];fail=[False]
    def handler(route):
        path=route.request.url.split('http://tm.test',1)[-1]
        if path=='/sherlock':
            route.fulfill(content_type='text/html',body=HTML);return
        if path.endswith('/capabilities'):
            data={'version':'test','max_usernames':5,'max_sites':50,'proxy_available':False,'catalogs':['builtin','approved.json'],'sites':['GitHub','Reddit']}
        elif '/export?format=' in path:
            exports.append(path.rsplit('=',1)[1]);route.fulfill(content_type='application/octet-stream',body='fixture');return
        elif path.endswith('/v2/recent'):data={'items':[JOB]}
        elif path.endswith('/v2/jobs') and route.request.method=='POST':
            posts.append(json.loads(route.request.post_data))
            if fail[0]:
                fail[0]=False;route.fulfill(status=503,content_type='application/json',body='{"detail":"temporarily unavailable"}');return
            data=JOB
        elif '/v2/jobs/' in path:data=JOB
        else:raise AssertionError(path)
        route.fulfill(content_type='application/json',body=json.dumps(data))
    context.route('**/*',handler)
    page=context.new_page();errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto('http://tm.test/sherlock')
    expect(page.locator('#capabilities')).to_contain_text('до 5 username')
    expect(page.locator('#proxy')).to_be_disabled()
    page.locator('#site-choice').fill('GitHub');page.locator('#add-site').click()
    page.locator('#site-choice').fill('GitHub');page.locator('#add-site').click()
    expect(page.locator('#sites')).to_have_value('GitHub')
    page.locator('#names').fill('\n'.join('user'+str(i) for i in range(6)))
    page.locator('#start').click()
    expect(page.locator('#status')).to_contain_text('от 1 до 5')
    assert posts==[]
    page.locator('#names').fill('octocat\nexample')
    page.locator('#timeout').fill('5')
    page.locator('#nsfw').check();page.locator('#exclusions').check()
    page.locator('#catalog').select_option('approved.json')
    page.locator('#start').click()
    expect(page.locator('#rows .row')).to_have_count(3)
    assert posts==[{'usernames':['octocat','example'],'sites':['GitHub'],'timeout':5,'include_nsfw':True,'ignore_exclusions':True,'use_proxy':False,'catalog':'approved.json'}]
    expect(page.locator('#rows img')).to_have_count(0)
    expect(page.locator('#rows a')).to_have_count(1)
    expect(page.locator('#rows a')).to_have_attribute('rel','noopener noreferrer')
    page.locator('#filter').select_option('waf')
    expect(page.locator('#rows .row')).to_have_count(1)
    expect(page.locator('#rows')).to_contain_text('Guard')
    for fmt in ('json','csv','xlsx','txt'):
        with page.expect_download() as download:
            page.locator('[data-format="'+fmt+'"]').click()
        assert download.value.suggested_filename.endswith('.'+fmt)
    assert exports==['json','csv','xlsx','txt']
    page.locator('#history').click()
    page.locator('#history-items button').click()
    expect(page.locator('#details')).to_contain_text('octocat')
    fail[0]=True
    page.locator('#start').click()
    expect(page.locator('#status')).to_contain_text('HTTP 503')
    expect(page.locator('#start')).to_be_enabled()
    page.locator('#start').click()
    expect(page.locator('#status')).to_contain_text('done')
    assert not errors,errors
    browser.close()
print('SHERLOCK_ADVANCED_CHROMIUM_OK')
