import copy
import json
import tempfile
import unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from decimal import Decimal

from backend.server import Store,Runtime
from backend.live import LiveService
from backend.overview import snapshot
from test_sessions import SessionMarket,SessionPrivate


class OverviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(Path(self.temp.name)/'state.db')
        self.market=SessionMarket();self.private=SessionPrivate()
        self.runtime=Runtime(self.store,self.market,paper_enabled=False)
        self.runtime.exchange=LiveService(self.store,self.market,self.private,enabled=False)
        self.e=self.runtime.exchange;self.runtime.monitor_market()
    def tearDown(self):
        self.runtime.stop();self.store.close();self.temp.cleanup()
    def check(self):self.e.inspect();return snapshot(self.runtime)
    def test_missing_account_is_unknown_not_zero_and_makes_no_requests(self):
        before=len(self.private.calls);data=snapshot(self.runtime)
        self.assertIsNone(data['totals']['accountValueEUR']);self.assertIsNone(data['totals']['availableEUR'])
        self.assertEqual(len(self.private.calls),before);self.assertFalse(data['freshness']['account']['fresh'])
        self.assertEqual(len(data['agents']),3);self.assertTrue(data['warnings'])
    def test_zero_account_is_verified_zero_and_no_deposit_is_profit(self):
        for b in self.private.balance.values():b['balance']='0'
        data=self.check()
        self.assertEqual(Decimal(data['totals']['accountValueEUR']),0)
        self.assertTrue(data['freshness']['account']['fresh'])
        self.private.balance['ZEUR']['balance']='100'
        data=self.check()
        self.assertEqual(Decimal(data['totals']['accountValueEUR']),100)
        self.assertEqual(data['totals']['realizedNetEUR'],'0')
        self.assertEqual(data['totals']['todayRealizedNetEUR'],'0')
    def test_account_valuation_uses_actual_holdings_and_unknown_basis_is_not_invented(self):
        self.private.balance['ZEUR']['balance']='100'
        self.private.balance['XXBT']['balance']='.001'
        data=self.check();btc=next(h for h in data['holdings'] if h['asset']=='BTC')
        self.assertFalse(btc['basisKnown']);self.assertIsNone(btc['unrealizedEUR'])
        self.assertGreater(Decimal(data['totals']['accountValueEUR']),100)
        self.assertEqual(Decimal(btc['quantity']),Decimal('.001'))
    def test_stale_or_future_times_never_mark_data_current(self):
        self.check();later=datetime.now(timezone.utc)+timedelta(seconds=100)
        data=snapshot(self.runtime,later)
        self.assertFalse(data['freshness']['account']['fresh']);self.assertFalse(data['freshness']['market']['fresh'])
        self.assertIsNotNone(data['totals']['accountValueEUR'])
        data=snapshot(self.runtime,datetime.now(timezone.utc)-timedelta(days=1))
        self.assertFalse(data['freshness']['account']['fresh']);self.assertFalse(data['freshness']['market']['fresh'])
    def test_missing_one_instrument_quote_makes_nonzero_position_total_unknown(self):
        self.private.balance['XXBT']['balance']='.001';self.check()
        del self.runtime.last_metadata['orderBooks']['BTC-SIM']
        data=snapshot(self.runtime)
        self.assertIsNone(data['totals']['accountValueEUR']);self.assertFalse(data['freshness']['market']['fresh'])
        self.assertIsNone(data['holdings'][0]['valueEUR'])
    def test_cash_reserve_is_separate_from_signed_plan(self):
        self.private.balance['ZEUR']['balance']='100';self.check()
        reserve=self.e.governance.load('savings');reserve['reservedEUR']='20'
        self.e.governance.save('savings',reserve,'TEST')
        data=snapshot(self.runtime)
        self.assertEqual(data['totals']['spendableEUR'],'80')
        self.assertEqual(data['totals']['reservedEUR'],'20')
        self.assertIsNone(data['trading']['session'])
    def test_sanitized_open_orders_and_pending_unknowns_are_not_fills(self):
        self.private.orders['O-1']=dict(status='open',vol='.0001',vol_exec='0',descr=dict(pair='XBTEUR',type='buy',price='60000',ordertype='limit'),secret='must-not-leak')
        self.e.save(dict(id='LOCAL',state='UNKNOWN',pair='XBTEUR',side='buy',volume='.0001',price='60000'),'TEST')
        self.e.account();data=snapshot(self.runtime)
        self.assertEqual(data['openOrderCount'],1);self.assertEqual(len(data['openOrders']),1)
        self.assertNotIn('secret',data['openOrders'][0]);self.assertFalse(data['recentFills'])
        self.assertEqual(data['pendingTickets'][0]['state'],'UNKNOWN')
        self.assertTrue(any('avstämning' in w for w in data['warnings']))
    def test_read_failure_does_not_show_previous_balance_as_current(self):
        self.check();self.e.last_account=None;self.e.last_error='API-åtkomst saknas'
        data=snapshot(self.runtime)
        self.assertIsNone(data['totals']['accountValueEUR']);self.assertFalse(data['freshness']['account']['fresh'])
        self.assertIn('API-åtkomst saknas',data['warnings'])

    def test_today_realized_result_uses_stockholm_day_and_actual_fees(self):
        from test_trade_history import fill
        values=[fill('BUY','buy','2','100','2','2026-10-21T20:00:00+00:00','gamma'),
                fill('SELL-OLD','sell','1','60','1','2026-10-21T21:00:00+00:00','gamma'),
                fill('SELL-TODAY','sell','1','70','1','2026-10-21T22:15:00+00:00','gamma')]
        with self.store.lock,self.store.db:
            for f in values:self.store.db.execute('INSERT INTO live_fills VALUES(?,?)',(f['tradeId'],json.dumps(f)))
        data=snapshot(self.runtime,datetime(2026,10,21,22,30,tzinfo=timezone.utc))
        self.assertEqual(Decimal(data['totals']['todayRealizedNetEUR']),18)
        self.assertEqual(Decimal(data['totals']['realizedNetEUR']),26)
        self.assertEqual(len(data['recentFills']),3)

    def test_same_exchange_order_is_not_duplicated_as_local_pending_order(self):
        self.private.orders['OPEN-1']=dict(status='open',vol='.0001',vol_exec='0',descr=dict(pair='XBTEUR',type='buy',price='60000',ordertype='limit'))
        self.e.account()
        self.e.save(dict(id='LOCAL',state='OPEN',pair='XBTEUR',side='buy',agentId='gamma',exchangeOrderId='OPEN-1'),'TEST')
        data=snapshot(self.runtime)
        self.assertEqual(len(data['openOrders']),1);self.assertFalse(data['pendingTickets'])
        self.assertEqual(data['openOrders'][0]['agentId'],'gamma')

    def test_fresh_quotes_do_not_make_old_agent_signals_current(self):
        old=(datetime.now(timezone.utc)-timedelta(minutes=10)).isoformat()
        self.store.state['agentReports']={'gamma|BTC-SIM':dict(agentId='gamma',instrument='BTC-SIM',side='BUY',status='reading',closedAt=old)}
        self.store.state['chefSignals']={'BTC-SIM':dict(side='BUY',closedAt=old)}
        data=snapshot(self.runtime)
        self.assertTrue(data['freshness']['market']['fresh'])
        self.assertFalse(data['prices'][0]['signalFresh'])
        self.assertFalse(data['agents'][0]['reports'][0]['fresh'])

if __name__=='__main__':unittest.main()
