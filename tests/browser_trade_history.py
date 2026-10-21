"""Optional browser check: real-history UI with explicitly mocked exchange fills."""
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
from backend.server import Store,Runtime,make_server
from test_analysis_live import Public,Private

with tempfile.TemporaryDirectory() as tmp:
    store=Store(Path(tmp)/'paper.db')
    store.state['orderHistory'].append(dict(agent='gamma',id='BTC-SIM',orderId='FAKE-OLD-TRADE',side='KÖP',qty=1,amount=10000,fee=0,day=1))
    runtime=Runtime(store,Public());runtime.exchange.private=Private()
    runtime.exchange.private.balance['ZEUR']['balance']='0'
    for ident,side,when,price,cost in [('BUY-ID','buy','2026-10-21T08:00:00+00:00','60000','6'),('SELL-ID','sell','2026-10-21T10:00:00+00:00','62000','6.2')]:
        value=dict(tradeId=ident,ticketId=side,orderId=side,pair='XBTEUR',side=side,quantity='0.0001',priceEUR=price,costEUR=cost,feeEUR='0.02',executedAt=when,agentId=None,actor='Manuell')
        with store.db:store.db.execute('INSERT INTO live_fills VALUES(?,?)',(ident,json.dumps(value)))
    server=make_server(runtime,0);threading.Thread(target=server.serve_forever,daemon=True).start();runtime.start()
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto('http://127.0.0.1:'+str(server.server_port))
            page.wait_for_function("() => document.querySelector('#real-overview').children.length===4")
            assert not page.locator('#equity').is_visible()
            assert not page.locator('#nav-portfolio').is_visible()
            assert not page.locator('#run').is_visible()
            assert 'paper' not in page.locator('body').inner_text().lower()
            page.locator('#nav-history').click()
            page.wait_for_function("() => document.querySelectorAll('#real-executions tr').length===2")
            text=page.locator('#real-roundtrips').inner_text()
            assert '2026-10-21 10:00:00' in text,text
            assert '2026-10-21 12:00:00' in text,text
            assert 'Manuell' in text and '0,16' in text
            assert 'FAKE-OLD-TRADE' not in page.locator('body').inner_text()
            assert 'paper' not in page.locator('body').inner_text().lower()
            if os.environ.get('JARVIS_TEST_SCREENSHOT'):page.screenshot(path=os.environ['JARVIS_TEST_SCREENSHOT'],full_page=True)
            page.reload();page.locator('#nav-history').click()
            page.wait_for_function("() => document.querySelectorAll('#real-executions tr').length===2")
            page.set_viewport_size({'width':390,'height':844})
            assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
            assert not errors,errors
            assert store.state['tick']==0
            assert len(store.state['orderHistory'])==1 # Archive preserved, never shown as real.
            assert all(m in ('BalanceEx','OpenOrders') for m,_ in runtime.exchange.private.calls)
            browser.close()
        print('Real history browser passed: no paper UI, actual-fill fixture dates in Stockholm, FIFO net after fees, manual labels, reload, mobile, no JS errors. All account/trade data mocked.')
    finally:
        server.shutdown();server.server_close();runtime.stop();store.close()
