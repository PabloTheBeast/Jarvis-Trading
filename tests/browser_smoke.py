"""Optional real-browser integration test: python tests/browser_smoke.py.

Requires Playwright and Chromium. Unit/API tests need only Python stdlib.
"""
import os
import sys
import tempfile
import threading
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
from backend.server import Runtime, Store, make_server


class Market:
    metadata = dict(fxDate='2026-10-21', eurSek=11.1)

    def fetch(self):
        return {'BTC-SIM':600000, 'ETH-SIM':25000}


with tempfile.TemporaryDirectory() as tmp:
    store = Store(Path(tmp)/'paper.sqlite3')
    def seed_profit(e):
        e.s['prices']['BTC-SIM']=[600000]
        e.s['paperBroker']=dict(spreadBps=0,slippageBps=0)
        e.submit('gamma','BTC-SIM','BUY',300)
        e.s['prices']['BTC-SIM'].append(900000)
        e.submit('gamma','BTC-SIM','SELL',1000)
        e.sweep('test-profit')
    store.change('test-profit',seed_profit)
    runtime = Runtime(store, Market())
    server = make_server(runtime, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    runtime.start()
    errors = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH', '/usr/bin/chromium'), headless=True)
            page = browser.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto('http://127.0.0.1:'+str(server.server_port))
            page.wait_for_function("()=>document.querySelector('#equity').textContent.includes('10')")
            page.click('#step')
            page.wait_for_function("()=>document.querySelector('#tick').textContent==='Steg 1'")
            page.click('[data-tab=\"agents\"]')
            page.fill('#deposit-amount', '500')
            page.once('dialog', lambda dialog: dialog.accept())
            page.click('#deposit-btn')
            page.wait_for_function("()=>document.querySelector('#owner-capital').textContent.includes('10 500')")
            page.click('[data-tab="portfolio"]')
            before=store.state['vault']
            result=subprocess.run([sys.executable,'-m','backend.ops','--db',str(Path(tmp)/'paper.sqlite3'),
                                   'approve','release','--amount','1'],capture_output=True,text=True,check=True)
            answers=iter(['1',None,result.stdout.strip()])
            def approve_dialog(dialog):
                value=next(answers)
                if value is None:
                    dialog.accept()
                else:
                    dialog.accept(value)
            page.on('dialog',approve_dialog)
            page.click('#release')
            page.wait_for_function("()=>document.querySelector('#vault').textContent.includes('28')")
            page.remove_listener('dialog',approve_dialog)
            assert abs(store.state['vault']-(before-1))<1e-8
            page.click('[data-tab="history"]')
            for button,filename in [('#export-capital','capital.csv'),('#export-journal','journal.jsonl')]:
                with page.expect_download() as event:
                    page.click(button)
                assert event.value.suggested_filename==filename
            page.click('[data-tab="risk"]')
            page.click('#testpause')
            page.wait_for_function("()=>document.querySelector('#recovery').textContent.includes('GLOBAL PAUS')")
            assert page.is_disabled('#approve')
            page.click('[data-tab=\"history\"]')
            page.wait_for_function("()=>document.querySelector('#order-audit').textContent.includes('Avstämning OK')")
            page.click('[data-tab=\"overview\"]')
            page.click('#run')
            page.wait_for_function("()=>document.querySelector('#status').textContent==='SERVER KÖR'")
            tick = store.snapshot()['state']['tick']
            page.close()
            # Demonstrate background trading with no page connected.
            import time
            deadline = time.monotonic()+8
            while store.snapshot()['state']['tick'] <= tick and time.monotonic() < deadline:
                time.sleep(.1)
            assert store.snapshot()['state']['tick'] > tick
            page = browser.new_page(viewport=dict(width=390, height=844))
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto('http://127.0.0.1:'+str(server.server_port))
            page.wait_for_function("()=>document.querySelector('#status').textContent==='SERVER KÖR'")
            page.click('#stop')
            page.wait_for_function("()=>document.querySelector('#status').textContent==='SERVER PAUSAD'")
            assert not store.snapshot()['state']['running']
            assert page.locator('#agent-compact .agent-chip').count() == 10
            assert not errors, errors
            browser.close()
        print('Browser smoke passed: rendering, tabs, capital, pause, headless runtime, reconnect, emergency stop, mobile.')
    finally:
        server.shutdown()
        server.server_close()
        runtime.stop()
        store.close()
