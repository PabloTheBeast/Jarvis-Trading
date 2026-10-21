import math
import unittest
from datetime import datetime, timezone

from backend.engine import Engine, FEE, clock, new_state

NOW = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.e = Engine(new_state())
        self.e.s['marketAuto'] = False
        self.e.now = NOW
        self.e.s['prices']['BTC-SIM'] = [600000]
        self.e.s['prices']['ETH-SIM'] = [25000]

    def test_signals_match_original_thresholds(self):
        e = self.e
        for strategy in ['Trend', 'Momentum', 'Mean reversion', 'ETF']:
            a = dict(strategy=strategy)
            e.s['prices']['ETF-WORLD'] = [100]*6+[110]
            e.s['tick'] = 5
            self.assertEqual(e.signal(a, 'ETF-WORLD'), 'SELL' if strategy == 'Mean reversion' else 'BUY')
            e.s['prices']['ETF-WORLD'] = [100]*6+[90]
            self.assertEqual(e.signal(a, 'ETF-WORLD'), 'BUY' if strategy == 'Mean reversion' else 'SELL')

    def test_cost_basis_and_fees_for_multiple_owners(self):
        e = self.e
        e.submit('gamma', 'BTC-SIM', 'BUY', 300)
        e.s['prices']['BTC-SIM'].append(700000)
        e.submit('iota', 'BTC-SIM', 'BUY', 300)
        qty = e.s['positions']['BTC-SIM']['owners']['gamma']
        e.submit('gamma', 'BTC-SIM', 'SELL', qty*700000+1)
        self.assertAlmostEqual(e.s['positions']['BTC-SIM']['cost'], e.s['ownerCost']['iota']['BTC-SIM'])
        self.assertAlmostEqual(e.s['realizedTotal'], 350*(1-FEE)-300*(1+FEE))
        self.assertTrue(e.audit())

    def test_order_and_portfolio_limits_include_fees(self):
        e = self.e
        e.capital(100000, 'deposit')
        for _ in range(60):
            for a in e.agents():
                for symbol in a['ids']:
                    e.submit(a['id'], symbol, 'BUY', 1e6)
            for o in list(e.s['orders']):
                if o['status'] == 'PARTIAL':
                    e.fill(o, next(a for a in e.agents() if a['id'] == o['agentId']))
        self.assertLessEqual(e.invested(), e.equity()*.7+1e-7)
        for symbol in e.s['positions']:
            self.assertLessEqual(e.value(symbol), e.equity()*.25+1e-7)
        self.assertTrue(all(o['requestedQty']*o.get('limitPrice', 0) <= 2000+1e-6 for o in e.s['orders']))
        self.assertTrue(e.audit())

    def test_correlated_group_limit(self):
        e = self.e
        series = [100*(1+.001*i*i) for i in range(20)]
        e.s['prices']['BTC-SIM'] = series[:]
        e.s['prices']['ETH-SIM'] = [p*2 for p in series]
        e.s['agentCount'] = 30
        for a in e.agents()[10:]:
            e.init_agent(a, False)
        for _ in range(12):
            for a in e.agents():
                for symbol in a['ids']:
                    if symbol.endswith('-SIM'):
                        e.submit(a['id'], symbol, 'BUY', 300)
        self.assertAlmostEqual(e.correlation('BTC-SIM', 'ETH-SIM'), 1)
        self.assertLessEqual(e.value('BTC-SIM')+e.value('ETH-SIM'), e.equity()*.4+1e-6)

    def test_partial_fill_rechecks_price_and_deduplicates(self):
        e = self.e
        e.s['orderSeq'] = 6
        o = e.submit('gamma', 'BTC-SIM', 'BUY', 650)
        self.assertEqual(o['status'], 'PARTIAL')
        self.assertEqual(e.submit('gamma', 'BTC-SIM', 'BUY', 100)['status'], 'REJECTED')
        e.s['prices']['BTC-SIM'].append(6000000)
        e.fill(o, next(a for a in e.agents() if a['id'] == 'gamma'))
        self.assertEqual(o['status'], 'CANCELLED')
        self.assertTrue(e.audit())

    def test_partial_order_total_respects_dynamic_order_cap(self):
        e = self.e
        e.s['orderSeq'] = 6
        o = e.submit('gamma', 'BTC-SIM', 'BUY', 650)
        self.assertEqual(o['status'], 'PARTIAL')
        e.s['prices']['BTC-SIM'].append(610000)
        e.fill(o, next(a for a in e.agents() if a['id'] == 'gamma'))
        self.assertEqual(o['status'], 'CANCELLED')
        self.assertAlmostEqual(o['filledNotional'], 390)

    def test_broker_rejection_uses_order_identity(self):
        self.e.s['orderSeq'] = 18
        o = self.e.submit('gamma', 'BTC-SIM', 'BUY', 200)
        self.assertEqual(o['status'], 'REJECTED')
        self.assertEqual(self.e.s['cash'], 10000)

    def test_stop_cancels_pending_and_survives_midnight(self):
        e = self.e
        e.s['orderSeq'] = 6
        o = e.submit('gamma', 'BTC-SIM', 'BUY', 200)
        e.command('start', {})
        e.command('stop', {})
        self.assertEqual(o['status'], 'CANCELLED')
        e.step({'BTC-SIM':600000, 'ETH-SIM':25000}, NOW)
        self.assertFalse(e.s['running'])

    def test_drawdown_blocks_buys_but_allows_exits(self):
        e = self.e
        e.submit('gamma', 'BTC-SIM', 'BUY', 300)
        e.s['peak'] = 12000
        e.global_risk()
        self.assertTrue(e.s['halt'])
        self.assertEqual(e.submit('gamma', 'ETH-SIM', 'BUY', 200)['status'], 'REJECTED')
        self.assertEqual(e.submit('gamma', 'BTC-SIM', 'SELL', 500)['status'], 'FILLED')
        with self.assertRaises(ValueError):
            e.command('approve', {})

    def test_recovery_cannot_restart_between_ten_and_twelve_percent(self):
        e = self.e
        e.s['peak'] = 11200
        e.pause('risk')
        q = e.s['globalPause']
        q.update(trades=5, wins=4, pnl=10, maxDD=5, next=0)
        self.assertFalse(e.review_shadow(q, True))
        e.s['approvalPending'] = True
        with self.assertRaises(ValueError):
            e.command('approve', {})
        self.assertTrue(e.s['halt'])

    def test_boolean_capital_is_rejected(self):
        with self.assertRaises(ValueError):
            self.e.capital(True, 'deposit')

    def test_stop_loss_and_quarantine_keep_lifetime_results(self):
        e = self.e
        e.submit('gamma', 'BTC-SIM', 'BUY', 300)
        e.step({'BTC-SIM':500000, 'ETH-SIM':25000}, NOW)
        self.assertNotIn('BTC-SIM', e.s['positions'])
        a = next(a for a in e.agents() if a['id'] == 'gamma')
        e.quarantine(a)
        realized = e.s['agentRealized']['gamma']
        q = e.s['quarantine']['gamma']
        q.update(trades=3, wins=2, pnl=10, maxDD=5, until=e.s['tick'])
        e.step({'BTC-SIM':500000, 'ETH-SIM':25000}, NOW)
        self.assertEqual(e.s['agentStatus']['gamma'], 'AKTIV')
        self.assertEqual(e.s['agentRealized']['gamma'], realized)
        self.assertEqual(e.s['riskBaseline']['gamma'], realized)

    def test_shadow_requires_review_and_manual_approval(self):
        e = self.e
        e.pause('test')
        q = e.s['globalPause']
        q.update(trades=5, wins=3, pnl=10, maxDD=5)
        self.assertFalse(e.review_shadow(q, True))
        e.s['tick'] = 40
        self.assertTrue(e.review_shadow(q, True))
        e.s['approvalPending'] = True
        cash = e.s['cash']
        e.command('approve', {})
        self.assertFalse(e.s['halt'])
        self.assertEqual(e.s['cash'], cash)

    def test_deposits_are_not_profits_and_vault_needs_approval(self):
        e = self.e
        e.capital(10000, 'deposit')
        self.assertEqual(e.sweep('test'), (0, 0))
        e.submit('gamma', 'BTC-SIM', 'BUY', 300)
        e.s['prices']['BTC-SIM'].append(900000)
        e.submit('gamma', 'BTC-SIM', 'SELL', 1000)
        profit = e.s['realizedTotal']
        for _ in range(5):
            e.close_day()
            e.open_day()
        self.assertAlmostEqual(e.s['vault'], profit*.2)
        before = e.s['vault']
        self.assertEqual(e.sweep('test')[1], 0)
        with self.assertRaises(ValueError):
            e.command('release', dict(amount=1))
        e.command('release', dict(amount=1, approved=True))
        self.assertAlmostEqual(e.s['vault'], before-1)
        self.assertTrue(e.audit())

    def test_incomplete_and_nonfinite_snapshot_rejected_before_mutation(self):
        for prices in [{}, {'BTC-SIM':math.nan, 'ETH-SIM':200}, {'BTC-SIM':0, 'ETH-SIM':200}]:
            with self.assertRaises(ValueError):
                self.e.step(prices)
            self.assertEqual(self.e.s['tick'], 0)

    def test_stock_schedule_and_stockholm_midnight(self):
        e = self.e
        e.s['marketAuto'] = True
        saturday = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
        e.step({'BTC-SIM':600000, 'ETH-SIM':25000}, saturday)
        self.assertEqual(len(e.s['prices']['STOCK-A']), 1)
        e.step({'BTC-SIM':600000, 'ETH-SIM':25000}, datetime(2026, 10, 24, 22, tzinfo=timezone.utc))
        self.assertEqual(len(e.s['dayReports']), 1)
        self.assertEqual(clock(datetime(2026, 10, 21, 7, tzinfo=timezone.utc)), ('2026-10-21', True))
        self.assertFalse(clock(datetime(2026, 10, 21, 15, 30, tzinfo=timezone.utc))[1])

    def test_expansion_only_after_profitable_five_days_and_quarantines(self):
        e = self.e
        e.capital(20000, 'deposit')
        for _ in range(4):
            e.close_day()
            e.open_day()
        self.assertEqual(e.s['agentCount'], 10)
        # Valid realized paper gain for the fifth report.
        e.submit('gamma', 'BTC-SIM', 'BUY', 300)
        e.s['prices']['BTC-SIM'].append(900000)
        e.submit('gamma', 'BTC-SIM', 'SELL', 1000)
        e.close_day()
        self.assertEqual(e.s['agentCount'], 12)
        self.assertEqual(e.s['agentStatus']['extra11'], 'KARANTÄN')
        self.assertEqual(e.s['agentStatus']['extra12'], 'KARANTÄN')

    def test_representative_500_tick_run_reconciles(self):
        e = self.e
        for n in range(500):
            e.step({'BTC-SIM':600000*(1+.03*math.sin(n/5)),
                    'ETH-SIM':25000*(1+.04*math.sin(n/7))}, NOW)
        self.assertGreater(len(e.s['orderHistory']), 20)
        self.assertTrue(e.audit())


if __name__ == '__main__':
    unittest.main()
