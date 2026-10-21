import copy
import unittest
from datetime import timedelta

from backend.advisory import review
from backend.engine import Engine,new_state
from test_analysis_live import NOW,analyse,candles,Public


class AdvisoryTests(unittest.TestCase):
    def setUp(self):
        self.engine=Engine(new_state());self.engine.now=NOW
        self.analysis=analyse(candles(),NOW)
        self.analysis['signals']={'Trend':'BUY','Momentum':'BUY','Mean reversion':None}
        self.engine.s['marketAnalysis']={s:copy.deepcopy(self.analysis) for s in ('BTC-SIM','ETH-SIM')}

    def alerts(self):
        return [n for n in self.engine.s['notifications'] if n.get('kind')=='CRYPTO_SIGNAL']

    def test_reports_consensus_and_alert_without_money_or_orders(self):
        before=copy.deepcopy(self.engine.s['book'])
        review(self.engine)
        self.assertEqual(len(self.engine.s['agentReports']),5)
        self.assertEqual(self.engine.s['chefSignals']['BTC-SIM']['side'],'BUY')
        self.assertEqual(self.engine.s['chefSignals']['ETH-SIM']['side'],'WAIT')
        self.assertEqual(len(self.alerts()),1)
        self.assertEqual(self.engine.s['tick'],0)
        self.assertFalse(self.engine.s['orders'])
        self.assertEqual(self.engine.s['book'],before)
        self.assertFalse(self.engine.s['signalDecisions'])
        self.assertEqual(self.engine.signal(next(a for a in self.engine.agents() if a['id']=='iota'),'BTC-SIM'),'BUY')

    def test_deduplication_survives_recreation_and_same_bar_risk_changes(self):
        review(self.engine)
        restored=Engine(self.engine.s);restored.now=NOW;review(restored)
        self.engine.s['halt']=True;review(self.engine)
        self.engine.s['halt']=False;review(self.engine)
        self.assertEqual(len(self.alerts()),1)

    def test_unchanged_signal_on_next_bar_does_not_spam(self):
        review(self.engine)
        self.engine.now+=timedelta(minutes=5)
        for analysis in self.engine.s['marketAnalysis'].values():
            analysis['barId']+=300
            analysis['closedAt']=self.engine.now.isoformat()
        review(self.engine)
        self.assertEqual(len(self.alerts()),1)

    def test_conflicting_strategies_and_cloned_agents_do_not_inflate_votes(self):
        self.engine.s['marketAnalysis']['BTC-SIM']['signals']['Mean reversion']='SELL'
        review(self.engine)
        self.assertEqual(self.engine.s['chefSignals']['BTC-SIM']['side'],'WAIT')
        self.engine.s['agentCount']=30
        for a in self.engine.agents()[10:]:self.engine.init_agent(a,False)
        self.engine.s['marketAnalysis']['BTC-SIM']['signals'].update(Trend=None,Momentum='BUY',**{'Mean reversion':None})
        review(self.engine)
        self.assertEqual(self.engine.s['chefSignals']['BTC-SIM']['side'],'WAIT')
        self.assertFalse(self.alerts())

    def test_stale_data_and_quarantined_agents_do_not_create_buy_alert(self):
        self.engine.s['agentStatus']['iota']='KARANTÄN'
        review(self.engine)
        self.assertEqual(self.engine.s['chefSignals']['BTC-SIM']['side'],'WAIT')
        self.engine.s['agentStatus']['iota']='AKTIV'
        self.engine.now+=timedelta(minutes=6)
        review(self.engine)
        self.assertFalse(self.alerts())
        self.assertTrue(all(r['status']=='waiting' for r in self.engine.s['agentReports'].values()))

    def test_sell_is_conditional_and_buy_is_blocked_by_risk_pause(self):
        self.engine.s['halt']=True;review(self.engine)
        self.assertEqual(self.engine.s['chefSignals']['BTC-SIM']['side'],'BLOCKED')
        self.assertFalse(self.alerts())
        for a in self.engine.s['marketAnalysis'].values():a['signals'].update(Trend='SELL',Momentum='SELL')
        review(self.engine)
        self.assertEqual(self.engine.s['chefSignals']['BTC-SIM']['side'],'SELL')
        self.assertIn('om du äger',self.alerts()[0]['msg'])

    def test_observe_integration_and_restart_keep_alerts_and_do_not_trade(self):
        source=Public();prices=source.fetch()
        source.metadata.update(fetchedAt=NOW.isoformat(),analysis=self.engine.s['marketAnalysis'])
        self.engine.observe(prices,source.metadata)
        restored=Engine(copy.deepcopy(self.engine.s));restored.now=NOW
        restored.observe(prices,source.metadata)
        self.assertEqual(len(restored.s['notifications']),1)
        self.assertFalse(restored.s['orders'])


if __name__=='__main__':unittest.main()
