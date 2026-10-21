"""Optional Chromium smoke for read-only reports and opt-in desktop alerts.

Uses fake market and account data; never sends exchange requests.
"""
import copy
import os
import sys
import tempfile
import threading
import time
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
from backend.server import Store,Runtime,make_server
from test_analysis_live import Public,Private,analyse,candles


class Market(Public):
    signals={'Trend':None,'Momentum':None,'Mean reversion':None}

    def fetch(self):
        prices=super().fetch()
        end=int(time.time()//300)*300
        analysis=analyse(candles(start=end-90*300),datetime.now(timezone.utc))
        analysis['signals']=dict(self.signals)
        self.metadata['analysis']={s:copy.deepcopy(analysis) for s in prices}
        return prices


with tempfile.TemporaryDirectory() as tmp:
    store=Store(Path(tmp)/'paper.db');market=Market()
    with patch.dict(os.environ,{'JARVIS_MONITOR_ONLY':'1'}):runtime=Runtime(store,market)
    runtime.exchange.private=Private()
    server=make_server(runtime,0)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    runtime.start()
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':1280,'height':900});errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.add_init_script("""window.sentAlerts=[];window.Notification=class {
              static permission='granted';static requestPermission(){return Promise.resolve('granted');}
              constructor(title,options){window.sentAlerts.push({title,...options});}
            };""")
            page.goto('http://127.0.0.1:'+str(server.server_port))
            page.locator('#nav-decisions').click()
            page.wait_for_function("() => document.querySelectorAll('#agent-report-rows tr').length===5")
            assert 'Avvakta' in page.locator('#chef-signals').inner_text()
            page.locator('#enable-alerts').click()
            page.wait_for_function("() => document.querySelector('#enable-alerts').textContent.includes('aktiverade')")
            market.signals={'Trend':'BUY','Momentum':'BUY','Mean reversion':None}
            runtime.last_fetch=0
            runtime.monitor_market()
            page.wait_for_function("() => window.sentAlerts.length===1")
            assert 'Möjligt köpläge' in page.locator('#chef-signals').inner_text()
            assert 'möjligt köpläge' in page.locator('#crypto-notices').inner_text()
            if os.environ.get('JARVIS_TEST_SCREENSHOT'):
                page.screenshot(path=os.environ['JARVIS_TEST_SCREENSHOT'],full_page=True)
            runtime.last_fetch=0;runtime.monitor_market()
            page.wait_for_timeout(2200)
            assert page.evaluate('window.sentAlerts.length')==1
            page.evaluate('() => {app.serverTime+=600000;renderDecisions();}')
            assert 'Avvakta · ingen färsk bedömning' in page.locator('#chef-signals').inner_text()
            assert 'Historisk signal' in page.locator('#crypto-notices').inner_text()
            page.reload();page.locator('#nav-decisions').click()
            page.wait_for_function("() => document.querySelectorAll('#agent-report-rows tr').length===5")
            assert page.evaluate('window.sentAlerts.length')==0 # No replay on reload.
            assert 'möjligt köpläge' in page.locator('#crypto-notices').inner_text()
            page.set_viewport_size({'width':390,'height':844})
            assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
            assert not errors,errors
            assert store.state['tick']==0 and not store.state['orders']
            assert all(method in ('BalanceEx','OpenOrders') for method,_ in runtime.exchange.private.calls)
            browser.close()
        print('Advisory browser passed: five reports, CHEF consensus, opt-in desktop alert, deduplication, persisted history, no replay, mobile, no orders. All market/account data mocked.')
    finally:
        server.shutdown();server.server_close();runtime.stop();store.close()
