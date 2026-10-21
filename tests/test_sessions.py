import copy
import tempfile
import time
import threading
import unittest
from datetime import datetime,timezone,timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from backend.approval import issue,ApprovalError
from backend.live import LiveService
from backend.sessions import Sessions,digest
from backend.server import Store
from backend.kraken_private import ExchangeError,ExchangeUnknown
from test_analysis_live import Public,Private


class SessionMarket(Public):
    def __init__(self):
        super().__init__();self.side='BUY';self.btc=Decimal('60000');self.failure=False
    def fetch(self):
        if self.failure:raise ExchangeError('offline')
        prices=super().fetch()
        self.metadata['orderBooks']['BTC-SIM']=dict(bids=[[float(self.btc*10-10),2]],asks=[[float(self.btc*10+10),2]])
        closed=int(time.time()//300)*300
        self.metadata['analysis']={symbol:dict(status='ready',barId=closed-300,closedAt=datetime.fromtimestamp(closed,timezone.utc).isoformat(),
            close=float(self.btc if symbol=='BTC-SIM' else 2500),signals={'Trend':self.side,'Momentum':self.side,'Mean reversion':None},reasons={},candles=[]) for symbol in prices}
        prices['BTC-SIM']=float(self.btc*10)
        return prices


class SessionPrivate(Private):
    def call(self,method,parameters=None):
        data=dict(parameters or {})
        if method=='OpenOrders':
            self.calls.append((method,data));return dict(open=copy.deepcopy({i:o for i,o in self.orders.items() if o['status'] in ('open','pending')}))
        if method=='AddOrder' and data.get('validate')=='false':
            self.calls.append((method,data))
            if self.failure=='reject':raise ExchangeError('rejected')
            ident='ORDER-'+str(len(self.orders)+1)
            self.orders[ident]=dict(status='open',cl_ord_id=data['cl_ord_id'],vol=data['volume'],vol_exec='0',cost='0',fee='0',trades=[],
                descr=dict(pair=data['pair'],type=data['type'],ordertype='limit',price=data['price']))
            if self.failure=='timeout':raise ExchangeUnknown('timeout')
            return dict(txid=[ident])
        return super().call(method,data)

    def settle(self,ident):
        o=self.orders[ident];qty=Decimal(o['vol']);price=Decimal(o['descr']['price']);cost=qty*price;fee=cost*Decimal('.0015')
        asset='XXBT' if o['descr']['pair']=='XBTEUR' else 'XETH';side=o['descr']['type'];fill='FILL-'+ident
        o.update(status='closed',vol_exec=str(qty),cost=str(cost),fee=str(fee),trades=[fill])
        self.trades[fill]=dict(ordertxid=ident,pair=o['descr']['pair'],type=side,vol=str(qty),price=str(price),cost=str(cost),fee=str(fee),time=str(time.time()))
        self.balance[asset]['balance']=str(Decimal(self.balance[asset]['balance'])+(qty if side=='buy' else -qty))
        self.balance['ZEUR']['balance']=str(Decimal(self.balance['ZEUR']['balance'])+(-cost-fee if side=='buy' else cost-fee))


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name)/'paper.db')
        self.market=SessionMarket();self.private=SessionPrivate()
        self.e=LiveService(self.store,self.market,self.private,enabled=True)
        self.s=self.e.sessions;self.s.enabled=True;self.refresh()

    def tearDown(self):self.store.close();self.tmp.cleanup()

    def refresh(self):
        prices=self.market.fetch()
        def observe(engine):engine.real_monitoring=True;engine.observe(prices,self.market.metadata)
        self.store.change('observe',observe)

    def plan(self,policy='assess'):
        return self.s.run('plan',dict(capitalEUR='100',endPolicy=policy,durationMinutes=480),'plan')

    def start(self,policy='assess'):
        p=self.plan(policy)
        return self.s.run('start',dict(session=p['id']),'start',issue(self.store.operator_key,'session-start',subject=p['fingerprint']))

    def sends(self):return [data for method,data in self.private.calls if method=='AddOrder' and data.get('validate')=='false']

    def stop(self,p):return self.s.run('stop',dict(session=p['id']),'stop')

    def test_plan_budget_no_order_and_signed_start_gates(self):
        self.private.balance['ZEUR']['balance']='100'
        p=self.plan()
        self.assertEqual(Decimal(p['newCashEUR']),Decimal('39.6'))
        self.assertEqual(sum(Decimal(v) for v in p['budgets'].values()),Decimal('39.6'))
        self.assertFalse(self.sends())
        with self.assertRaises(ApprovalError):self.s.run('start',dict(session=p['id']),'unsigned')
        self.e.monitor_only=True
        with self.assertRaises(ExchangeError):self.s.run('start',dict(session=p['id']),'blocked',issue(self.store.operator_key,'session-start',subject=p['fingerprint']))
        self.assertFalse(self.sends())

    def test_automatic_orders_attributed_to_agent_after_one_session_approval(self):
        p=self.start();self.s.tick()
        self.assertEqual(len(self.sends()),1)
        self.assertEqual(self.sends()[0]['validate'],'false')
        self.private.settle('ORDER-1');self.s.tick()
        fills=self.s.fills()
        self.assertEqual(fills[0]['agentId'],'gamma')
        self.assertEqual(fills[0]['sessionId'],p['id'])
        self.assertFalse(self.store.state['orders'])
        self.assertEqual(self.store.state['tick'],0)
        if 'ORDER-2' in self.private.orders:self.private.settle('ORDER-2')
        self.s.tick();self.s.tick()
        self.assertEqual(len(self.sends()),2) # Gamma + Iota, no duplicate per bar.

    def test_stop_revokes_buy_before_network_preflight_finishes(self):
        p=self.start();original=self.e.preflight
        def race(params):
            result=original(params)
            self.stop(p)
            return result
        self.e.preflight=race;self.s.tick()
        self.assertFalse(self.sends())
        self.assertEqual(self.s.get(p['id'])['status'],'STOPPING')
        self.s.tick();self.assertEqual(self.s.get(p['id'])['status'],'STOPPED')

    def test_stop_cancels_unfilled_order_before_reporting_stopped(self):
        p=self.start();self.s.tick();self.stop(p)
        self.s.tick()
        self.assertEqual(self.s.get(p['id'])['status'],'STOPPING')
        self.assertTrue(any(m=='CancelOrder' for m,_ in self.private.calls))
        self.s.tick();self.assertEqual(self.s.get(p['id'])['status'],'STOPPED')
        self.assertEqual(len(self.sends()),1)

    def test_hold_policy_never_liquidates_on_end(self):
        p=self.start('hold');self.s.tick();self.private.settle('ORDER-1');self.stop(p);self.s.tick()
        session=self.s.get(p['id'])
        self.assertEqual(session['status'],'STOPPED_HOLDINGS')
        self.assertTrue(all(d['decision']=='HOLD' for d in session['endDecisions']))
        self.assertEqual(len(self.sends()),1)

    def test_chef_sell_at_end_only_uses_owned_quantity_and_records_roundtrip(self):
        p=self.start();self.s.tick();self.private.settle('ORDER-1')
        self.market.side='SELL';self.refresh();self.stop(p);self.s.tick()
        self.assertEqual(len(self.sends()),2)
        self.assertEqual(self.sends()[-1]['type'],'sell')
        self.assertLessEqual(Decimal(self.sends()[-1]['volume']),Decimal(self.sends()[0]['volume']))
        self.private.settle('ORDER-2');self.s.tick()
        self.assertIn(self.s.get(p['id'])['status'],('STOPPED','STOPPED_HOLDINGS'))
        closed=self.e.status()['tradeHistory']['closed']
        self.assertTrue(closed)
        self.assertEqual(closed[0]['agentId'],'gamma')

    def test_external_deposit_pauses_instead_of_increasing_budget(self):
        p=self.start();self.private.balance['ZEUR']['balance']='1100';self.s.tick()
        self.assertEqual(self.s.get(p['id'])['status'],'PAUSED');self.assertFalse(self.sends())

    def test_real_drawdown_pauses_without_fake_portfolio_inputs(self):
        self.private.balance['XXBT']['balance']='0.001'
        p=self.start();self.market.btc=Decimal('40000');self.s.tick()
        self.assertEqual(self.s.get(p['id'])['status'],'PAUSED');self.assertFalse(self.sends())

    def test_unknown_send_pauses_and_is_never_resent(self):
        p=self.start();self.private.failure='timeout';self.s.tick();self.s.tick();self.s.tick()
        self.assertEqual(self.s.get(p['id'])['status'],'PAUSED');self.assertEqual(len(self.sends()),1)

    def test_restart_requires_new_owner_consent_and_cannot_auto_resume(self):
        p=self.start();self.s.tick()
        restored=Sessions(self.e,enabled=True)
        self.assertEqual(restored.get(p['id'])['status'],'RECOVERY')
        restored.tick();self.assertEqual(len(self.sends()),1)
        with self.assertRaises(ExchangeError):restored.run('start',dict(session=p['id']),'resume',issue(self.store.operator_key,'session-start',subject=p['fingerprint']))

    def test_zero_balance_can_preview_plan_but_not_start(self):
        self.private.balance['ZEUR']['balance']='0';p=self.plan()
        self.assertEqual(Decimal(p['newCashEUR']),0)
        with self.assertRaises(ExchangeError):self.s.run('start',dict(session=p['id']),'start',issue(self.store.operator_key,'session-start',subject=p['fingerprint']))

    def test_manual_order_paths_blocked_during_agent_session(self):
        self.start()
        with self.assertRaises(ExchangeError):self.e.run('preview',dict(pair='XBTEUR',side='buy',volume='0.0001',price='60000'),'manual')

    def test_reused_start_key_idempotent_and_no_duplicate_approval(self):
        p=self.start();self.assertEqual(self.s.run('start',dict(session=p['id']),'start')['status'],'RUNNING')
        with self.assertRaises(ExchangeError):self.s.run('plan',dict(capitalEUR='100',endPolicy='assess',durationMinutes=480),'another-plan')

    def test_position_stop_loss_can_sell_without_a_matching_strategy_signal(self):
        self.start();self.s.tick();self.private.settle('ORDER-1')
        self.market.btc=Decimal('56400');self.refresh();self.s.tick()
        self.assertEqual(self.sends()[-1]['type'],'sell')

    def test_expiry_stops_without_new_buys(self):
        p=self.start()
        with patch('backend.sessions.now',return_value=datetime.fromisoformat(p['expiresAt'])+timedelta(seconds=1)):self.s.tick()
        self.assertEqual(self.s.get(p['id'])['status'],'STOPPED');self.assertFalse(self.sends())

    def test_no_fresh_quotes_on_end_keeps_positions(self):
        p=self.start();self.s.tick();self.private.settle('ORDER-1');self.stop(p)
        self.market.failure=True;self.s.tick()
        self.assertEqual(self.s.get(p['id'])['status'],'STOPPED_HOLDINGS');self.assertEqual(len(self.sends()),1)

    def test_cancel_plan_during_start_checks_revokes_consent(self):
        p=self.plan();original=self.e.account
        def cancel_during_check():
            value=original();self.s.run('stop',dict(session=p['id']),'cancel-plan');return value
        self.e.account=cancel_during_check
        with self.assertRaises(ExchangeError):self.s.run('start',dict(session=p['id']),'start',issue(self.store.operator_key,'session-start',subject=p['fingerprint']))
        self.assertEqual(self.s.get(p['id'])['status'],'CANCELLED');self.assertFalse(self.sends())

    def test_archived_fake_risk_does_not_allocate_or_pause_real_capital(self):
        self.store.state['halt']=True;self.store.state['agentHalt']['gamma']=True
        self.refresh();self.start();self.s.tick()
        self.assertEqual(len(self.sends()),1)

    def test_conflicting_agent_signals_do_not_trade(self):
        self.market.side=None;self.refresh();self.start();self.s.tick()
        self.assertFalse(self.sends())

    def test_stop_does_not_wait_for_inflight_worker_network_lock(self):
        p=self.start();entered=threading.Event();release=threading.Event();original=self.e.preflight
        def slow(params):
            result=original(params);entered.set();release.wait(3);return result
        self.e.preflight=slow
        thread=threading.Thread(target=self.s.tick);thread.start()
        try:
            self.assertTrue(entered.wait(2));started=time.monotonic();self.stop(p)
            self.assertLess(time.monotonic()-started,.5)
        finally:release.set();thread.join(3)
        self.assertFalse(self.sends())

    def test_server_shutdown_revokes_inflight_buy_and_end_sale_authority(self):
        p=self.start();original=self.e.preflight
        def shutdown(params):
            result=original(params);self.s.shutdown();return result
        self.e.preflight=shutdown;self.s.tick()
        self.assertFalse(self.sends())
        self.assertEqual(self.s.get(p['id'])['status'],'RECOVERY')

    def test_repeated_stop_does_not_extend_exit_sale_permission(self):
        p=self.start();self.stop(p);first=self.s.get(p['id'])['stopRequestedAt']
        with patch('backend.sessions.now',return_value=datetime.fromisoformat(first)+timedelta(minutes=10)):
            self.s.run('stop',dict(session=p['id']),'second-stop')
        self.assertEqual(self.s.get(p['id'])['stopRequestedAt'],first)

    def test_owner_code_is_bound_to_exact_agent_budgets(self):
        p=self.plan();token=issue(self.store.operator_key,'session-start',subject=p['fingerprint'])
        changed=self.s.get(p['id']);changed['budgets']['gamma']='999';changed['fingerprint']=digest(changed);self.s.save(changed,'test-tamper')
        with self.assertRaises(ApprovalError):self.s.run('start',dict(session=p['id']),'tampered-start',token)
        self.assertFalse(self.sends())

    def test_bar_expiry_before_send_waits_for_new_bar_without_reusing_intent(self):
        p=self.start();original=self.e.preflight
        def expire(params):
            result=original(params)
            for a in self.store.state['marketAnalysis'].values():a['closedAt']=(datetime.now(timezone.utc)-timedelta(seconds=301)).isoformat()
            return result
        self.e.preflight=expire;self.s.tick()
        self.assertFalse(self.sends())
        self.assertEqual(self.s.get(p['id'])['status'],'RUNNING')
        self.stop(p);self.s.tick()
        ticket=next(t for t in self.e.tickets() if t.get('sessionId')==p['id'])
        with self.assertRaises(ExchangeError):self.e.run('submit',dict(ticket=ticket['id']),'manual-bypass')


if __name__=='__main__':unittest.main()
