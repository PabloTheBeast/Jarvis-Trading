"""Browser integration for supervision. Fake public/private sources only."""
import os
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
from backend.server import Store,Runtime,make_server
from backend.live import LiveService
from test_sessions import SessionMarket,SessionPrivate

with tempfile.TemporaryDirectory() as tmp:
    store=Store(Path(tmp)/'test.db');market=SessionMarket();private=SessionPrivate()
    runtime=Runtime(store,market,paper_enabled=False)
    runtime.exchange=LiveService(store,market,private,enabled=False)
    server=make_server(runtime,0);threading.Thread(target=server.serve_forever,daemon=True).start()
    runtime.monitor_market()
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':1440,'height':1100});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto('http://127.0.0.1:'+str(server.server_port))
            page.locator('#nav-supervision').click()
            page.wait_for_function("() => document.querySelector('#supervision-message').textContent.includes('uppdaterad')")
            assert page.locator('#supervision-agents article').count()==3
            assert '0,00' in page.locator('#supervision-summary').inner_text()
            assert 'REST' in page.locator('#supervision-summary').inner_text()
            form=page.locator('#research-form')
            for field,value in dict(title='<script>throw Error("bad")</script>',url='https://www.youtube.com/watch?v=test',hypothesis='Testa trend med avgifter',entry='SMA20 över SMA50',exit='SMA20 bryts',risk='Riskera högst 0,5 %').items():form.locator('[name="'+field+'"]').fill(value)
            form.locator('button').click()
            page.wait_for_function("() => document.querySelector('#research-ideas').textContent.includes('Testa trend')")
            assert page.locator('#research-ideas script').count()==0
            assert not [x for x in private.calls if x[0]=='AddOrder']
            page.screenshot(path='/tmp/jarvis-agentkontroll.png',full_page=True)
            page.set_viewport_size({'width':390,'height':844})
            assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
            assert not errors,errors
            browser.close()
        print('Governance browser passed: agent metrics, reserve, untrusted research text, mobile, no JS errors, no exchange orders.')
    finally:
        server.shutdown();server.server_close();runtime.stop();store.close()
