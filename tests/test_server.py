import copy
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from backend.market import MarketError
from backend.server import Runtime, Store, make_server


class FakeMarket:
    def fetch(self):
        return {'BTC-SIM':600000, 'ETH-SIM':25000}


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/'state.sqlite3'
        self.store = Store(self.path)
        self.runtime = Runtime(self.store, FakeMarket(),paper_enabled=True)
        self.server = make_server(self.runtime, 0)
        self.url = 'http://127.0.0.1:'+str(self.server.server_port)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.runtime.stop()
        self.store.close()
        self.tmp.cleanup()

    def request(self, path='/api/state', data=None, key='request-1', extra=None):
        headers = {'X-Jarvis-Client':'dashboard', 'Idempotency-Key':key, 'Content-Type':'application/json'}
        headers.update(extra or {})
        req = Request(self.url+path, data=json.dumps(data).encode() if data is not None else None, headers=headers)
        try:
            with urlopen(req, timeout=5) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            return exc.code, json.load(exc)

    def command(self, name, data=None, key='request-1', extra=None):
        return self.request('/api/commands/'+name, {} if data is None else data, key, extra)

    def test_http_state_step_and_static_dashboard(self):
        self.assertTrue(self.request()[1]['paperOnly'])
        response = self.command('step')
        self.assertEqual(response[0], 200)
        self.assertEqual(response[1]['state']['tick'], 1)
        with urlopen(self.url, timeout=5) as response:
            html = response.read().decode()
            self.assertIn('/dashboard.js', html)
            self.assertNotIn('localStorage.setItem', html)
        self.assertEqual(self.request('/missing')[0], 404)

    def test_persistence_and_exactly_once_deposit_after_restart(self):
        self.assertEqual(self.command('deposit', dict(amount=100), 'deposit-1')[0], 200)
        self.command('deposit', dict(amount=100), 'deposit-1')
        self.assertEqual(self.request()[1]['state']['cash'], 10100)
        self.store.close()
        self.store = Store(self.path)
        self.runtime.store = self.store
        from backend.live import LiveService
        self.runtime.exchange=LiveService(self.store)
        self.assertEqual(self.command('deposit', dict(amount=100), 'deposit-1')[1]['state']['cash'], 10100)
        self.assertEqual(self.command('deposit', dict(amount=101), 'deposit-1')[0], 400)

    def test_failed_command_rolls_back_every_field(self):
        before = self.store.snapshot()
        before.pop('serverTime')  # Response clock is intentionally not persisted state.
        self.assertEqual(self.command('settings', dict(autoPace=30000, marketAuto='bad'))[0], 400)
        actual=self.store.snapshot();actual.pop('serverTime')
        self.assertEqual(actual,before)
        self.assertEqual(self.command('withdraw', dict(amount=20000))[0], 400)
        actual=self.store.snapshot();actual.pop('serverTime')
        self.assertEqual(actual,before)

    def test_concurrent_deposits_serialized(self):
        threads = [threading.Thread(target=lambda n=i: self.runtime.command('deposit', dict(amount=10), str(n))) for i in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(self.store.state['cash'], 10120)
        self.assertEqual(len(self.store.state['ownerMovements']), 12)

    def test_cross_origin_and_dns_rebinding_rejected(self):
        self.assertEqual(self.command('deposit', dict(amount=100), extra={'Origin':'https://evil.example'})[0], 403)
        self.assertEqual(self.request(extra={'Host':'evil.example'})[0], 403)
        self.assertEqual(self.command('deposit', dict(amount=100), extra={'X-Jarvis-Client':''})[0], 403)
        self.assertEqual(self.store.state['cash'], 10000)

    def test_exports_and_protected_approval_endpoints(self):
        self.command('deposit', dict(amount=100))
        for name in ['trades.csv', 'capital.csv', 'journal.jsonl']:
            with urlopen(self.url+'/api/export/'+name,timeout=5) as response:
                body=response.read().decode('utf-8-sig')
                self.assertTrue(body)
                if name=='capital.csv':
                    self.assertIn('DEPOSIT',body)
                if name=='journal.jsonl':
                    rows=[json.loads(row) for row in body.splitlines()]
                    self.assertGreaterEqual(len(rows),2)
        self.assertEqual(self.command('release',dict(amount=1,approved=True),key='unsigned')[0],400)
        self.assertEqual(self.command('approve',key='unsigned-approve')[0],400)
        self.assertNotIn('book',self.request()[1]['state'])

    def test_no_live_or_reset_command(self):
        for name in ['live', 'order', 'reset']:
            self.assertEqual(self.command(name)[0], 400)

    def test_process_lock_prevents_second_writer(self):
        with self.assertRaises(RuntimeError):
            Store(self.path)

    def test_session_http_requires_owner_approval_and_stop_needs_no_new_signature(self):
        from backend.live import LiveService
        from backend.approval import issue
        from test_sessions import SessionMarket,SessionPrivate
        self.runtime.exchange=LiveService(self.store,SessionMarket(),SessionPrivate(),enabled=True)
        self.runtime.exchange.sessions.enabled=True
        data=dict(capitalEUR='100',endPolicy='hold',durationMinutes=120)
        self.assertEqual(self.request('/api/trading-session/plan',data,'bad-origin',{'X-Jarvis-Client':'other'})[0],403)
        code,plan=self.request('/api/trading-session/plan',data,'session-plan')
        self.assertEqual(code,200)
        target=dict(session=plan['id'])
        self.assertEqual(self.request('/api/trading-session/start',target,'unsigned-session')[0],400)
        token=issue(self.store.operator_key,'session-start',subject=plan['fingerprint'])
        code,result=self.request('/api/trading-session/start',target,'signed-session',{'X-Jarvis-Approval':token})
        self.assertEqual((code,result['status']),(200,'RUNNING'))
        self.assertEqual(self.request('/api/trading-session/start',target,'signed-session')[1]['status'],'RUNNING')
        self.assertEqual(self.request('/api/trading-session/stop',target,'stop-session')[1]['status'],'STOPPING')
        self.assertEqual(self.request('/api/trading-session')[1]['session']['status'],'STOPPING')

    def test_network_failure_executes_no_tick_or_fill(self):
        class Offline:
            def fetch(self):
                raise MarketError('offline')
        self.runtime.market = Offline()
        self.assertEqual(self.command('step')[0], 503)
        state = self.request()[1]['state']
        self.assertEqual(state['tick'], 0)
        self.assertEqual(state['orders'], [])
        self.assertEqual(state['marketData']['status'], 'error')
        # Recovery on new data resumes the intended workflow.
        self.runtime.market = FakeMarket()
        self.assertEqual(self.command('step', key='recover')[1]['state']['tick'], 1)

    def test_background_scheduler_without_browser_and_stop_during_fetch(self):
        self.command('start')
        self.runtime.start()
        deadline = time.monotonic()+3
        while self.store.snapshot()['state']['tick'] == 0 and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertGreater(self.store.snapshot()['state']['tick'], 0)
        self.command('stop', key='stop')
        self.runtime.stop()
        # A slow fetch must not bypass a stop received while it was in flight.
        entered, release = threading.Event(), threading.Event()
        class SlowMarket:
            def fetch(self):
                entered.set()
                release.wait(3)
                return FakeMarket().fetch()
        self.runtime.market = SlowMarket()
        self.runtime.last_prices = None
        self.runtime.command('start', {}, 'restart')
        task = threading.Thread(target=lambda: self.runtime.step(only_running=True))
        task.start()
        self.assertTrue(entered.wait(1))
        self.runtime.command('stop', {}, 'stop-again')
        before = copy.deepcopy(self.store.state)
        release.set()
        task.join(3)
        self.assertFalse(task.is_alive())
        self.assertEqual(self.store.state['tick'], before['tick'])
        self.assertFalse(self.store.state['running'])


if __name__ == '__main__':
    unittest.main()
