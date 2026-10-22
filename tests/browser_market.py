"""Live market browser check: fake public feed and private account only."""
import sys
import tempfile
import threading
from pathlib import Path
from datetime import datetime,timezone,timedelta
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
from backend.server import Store,Runtime,make_server
from backend.live import LiveService
from backend.market_feed import MarketFeed
from test_sessions import SessionMarket,SessionPrivate
from test_market_feed import FeedClient

with tempfile.TemporaryDirectory() as tmp:
    store=Store(Path(tmp)/'db');market=SessionMarket();private=SessionPrivate()
    runtime=Runtime(store,market,paper_enabled=False);runtime.exchange=LiveService(store,market,private,enabled=False)
    client=FeedClient();feed=MarketFeed(runtime.exchange.stream,client,enabled=False);runtime.market_feed=feed
    feed.ticker();feed.history('BTC-SIM');feed.history('ETH-SIM');runtime.monitor_market()
    server=make_server(runtime,0);threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto('http://127.0.0.1:'+str(server.server_port));page.locator('#nav-market').click()
            page.wait_for_function("() => document.querySelector('#market-live-stats').textContent.includes('120,00')")
            assert '80,00' in page.locator('#market-live-stats').inner_text()
            assert 'UTC' in page.locator('#market-live-stats').inner_text()
            page.locator('#market-range').select_option('1')
            assert '102,00' in page.locator('#market-range-stats').inner_text()
            assert page.locator('#market-chart rect').count()<=13
            page.locator('#market-range').select_option('24')
            assert page.locator('#market-chart rect').count()>280
            assert '24 h' in page.locator('#market-chart-title').inner_text()
            page.locator('#market-symbol').select_option('ETH-SIM')
            assert 'ETH/EUR' in page.locator('#market-chart-title').inner_text()
            page.locator('#market-symbol').select_option('BTC-SIM')
            page.locator('#market-range').select_option('6')
            with feed.lock:feed.tickers['BTC-SIM']['high24hEUR']='141'
            page.wait_for_function("() => document.querySelector('#market-live-stats').textContent.includes('141,00')")
            page.screenshot(path='/tmp/estman-market-fixture.png',full_page=True)
            page.set_viewport_size({'width':390,'height':844})
            assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
            old=(datetime.now(timezone.utc)-timedelta(seconds=60)).isoformat()
            with feed.lock:feed.tickers['BTC-SIM']['checkedAt']=old;feed.candles['BTC-SIM']['checkedAt']=old
            page.wait_for_function("() => document.querySelector('#market-quality').textContent.includes('gamla')")
            assert 'ej aktuell' in page.locator('#market-live-stats').inner_text()
            page.route('**/api/market-live',lambda route:route.abort())
            page.wait_for_function("() => document.querySelector('#market-quality').textContent.includes('Anslutningen')")
            page.unroute('**/api/market-live')
            feed.ticker();feed.history('BTC-SIM')
            page.wait_for_function("() => document.querySelector('#market-quality').textContent.includes('REST aktuell')")
            assert not [c for c in private.calls if c[0]=='AddOrder']
            assert not errors,errors
            browser.close()
        print('Market browser passed: rolling high/low vs chart period, instrument/range switching, polling updates, stale/disconnected/recovered, mobile, no JS errors. No real exchange calls.')
    finally:
        server.shutdown();server.server_close();runtime.stop();store.close()
