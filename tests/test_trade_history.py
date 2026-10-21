import copy
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime,timezone
from decimal import Decimal
from pathlib import Path

from backend.approval import issue
from backend.live import LiveService,fingerprint
from backend.server import Store,Runtime
from backend.trade_history import history
from test_analysis_live import Public,Private


def fill(ident,side,quantity,cost,fee,when,agent=None):
    return dict(tradeId=ident,pair='XBTEUR',side=side,quantity=str(quantity),costEUR=str(cost),
                feeEUR=str(fee),priceEUR='60000',executedAt=when,agentId=agent)


class MatchingTests(unittest.TestCase):
    def test_fifo_partial_sales_fees_and_open_purchase_dates(self):
        data=[fill('B1','buy','2','200','2','2026-10-21T10:00:00+00:00'),
              fill('B2','buy','1','120','1','2026-10-21T11:00:00+00:00'),
              fill('S1','sell','2.5','350','3.5','2026-10-22T12:00:00+00:00')]
        h=history(data)
        self.assertEqual(len(h['closed']),2)
        self.assertEqual(h['open'][0]['quantity'],'0.5')
        self.assertEqual(h['open'][0]['boughtAt'],data[1]['executedAt'])
        self.assertEqual(sum(Decimal(t['netEUR']) for t in h['closed']),Decimal('84'))
        self.assertTrue(all(t['soldAt']==data[2]['executedAt'] for t in h['closed']))

    def test_unknown_entry_and_other_agents_never_create_fake_profit(self):
        h=history([fill('B','buy','1','100','1','2026-10-21T10:00:00+00:00','gamma'),
                   fill('S','sell','1','120','1','2026-10-21T11:00:00+00:00','zeta')])
        self.assertFalse(h['closed'])
        self.assertIsNone(h['unmatchedSales'][0]['netEUR'])
        self.assertEqual(h['open'][0]['agentId'],'gamma')


class ExchangeHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'paper.db'
        self.store=Store(self.path);self.private=Private()
        self.live=LiveService(self.store,Public(),self.private,enabled=True)
        request=dict(pair='XBTEUR',side='buy',volume='0.0002',price='60000.0')
        ticket=self.live.run('preview',request,'preview')
        ticket=self.live.run('validate',dict(ticket=ticket['id']),'validate')
        self.live.run('submit',dict(ticket=ticket['id']),'submit',issue(self.store.operator_key,'live-order',subject=fingerprint(ticket)))
        self.ticket=ticket

    def tearDown(self):
        self.store.close();self.tmp.cleanup()

    def seed_fill(self,ident='F1',volume='0.0001'):
        self.private.trades[ident]=dict(ordertxid='KRAKEN-ORDER',pair='XXBTZEUR',type='buy',
            vol=volume,price='60000',cost=str(Decimal(volume)*60000),fee='0.01',time=str(time.time()-2))
        self.private.orders['KRAKEN-ORDER'].update(status='canceled',vol_exec=volume,cost=str(Decimal(volume)*60000),fee='0.01',trades=[ident])

    def test_confirmed_fill_uses_exchange_time_not_order_or_preview_time_and_survives_restart(self):
        self.seed_fill();stamp=self.private.trades['F1']['time']
        before=copy.deepcopy(self.store.state['book'])
        self.live.run('reconcile',dict(ticket=self.ticket['id']),'reconcile')
        execution=self.live.status()['tradeHistory']['executions'][0]
        self.assertEqual(execution['executedAt'],datetime.fromtimestamp(float(stamp),timezone.utc).isoformat())
        self.assertIsNone(execution['agentId'])
        self.assertEqual(self.store.state['book'],before)
        self.store.close();self.store=Store(self.path)
        self.live=LiveService(self.store,Public(),self.private)
        self.live.run('reconcile',dict(ticket=self.ticket['id']),'again')
        self.assertEqual(len(self.live.status()['tradeHistory']['executions']),1)
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.db:self.store.db.execute('DELETE FROM live_fills')

    def test_open_unfilled_order_has_no_execution_or_purchase_time(self):
        self.live.run('reconcile',dict(ticket=self.ticket['id']),'open')
        self.assertEqual(self.live.get(self.ticket['id'])['state'],'OPEN')
        self.assertFalse(self.live.status()['tradeHistory']['executions'])

    def test_missing_or_mismatched_fill_never_invents_trade(self):
        self.seed_fill();self.private.trades['F1']['ordertxid']='OTHER-ORDER'
        result=self.live.run('reconcile',dict(ticket=self.ticket['id']),'mismatch')
        self.assertFalse(result['fillHistoryComplete'])
        self.assertIn('historyError',result)
        self.assertFalse(self.live.status()['tradeHistory']['executions'])

    def test_incomplete_later_history_preserves_verified_fills(self):
        self.seed_fill();self.live.run('reconcile',dict(ticket=self.ticket['id']),'first')
        self.private.orders['KRAKEN-ORDER'].update(vol_exec='0.0002',trades=['F1','MISSING'])
        result=self.live.run('reconcile',dict(ticket=self.ticket['id']),'incomplete')
        self.assertIn('historyError',result)
        self.assertEqual(len(self.live.status()['tradeHistory']['executions']),1)

    def test_account_refresh_also_reconciles_known_orders(self):
        self.seed_fill();self.live.inspect()
        self.assertEqual(len(self.live.status()['tradeHistory']['executions']),1)
        self.assertTrue(any(method=='QueryTrades' for method,_ in self.private.calls))

    def test_partial_fills_are_batched_and_keep_distinct_execution_times(self):
        ids=[]
        base=time.time()-1
        for index in range(25):
            ident='F'+str(index);ids.append(ident)
            self.private.trades[ident]=dict(ordertxid='KRAKEN-ORDER',pair='XXBTZEUR',type='buy',
                vol='0.000008',price='60000',cost='0.48',fee='0.001',time=str(base+index*.001))
        self.private.orders['KRAKEN-ORDER'].update(status='closed',vol_exec='0.0002',cost='12',fee='0.025',trades=ids)
        self.live.run('reconcile',dict(ticket=self.ticket['id']),'many-fills')
        executions=self.live.status()['tradeHistory']['executions']
        self.assertEqual(len(executions),25)
        self.assertEqual(len({f['executedAt'] for f in executions}),25)
        batches=[data for method,data in self.private.calls if method=='QueryTrades']
        self.assertEqual([len(batch['txid'].split(',')) for batch in batches],[20,5])

    def test_default_runtime_disables_paper_and_keeps_archive(self):
        before=copy.deepcopy(self.store.state)
        runtime=Runtime(self.store,Public())
        self.assertFalse(self.store.snapshot()['paperMode'])
        for name in ('start','step','deposit','sweep'):
            with self.assertRaises(ValueError):runtime.command(name,{},'disabled-'+name)
        self.assertEqual(self.store.state,before)


if __name__=='__main__':unittest.main()
