"""Optional owner-CLI/start/stop browser check. ALL exchange activity is mocked."""
import os
import sys
import subprocess
import tempfile
import threading
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
from backend.server import Store,Runtime,make_server
from backend.live import LiveService
from test_sessions import SessionMarket,SessionPrivate

with tempfile.TemporaryDirectory() as tmp:
    path=Path(tmp)/'paper.db';store=Store(path);market=SessionMarket();private=SessionPrivate()
    runtime=Runtime(store,market);runtime.exchange=LiveService(store,market,private,enabled=True)
    runtime.exchange.sessions.enabled=True;runtime.exchange.monitoring=True
    server=make_server(runtime,0);threading.Thread(target=server.serve_forever,daemon=True).start();runtime.start()
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto('http://127.0.0.1:'+str(server.server_port))
            page.wait_for_function("() => !document.querySelector('#session-plan').disabled")
            page.locator('#session-policy').select_option('hold');page.locator('#session-plan').click()
            page.wait_for_function("() => document.querySelector('#session-state').textContent==='Inväntar godkännande'")
            session=runtime.exchange.sessions.status()['session']
            token=subprocess.run([sys.executable,'-m','backend.ops','--db',str(path),'approve','session-start','--session',session['id']],capture_output=True,text=True,check=True).stdout.strip()
            page.on('dialog',lambda dialog:dialog.accept(token) if dialog.type=='prompt' else dialog.accept())
            page.locator('#session-start').click()
            page.wait_for_function("() => document.querySelector('#session-state').textContent==='Trading pågår'")
            runtime.exchange.sessions.tick()
            assert len(private.orders)==1
            private.settle('ORDER-1')
            if os.environ.get('JARVIS_TEST_SCREENSHOT'):page.screenshot(path=os.environ['JARVIS_TEST_SCREENSHOT'],full_page=True)
            page.locator('#session-stop').click()
            page.wait_for_function("() => document.querySelector('#session-state').textContent.includes('Avslutar')")
            runtime.exchange.sessions.tick()
            page.wait_for_function("() => document.querySelector('#session-state').textContent.includes('innehav behållna')")
            assert 'BEHÅLL' in page.locator('#session-end-decisions').inner_text()
            page.locator('#nav-history').click()
            page.wait_for_function("() => document.querySelector('#real-executions').textContent.includes('gamma')")
            assert 'Manuell' not in page.locator('#real-executions').inner_text()
            page.locator('#nav-overview').click();page.set_viewport_size({'width':390,'height':844})
            assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
            assert not errors,errors
            assert len([d for m,d in private.calls if m=='AddOrder' and d.get('validate')=='false'])==1
            assert store.state['tick']==0
            browser.close()
        print('Sessions browser passed: plan, local owner approval, automatic mock buy, stop/hold, agent-attributed history, mobile and no JS errors. No real orders sent.')
    finally:
        server.shutdown();server.server_close();runtime.stop();store.close()
