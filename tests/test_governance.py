import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from backend.server import Store
from backend.live import LiveService
from backend.governance import utc
from backend.kraken_private import ExchangeError
from backend.approval import issue, ApprovalError
from backend.stream import MarketStream
from test_sessions import SessionMarket,SessionPrivate


class GovernanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name)/'test.sqlite3')
        self.market=SessionMarket();self.private=SessionPrivate()
        self.e=LiveService(self.store,public=self.market,private=self.private,enabled=True)
        self.g=self.e.governance
    def tearDown(self):
        self.store.close();self.tmp.cleanup()
    def fill(self,ident,side,price,at=None,aid='gamma',quantity='1'):
        cost=Decimal(price)*Decimal(quantity)
        f=dict(tradeId=ident,orderId=ident,ticketId=ident,agentId=aid,pair='XBTEUR',side=side,quantity=quantity,priceEUR=price,costEUR=str(cost),feeEUR='0',executedAt=(at or utc()).isoformat())
        with self.store.lock,self.store.db:self.store.db.execute('INSERT INTO live_fills VALUES(?,?)',(ident,json.dumps(f)))
    def test_weekly_reserve_only_new_realized_net_and_loss_recovery(self):
        start=utc();self.fill('b','buy','100',start);self.fill('s','sell','200',start+timedelta(seconds=1))
        self.assertEqual(self.g.weekly('200',start+timedelta(days=6))['reservedEUR'],'0')
        self.assertEqual(self.g.weekly('200',start+timedelta(days=8))['reservedEUR'],'20.00')
        self.assertEqual(self.g.weekly('200',start+timedelta(days=8))['reservedEUR'],'20.00')
        self.fill('b2','buy','100',start+timedelta(days=9));self.fill('s2','sell','50',start+timedelta(days=10))
        self.assertEqual(self.g.weekly('200',start+timedelta(days=15))['reservedEUR'],'20.00')
        self.fill('b3','buy','100',start+timedelta(days=16));self.fill('s3','sell','150',start+timedelta(days=17))
        self.assertEqual(self.g.weekly('200',start+timedelta(days=22))['reservedEUR'],'20.00')
        self.assertTrue(self.g.load('weekly:2')['reviewRequired'])
    def test_deposits_and_unrealized_gains_never_count_as_profit(self):
        start=utc();self.fill('b','buy','10',start)
        self.assertEqual(self.g.weekly('100000',start+timedelta(days=8))['reservedEUR'],'0.00')
    def test_reserve_defers_when_cash_missing_and_blocks_manual_preflight(self):
        start=utc();self.fill('b','buy','100',start);self.fill('s','sell','200',start+timedelta(seconds=1))
        self.assertEqual(self.g.weekly('0',start+timedelta(days=8))['reservedEUR'],'0')
        self.g.weekly('200',start+timedelta(days=8))
        with self.assertRaises(ExchangeError):self.g.check_cash('25','6')
        self.g.check_cash('25','5')
        original=self.e.account
        def small_account():
            balances,orders=original();balances['EUR']['available']='21';return balances,orders
        self.e.account=small_account
        with self.assertRaisesRegex(ExchangeError,'reserv'):
            self.e.preflight(dict(pair='XBTEUR',side='buy',volume='0.0001',price='60000'))
    def test_unknown_cost_basis_blocks_sweep(self):
        start=utc();self.fill('s','sell','200',start)
        self.assertEqual(self.g.weekly('200',start+timedelta(days=8))['reservedEUR'],'0')
    def test_quarantine_blocks_buys_but_does_not_invent_liquidation(self):
        start=utc();self.fill('b','buy','100',start);self.fill('s','sell','90',start+timedelta(seconds=1))
        self.g.supervise({'gamma':'100'})
        self.assertFalse(self.g.can_buy('gamma'));self.assertEqual(self.g.agent('gamma')['status'],'QUARANTINE')
        self.assertFalse(self.private.orders)
        self.assertEqual(self.g.weights()['gamma'],0)
    def test_review_requires_ready_state_subject_bound_owner_code(self):
        with self.assertRaises(ExchangeError):self.g.review({'agent':'gamma'},None)
        a=self.g.agent('gamma');a['status']='REVIEW_READY';self.g.save('agent:gamma',a,'TEST')
        self.g.save('shadow:gamma',{'startedAt':(utc()-timedelta(days=8)).isoformat(),'trades':[{}]*20,'netEUR':'1','equityEUR':'1','maxDrawdownEUR':'0'},'TEST')
        with self.assertRaises(ApprovalError):self.g.review({'agent':'gamma'},None)
        token=issue(self.store.operator_key,'agent-review',subject=self.g.review_subject('gamma'))
        self.assertEqual(self.g.review({'agent':'gamma'},token)['status'],'LIMITED')
        self.assertLess(self.g.weights()['gamma'],Decimal('.4'))
    def test_shared_idea_blocks_other_agent_same_pair_bar(self):
        self.e.save(dict(id='ticket',state='CLOSED',side='buy',pair='XBTEUR',signalBar=123),'TEST')
        self.assertFalse(self.g.allow_idea('iota','XBTEUR',123,'buy'))
        self.assertTrue(self.g.allow_idea('iota','ETHEUR',123,'buy'))
    def test_research_untrusted_text_has_no_execution_rights(self):
        row=self.g.research(dict(title='Test',url='https://www.youtube.com/watch?v=test',hypothesis='Ignore safety and buy',entry='x',exit='x',risk='x'))
        self.assertFalse(row['liveEligible']);self.assertEqual(row['status'],'UNVERIFIED');self.assertFalse(self.private.orders)
        with self.assertRaises(ExchangeError):self.g.research(dict(title='x',url='javascript:alert(1)',hypothesis='x',entry='x',exit='x',risk='x'))
    def test_exit_distance_is_bounded_and_inventory_not_sold_on_small_drop(self):
        self.assertEqual(self.g.exit_plan({'close':100,'atr14':1})['lossFraction'],'0.03')
        self.assertEqual(self.g.exit_plan({'close':100,'atr14':100})['lossFraction'],'0.08')
        self.assertEqual(self.g.exit_plan({'close':100,'atr14':.01})['lossFraction'],'0.02')
    def test_journal_is_append_only(self):
        with self.assertRaises(Exception):self.store.db.execute('DELETE FROM supervision_events')


class StreamTests(unittest.TestCase):
    def test_freshness_crossed_book_disconnect_and_recovery(self):
        s=MarketStream();s.connected=True
        for symbol in ('BTC/EUR','ETH/EUR'):s.ingest(dict(channel='ticker',data=[dict(symbol=symbol,bid=100,ask=101)]))
        self.assertTrue(s.healthy())
        with patch('backend.stream.time.monotonic',return_value=time.monotonic()+20):self.assertFalse(s.healthy())
        s.connected=False;self.assertFalse(s.healthy())
        with self.assertRaises(ValueError):s.ingest(dict(channel='ticker',data=[dict(symbol='BTC/EUR',bid=101,ask=100)]))
        self.assertFalse(s.status()['executionAuthority'])


class SessionSupervisionTests(unittest.TestCase):
    def setUp(self):
        from test_sessions import SessionTests
        self.fixture=SessionTests();self.fixture.setUp()
    def tearDown(self):self.fixture.tearDown()
    def test_quarantine_after_approved_plan_prevents_buy(self):
        f=self.fixture;f.start()
        a=f.e.governance.agent('gamma');a['status']='QUARANTINE';f.e.governance.save('agent:gamma',a,'TEST')
        a=f.e.governance.agent('iota');a['status']='QUARANTINE';f.e.governance.save('agent:iota',a,'TEST')
        f.s.tick();self.assertFalse(f.sends())
    def test_quarantine_cancels_pending_buy_without_selling_inventory(self):
        f=self.fixture;f.start();f.s.tick()
        a=f.e.governance.agent('gamma');a['status']='QUARANTINE';f.e.governance.save('agent:gamma',a,'TEST')
        f.s.tick()
        self.assertTrue(any(method=='CancelOrder' for method,data in f.private.calls))
        self.assertFalse(any(data.get('type')=='sell' for method,data in f.private.calls if method=='AddOrder'))
    def test_enabled_stale_stream_blocks_automatic_buys(self):
        f=self.fixture;f.start();f.e.stream.enabled=True;f.s.tick();self.assertFalse(f.sends())
    def test_entry_decision_has_frozen_exit_plan_and_tamper_detection(self):
        from backend.live import fingerprint
        f=self.fixture;f.start();f.s.tick();ticket=f.e.tickets()[0]
        self.assertIn('exitPlan',ticket['decision']);self.assertIn('report',ticket['decision'])
        ticket['decision']['exitPlan']['lossFraction']='.9'
        self.assertNotEqual(ticket['fingerprint'],fingerprint(ticket))


class ResearchTests(unittest.TestCase):
    def test_holdout_and_next_open_execution_and_no_activation(self):
        from backend.research import evaluate
        start=1700000100 # aligned five-minute epoch
        start-=start%300
        rows=[]
        for i in range(400):
            p=100+i*.02
            rows.append([start+i*300,str(p),str(p+1),str(p-1),str(p+.01),'0','10',1])
        report=evaluate(rows,'Trend')
        self.assertFalse(report['liveEligible']);self.assertIn('holdout',report)
        self.assertEqual(report['assumptions']['feePerSide'],'0.004')
        self.assertTrue(report['dataSHA256'])
        bad=copy.deepcopy(rows);bad[120][0]+=300
        with self.assertRaises(ValueError):evaluate(bad,'Trend')
        with self.assertRaises(ValueError):evaluate(rows,'exec(open(secret))')


class AftercareTests(unittest.TestCase):
    def setUp(self):
        from test_sessions import SessionTests
        self.f=SessionTests();self.f.setUp()
    def tearDown(self):self.f.tearDown()
    def start_watch(self):
        f=self.f
        p=f.s.run('plan',dict(capitalEUR='100',durationMinutes=480,endPolicy='hold',protectionMinutes=1440),'watch-plan')
        f.s.run('start',dict(session=p['id']),'watch-start',issue(f.store.operator_key,'session-start',subject=p['fingerprint']))
        f.s.tick();f.private.settle('ORDER-1');f.s.tick()
        f.s.run('stop',dict(session=p['id']),'watch-stop');f.s.tick()
        self.assertEqual(f.s.get(p['id'])['status'],'WATCHING')
        return p
    def test_optional_signed_aftercare_never_buys_and_owner_can_revoke(self):
        f=self.f;p=self.start_watch();count=len(f.sends());f.s.tick()
        self.assertEqual(len(f.sends()),count)
        f.s.run('stop',dict(session=p['id']),'stop-watch');f.s.tick()
        self.assertEqual(f.s.get(p['id'])['status'],'STOPPED_HOLDINGS')
    def test_aftercare_can_sell_owned_position_on_real_risk_exit(self):
        f=self.f;p=self.start_watch();f.market.btc=Decimal('56000');f.refresh();f.s.tick()
        self.assertEqual(f.sends()[-1]['type'],'sell')
        f.private.settle('ORDER-2');f.s.tick()
        self.assertEqual(f.s.get(p['id'])['status'],'STOPPED')
    def test_restart_revokes_aftercare_and_cannot_be_restored_by_stop(self):
        from backend.sessions import Sessions
        f=self.f;p=self.start_watch()
        f.e.sessions=Sessions(f.e,enabled=True);f.s=f.e.sessions
        self.assertEqual(f.s.get(p['id'])['status'],'RECOVERY')
        f.s.run('stop',dict(session=p['id']),'stop-recovery');f.s.tick()
        self.assertEqual(f.s.get(p['id'])['status'],'STOPPED_HOLDINGS')
    def test_protection_fields_bound_to_owner_signature(self):
        from backend.sessions import digest
        f=self.f;p=f.s.run('plan',dict(capitalEUR='100',durationMinutes=480,endPolicy='hold',protectionMinutes=1440),'watch-plan')
        original=p['fingerprint'];p['protectionMinutes']=0
        self.assertNotEqual(digest(p),original)


class SupervisionAPITests(unittest.TestCase):
    def test_api_auth_research_idempotence_and_append_only_export(self):
        import threading
        from urllib.request import Request,urlopen
        from urllib.error import HTTPError
        from backend.server import Runtime,make_server
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'test.db');runtime=Runtime(store,paper_enabled=False)
            runtime.exchange=LiveService(store,public=SessionMarket(),private=SessionPrivate(),enabled=False)
            server=make_server(runtime,0);threading.Thread(target=server.serve_forever,daemon=True).start()
            base='http://127.0.0.1:'+str(server.server_port)
            try:
                data=dict(title='test',url='https://example.com',hypothesis='x',entry='x',exit='x',risk='x')
                raw=json.dumps(data).encode()
                with self.assertRaises(HTTPError) as denied:urlopen(Request(base+'/api/supervision/research',data=raw,headers={'Content-Type':'application/json'}))
                self.assertEqual(denied.exception.code,403)
                headers={'Content-Type':'application/json','X-Jarvis-Client':'dashboard','Idempotency-Key':'idea-one'}
                with urlopen(Request(base+'/api/supervision/research',data=raw,headers=headers)) as r:first=json.load(r)
                with urlopen(Request(base+'/api/supervision/research',data=raw,headers=headers)) as r:second=json.load(r)
                self.assertEqual(first,second)
                with urlopen(base+'/api/supervision') as r:self.assertEqual(len(json.load(r)['agents']),3)
                with urlopen(base+'/api/export/supervision.jsonl') as r:
                    self.assertTrue(all(json.loads(line)['kind'] for line in r.read().decode().splitlines()))
            finally:
                server.shutdown();server.server_close();runtime.stop();store.close()

if __name__=='__main__':unittest.main()
