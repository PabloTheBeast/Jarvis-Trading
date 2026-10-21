import unittest
from datetime import datetime,timezone,timedelta
from decimal import Decimal
from backend.shadow import update

class ShadowTests(unittest.TestCase):
    def setUp(self):
        self.at=datetime.now(timezone.utc)
        self.book=dict(positions={},trades=[],lastBars={})
        self.rule=dict(quantityStep='.001',minimumVolume='.001',minimumCost='0')
        self.levels=dict(bids=[['99.9','10']],asks=[['100','10']])
        self.exit=dict(lossFraction='.05')
    def tick(self,side='BUY',bar=1,seconds=0):
        update(self.book,'XBTEUR',self.levels,self.rule,side,bar,self.exit,self.at+timedelta(seconds=seconds),'100')
    def test_next_observation_fill_and_fee_slippage(self):
        self.tick();self.assertFalse(self.book['positions']);self.assertTrue(self.book['pending'])
        self.tick(seconds=15)
        self.assertTrue(self.book['positions']);self.assertFalse(self.book['pending'])
        p=self.book['positions']['XBTEUR']
        self.assertGreater(Decimal(p['costEUR']),Decimal(p['quantity'])*100)
    def test_partial_fill_then_expiry_keeps_owned_inventory(self):
        self.levels['asks'][0][1]='.01'
        self.tick();self.tick(seconds=15)
        self.assertEqual(self.book['positions']['XBTEUR']['quantity'],'0.001')
        self.assertTrue(self.book['pending'])
        self.tick(seconds=61)
        self.assertFalse(self.book['pending']);self.assertTrue(self.book['positions'])
        self.assertIn('EXPIRED',[r['kind'] for r in self.book['orders']])
    def test_sale_realizes_net_and_removes_position(self):
        self.tick();self.tick(seconds=15)
        self.tick(side='SELL',bar=2,seconds=30);self.tick(side='SELL',bar=2,seconds=45)
        self.assertFalse(self.book['positions']);self.assertEqual(len(self.book['trades']),1)
        self.assertLess(Decimal(self.book['trades'][0]['netEUR']),0)
    def test_small_budget_below_minimum_produces_no_order(self):
        self.rule['minimumCost']='25';self.tick()
        self.assertFalse(self.book.get('pending'));self.assertFalse(self.book['positions'])
    def test_limit_moves_away_and_never_fills_then_expires(self):
        self.tick();self.levels['asks'][0][0]='100.5';self.tick(seconds=15)
        self.assertFalse(self.book['positions']);self.tick(seconds=61)
        self.assertFalse(self.book.get('pending'))
    def test_partial_sale_expiry_preserves_actual_net(self):
        self.tick();self.tick(seconds=15)
        self.levels['bids'][0][1]='.01'
        self.tick(side='SELL',bar=2,seconds=30);self.tick(side='SELL',bar=2,seconds=45)
        self.tick(side='WAIT',bar=2,seconds=91)
        self.assertEqual(len(self.book['trades']),1);self.assertTrue(self.book['positions'])
        self.assertLess(Decimal(self.book['trades'][0]['netEUR']),0)
