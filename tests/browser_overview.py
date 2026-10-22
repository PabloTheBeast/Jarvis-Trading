"""Live overview integration. All market/account activity is mocked."""
import json
import sys
import tempfile
import threading
from pathlib import Path
from datetime import datetime,timezone,timedelta
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
from backend.server import Store,Runtime,make_server
from backend.live import LiveService
from test_sessions import SessionMarket,SessionPrivate

with tempfile.TemporaryDirectory() as tmp:
    store=Store(Path(tmp)/'db');market=SessionMarket();private=SessionPrivate()
    runtime=Runtime(store,market,paper_enabled=False);runtime.exchange=LiveService(store,market,private,enabled=False)
    runtime.exchange.monitor_only=True;runtime.exchange.monitoring=True
    private.balance['ZEUR']['balance']='100';private.balance['XXBT']['balance']='.001'
    private.orders['OPEN-1']=dict(status='open',vol='.0001',vol_exec='0',descr=dict(pair='XBTEUR',type='buy',price='60000',ordertype='limit'))
    runtime.monitor_market();runtime.exchange.inspect()
    server=make_server(runtime,0);threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':1440,'height':1050});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto('http://127.0.0.1:'+str(server.server_port))
            page.wait_for_function("() => document.querySelector('#overview-status').textContent.includes('Kraken-konto · aktuellt')")
            assert '160' in page.locator('#real-overview').inner_text() or '159' in page.locator('#real-overview').inner_text()
            assert page.locator('#overview-live-agents article').count()==3
            assert 'Okänd inköpskostnad' in page.locator('#overview-holdings').inner_text()
            assert 'OPEN-1' in page.locator('#overview-orders').inner_text()
            assert 'Inga verifierade' in page.locator('#overview-fills').inner_text()
            assert page.locator('#session-start').is_disabled()
            page.screenshot(path='/tmp/estman-live-overview-test.png',full_page=True)
            page.set_viewport_size({'width':390,'height':844})
            assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
            # A failed overview request must visibly invalidate retained numbers.
            page.route('**/api/overview',lambda route:route.abort())
            page.wait_for_function("() => document.querySelector('#overview-status').textContent.includes('Serverkontakt saknas')")
            assert 'senast hämtade' in page.locator('#overview-refresh-status').inner_text()
            page.unroute('**/api/overview')
            old=(datetime.now(timezone.utc)-timedelta(seconds=120)).isoformat()
            runtime.last_metadata['fetchedAt']=old;runtime.exchange.last_account['checkedAt']=old
            page.wait_for_function("() => document.querySelector('#overview-status').textContent.includes('Kraken-konto · ej aktuellt') && document.querySelector('#overview-status').textContent.includes('Marknad · ej aktuell')")
            assert 'Senast känt' in page.locator('#real-overview').inner_text()
            runtime.exchange.last_account=None;runtime.exchange.last_error='Test: kontoåtkomst saknas'
            page.wait_for_function("() => document.querySelector('#overview-warnings').textContent.includes('kontoåtkomst saknas')")
            assert '—' in page.locator('#real-overview').inner_text()
            # A real verified zero remains zero, distinct from a failed request.
            for b in private.balance.values():b['balance']='0'
            private.orders.clear();runtime.exchange.account();runtime.last_fetch=0;runtime.monitor_market()
            page.wait_for_function("() => document.querySelector('#overview-status').textContent.includes('Kraken-konto · aktuellt')")
            assert '0,00' in page.locator('#real-overview').inner_text()
            assert not [c for c in private.calls if c[0]=='AddOrder']
            assert not errors,errors
            browser.close()
        print('Overview browser passed: real-value fixture, open order, agents, unknown basis, disconnect, stale timestamps, zero-vs-missing, mobile, no JS errors. No real exchange calls.')
    finally:
        server.shutdown();server.server_close();runtime.stop();store.close()
