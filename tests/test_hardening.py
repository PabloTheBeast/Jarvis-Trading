import copy
import csv
import io
import json
import logging
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from backend.accounting import Ledger, units
from backend.approval import ApprovalError, issue, verify
from backend.calendar import clock, session
from backend.engine import Engine, new_state
from backend.exporting import capital, trades
from backend.ops import backup
from backend.server import Runtime, Store

NOW = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)


def engine():
    e = Engine(new_state())
    e.now = NOW
    e.s['prices']['BTC-SIM'] = [600000]
    e.s['prices']['ETH-SIM'] = [25000]
    e.s['paperBroker'] = dict(testFaults=False, spreadBps=0, slippageBps=0)
    return e


def gain(e):
    e.submit('gamma', 'BTC-SIM', 'BUY', 300, intent_id='gain-buy-'+str(e.s['tick']))
    e.s['prices']['BTC-SIM'].append(e.last('BTC-SIM')*1.5)
    e.submit('gamma', 'BTC-SIM', 'SELL', 10000, intent_id='gain-sell-'+str(e.s['tick']))


class CalendarAndOrderTests(unittest.TestCase):
    def test_stockholm_holidays_half_days_and_dst(self):
        for day in ['2026-01-01','2026-01-06','2026-04-03','2026-04-06',
                    '2026-05-01','2026-05-14','2026-06-19','2026-12-24','2026-12-25','2026-12-31']:
            self.assertIsNone(session(day), day)
        for day in ['2026-01-05','2026-04-02','2026-04-30','2026-05-13','2026-10-30']:
            self.assertEqual(session(day), (540,780), day)
        self.assertTrue(clock(datetime(2026,1,7,8,tzinfo=timezone.utc))[1])
        self.assertTrue(clock(datetime(2026,7,7,7,tzinfo=timezone.utc))[1])
        self.assertFalse(clock(datetime(2026,4,2,11,tzinfo=timezone.utc))[1])
        self.assertIsNone(session('2028-01-03'))

    def test_manual_close_cannot_create_five_days_or_expand(self):
        e = engine()
        e.capital(20000, 'deposit')
        gain(e)
        for _ in range(5):
            e.command('close-day', {})
            with self.assertRaises(ValueError):
                e.command('start', {})
        self.assertEqual(len(e.s['dayReports']), 1)
        self.assertEqual(e.s['agentCount'], 10)
        self.assertEqual(e.s['vault'], 0)

    def test_duplicate_strategy_intent_and_duplicate_fill_are_noops(self):
        e = engine()
        first = e.submit('gamma', 'BTC-SIM', 'BUY', 200)
        before = copy.deepcopy(e.s)
        self.assertIs(e.submit('gamma', 'BTC-SIM', 'BUY', 200), first)
        e.fill(first, next(a for a in e.agents() if a['id']=='gamma'))
        self.assertEqual(before, e.s)
        self.assertEqual(len(e.s['orderHistory']), 1)

    def test_stale_market_snapshot_does_not_mutate_accounts(self):
        e=engine()
        before=copy.deepcopy(e.s)
        with self.assertRaises(ValueError):
            e.step({'BTC-SIM':600000,'ETH-SIM':25000},NOW,dict(
                snapshotId='old', fetchedAt=(NOW-timedelta(seconds=31)).isoformat()))
        self.assertEqual(e.s,before)

    def test_eight_simulated_days_reconcile_money_and_unique_reports(self):
        import math
        e=engine()
        for n in range(2400):
            e.step({'BTC-SIM':600000*(1+.02*math.sin(n/13)),
                    'ETH-SIM':25000*(1+.03*math.sin(n/17))},NOW+timedelta(minutes=5*n))
        dates=[r['date'] for r in e.s['dayReports']]
        self.assertGreaterEqual(len(dates),8)
        self.assertEqual(len(dates),len(set(dates)))
        self.assertTrue(e.audit())
        self.assertGreater(len(e.s['orderHistory']),20)

    def test_week_is_seven_dates_and_manual_sweep_does_not_postpone_it(self):
        e = engine()
        gain(e)
        profit = e.s['realizedTotal']
        for i in range(7):
            e.now = NOW+timedelta(days=i)
            if i:
                e.open_day()
            e.close_day()
            if i < 6:
                self.assertEqual(e.s['vault'], 0)
        self.assertAlmostEqual(e.s['vault'], profit*.2, places=8)
        self.assertEqual(e.s['nextSweepDate'], '2026-11-01')
        anchor = e.s['weeklyLastDate']
        e.sweep('EXTRA VINSTSKYDD')
        self.assertEqual(e.s['weeklyLastDate'], anchor)

    def test_expansion_excludes_weekends_and_is_reviewed_once(self):
        e = engine()
        e.capital(20000, 'deposit')
        for offset in [0,1,2,3]:
            e.now = NOW+timedelta(days=offset)
            if offset:
                e.open_day()
            e.close_day()
        for offset in [4,5,6]:
            e.now = NOW+timedelta(days=offset)
            e.open_day()
            if offset==4:
                gain(e)
            e.close_day()
        self.assertEqual(e.s['agentCount'], 12)
        self.assertEqual(len(e.s['expansionDates']), 5)
        e.expand()
        self.assertEqual(e.s['agentCount'], 12)
        self.assertEqual(e.s['agentStatus']['extra11'], 'KARANTÄN')

    def test_cached_snapshot_cannot_accelerate_shadow_reviews(self):
        e = engine()
        prices = {'BTC-SIM':600000, 'ETH-SIM':25000}
        e.step(prices, NOW, metadata=dict(snapshotId='snapshot'))
        before = copy.deepcopy(e.s)
        for _ in range(50):
            e.step(prices, NOW, metadata=dict(snapshotId='snapshot'))
        self.assertEqual(e.s, before)

    def test_depth_liquidity_is_shared_and_partial_fill_is_recorded_once(self):
        e = engine()
        e.s['marketData'].update(snapshotId='one', orderBooks={
            'BTC-SIM':dict(asks=[[600000,.0005]], bids=[[599000,.0005]])})
        first = e.submit('gamma','BTC-SIM','BUY',600)
        second = e.submit('iota','BTC-SIM','BUY',600)
        self.assertEqual(first['status'], 'PARTIAL')
        self.assertEqual(second['filledQty'], 0)
        self.assertAlmostEqual(first['filledQty'], .0005)
        self.assertEqual(len(e.s['orderHistory']), 1)
        self.assertTrue(e.audit())
        e.now += timedelta(seconds=61)
        e.fill(second, next(a for a in e.agents() if a['id']=='iota'))
        self.assertEqual(second['status'], 'CANCELLED')

    def test_spread_and_slippage_are_adverse(self):
        e = engine()
        e.s['paperBroker'] = dict(spreadBps=10, slippageBps=5)
        buy = e.submit('gamma','BTC-SIM','BUY',300)
        self.assertEqual(buy['status'], 'FILLED')
        self.assertGreater(e.s['orderHistory'][0]['price'], 600000)
        e.submit('gamma','BTC-SIM','SELL',1000)
        self.assertLess(e.s['orderHistory'][0]['price'], 600000)
        self.assertLess(e.s['realizedTotal'], -.9)


class MoneyAndOwnerTests(unittest.TestCase):
    def test_thousand_small_deposits_are_exact(self):
        e=engine()
        for _ in range(1000):
            e.capital(.01, 'deposit')
        self.assertEqual(e.s['book']['cash'], units(10010))
        self.assertTrue(e.audit())
        e.s['cash'] += .000001
        with self.assertRaises(ValueError):
            e.audit()

    def test_losses_must_be_recovered_before_new_profit_protection(self):
        e=engine()
        gain(e)
        e.sweep('test')
        initial_watermark=e.s['book']['realizedHighWater']
        e.s['tick']+=1
        e.submit('gamma','BTC-SIM','BUY',300)
        e.s['prices']['BTC-SIM'].append(e.last('BTC-SIM')*.5)
        e.submit('gamma','BTC-SIM','SELL',10000)
        self.assertEqual(e.sweep('test')[1], 0)
        self.assertEqual(e.s['book']['realizedHighWater'], initial_watermark)
        e.s['tick']+=1
        gain(e)  # A similarly sized gain still does not recover both sets of fees.
        self.assertEqual(e.sweep('test')[1], 0)
        self.assertTrue(e.audit())

    def test_sweep_cannot_spend_reserved_protected_capital(self):
        e=engine()
        gain(e)
        e.sweep('test')
        vault=e.s['book']['vault']
        for i in range(8):
            e.s['tick']+=1
            e.submit('gamma','BTC-SIM','BUY',1000)
        self.assertEqual(e.s['book']['vault'],vault)
        with self.assertRaises(ValueError):
            e.capital(1,'release')

    def test_approval_expiry_amount_action_and_signature(self):
        key=b'unit-test-key'
        token=issue(key,'release',10,now=100)
        self.assertTrue(verify(key,token,'release',10,now=101)['actor'])
        for action,value,now in [('release',11,101),('approve',10,101),('release',10,221)]:
            with self.assertRaises(ApprovalError):
                verify(key,token,action,value,now=now)
        with self.assertRaises(ApprovalError):
            verify(b'wrong-key',token,'release',10,now=101)

    def test_zero_equity_cannot_approve_recovery(self):
        e=engine()
        e.capital(10000,'withdraw')
        e.pause('test')
        e.s['globalPause'].update(trades=5,wins=4,pnl=10,maxDD=1,next=0)
        self.assertFalse(e.review_shadow(e.s['globalPause'],True))
        e.s['approvalPending']=True
        e.operator='test-owner'
        with self.assertRaises(ValueError):
            e.command('approve',{})

    def test_reserved_buy_cash_cannot_be_withdrawn_or_swept(self):
        e=engine()
        gain(e)
        # Reserve almost all cash using an accepted-but-unfilled paper order.
        # No synthetic market execution is involved in this ledger boundary test.
        price=e.last('BTC-SIM')
        e.s['orders'].insert(0,dict(orderId='RESERVATION',side='BUY',status='NEW',
            instrument='BTC-SIM',requestedQty=e.s['cash']/(price*1.0015),filledQty=0))
        before=e.s['book']['cash']
        self.assertLessEqual(Ledger(e).free_cash(),1)
        self.assertEqual(e.sweep('test')[1],0)
        self.assertEqual(e.s['book']['cash'],before)
        with self.assertRaises(ValueError):
            e.capital(1,'withdraw')

    def test_signed_release_is_single_use_and_has_attributable_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'paper.db')
            def seed(e):
                e.s['prices']['BTC-SIM']=[600000]
                e.s['prices']['ETH-SIM']=[25000]
                e.s['paperBroker']=dict(spreadBps=0,slippageBps=0)
                gain(e)
                e.sweep('test')
            store.change('seed',seed)
            r=Runtime(store)
            with self.assertRaises(ApprovalError):
                r.command('release',dict(amount=1,approved=True),'unsigned')
            token=issue(store.operator_key,'release',1)
            r.command('release',dict(amount=1,approved=True),'release',token)
            balance=store.state['book']['vault']
            r.command('release',dict(amount=1,approved=True),'release',token)
            self.assertEqual(store.state['book']['vault'],balance)
            with self.assertRaises(ValueError):
                r.command('release',dict(amount=1,approved=True),'replay',token)
            exported=capital(store.snapshot()['state']).decode('utf-8-sig')
            rows=list(csv.DictReader(io.StringIO(exported)))
            row=next(row for row in rows if row['type']=='VAULT_RELEASE')
            self.assertTrue(row['actor'])
            self.assertTrue(row['approvalId'])
            self.assertNotIn(token,exported)
            store.close()


class LoggingTests(unittest.TestCase):
    def test_log_format_is_structured_and_omits_sensitive_extras(self):
        from backend.logging_config import JsonFormatter
        record=logging.LogRecord('jarvis',logging.WARNING,__file__,1,'Market data unavailable',(),None)
        record.requestId='request'
        record.command='step'
        record.approval='DO-NOT-LOG'
        payload=json.loads(JsonFormatter().format(record))
        self.assertEqual(payload['level'],'WARNING')
        self.assertEqual(payload['requestId'],'request')
        self.assertIn('at',payload)
        self.assertNotIn('DO-NOT-LOG',json.dumps(payload))


class RecoveryTests(unittest.TestCase):
    def test_hard_crash_before_and_after_commit(self):
        for stage,expected in [('after_state_write',10000),('after_commit',10100)]:
            with self.subTest(stage=stage),tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'paper.db'
                store=Store(path)
                store.close()
                child = (
                    'import os,sys; from backend.server import Store; '
                    's=Store(sys.argv[1]); '
                    's.fault_hook=lambda stage: os._exit(77) if stage==sys.argv[2] else None; '
                    "s.change('deposit',lambda e:e.command('deposit',dict(amount=100)), 'crash-key', 'request')"
                )
                result=subprocess.run([sys.executable,'-c',child,str(path),stage],capture_output=True,timeout=10)
                self.assertEqual(result.returncode,77,result.stderr.decode())
                store=Store(path)
                self.assertEqual(store.state['cash'],expected)
                self.assertFalse(store.state['running'])
                self.assertEqual(store.db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
                committed=store.db.execute("SELECT count(*) FROM commands WHERE id='crash-key'").fetchone()[0]
                self.assertEqual(committed,int(stage=='after_commit'))
                self.assertTrue(Engine(store.state).audit())
                store.close()

    def test_failed_journal_write_rolls_back_balances(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'paper.db')
            before=copy.deepcopy(store.state)
            def deny(action, arg1, arg2, db, source):
                return sqlite3.SQLITE_DENY if action==sqlite3.SQLITE_INSERT and arg1=='journal' else sqlite3.SQLITE_OK
            store.db.set_authorizer(deny)
            with self.assertRaises(sqlite3.DatabaseError):
                store.change('deposit',lambda e:e.capital(100,'deposit'))
            store.db.set_authorizer(None)
            self.assertEqual(store.state,before)
            persisted=json.loads(store.db.execute('SELECT payload FROM state').fetchone()[0])
            self.assertEqual(persisted['cash'],before['cash'])
            store.close()

    def test_backup_restore_and_journal_cannot_be_rewritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'paper.db';target=Path(tmp)/'backup.db'
            store=Store(path)
            store.change('deposit',lambda e:e.capital(100,'deposit'))
            backup(path,target)
            for statement in ['DELETE FROM journal','UPDATE journal SET kind=kind']:
                with self.assertRaises(sqlite3.IntegrityError):
                    store.db.execute(statement)
            store.close()
            restored=Store(target)
            self.assertEqual(restored.state['cash'],10100)
            self.assertTrue(Engine(restored.state).audit())
            restored.close()

    def test_v1_migration_keeps_money_and_creates_verified_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'paper.db'
            legacy=new_state()
            legacy.pop('book')
            legacy.pop('schemaVersion')
            db=sqlite3.connect(path)
            db.execute('CREATE TABLE state(id INTEGER PRIMARY KEY,payload TEXT NOT NULL)')
            db.execute('INSERT INTO state VALUES(1,?)',(json.dumps(legacy),))
            db.commit();db.close()
            store=Store(path)
            self.assertEqual(store.state['schemaVersion'],2)
            self.assertEqual(store.state['cash'],10000)
            copies=list(Path(tmp).glob('*.pre-v2-*.sqlite3'))
            self.assertEqual(len(copies),1)
            preserved=sqlite3.connect(copies[0])
            self.assertNotIn('book',json.loads(preserved.execute('SELECT payload FROM state').fetchone()[0]))
            preserved.close()
            store.close()

    def test_intent_deduplication_survives_process_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'paper.db'
            store=Store(path)
            def submit(e):
                e.s['prices']['BTC-SIM']=[600000]
                e.s['paperBroker']=dict(spreadBps=0,slippageBps=0)
                return e.submit('gamma','BTC-SIM','BUY',200)
            store.change('order',submit)
            before=store.state['book']['cash']
            store.close()
            store=Store(path)
            store.change('order-replay',submit)
            self.assertEqual(store.state['book']['cash'],before)
            self.assertEqual(len(store.state['orderHistory']),1)
            store.close()

    def test_unknown_schema_releases_database_lock_and_preserves_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'future.db'
            payload=new_state()
            payload['schemaVersion']=999
            db=sqlite3.connect(path)
            db.execute('CREATE TABLE state(id INTEGER PRIMARY KEY,payload TEXT NOT NULL)')
            db.execute('INSERT INTO state VALUES(1,?)',(json.dumps(payload),))
            db.commit();db.close()
            for _ in range(2):
                with self.assertRaisesRegex(ValueError,'newer JARVIS'):
                    Store(path)
            db=sqlite3.connect(path)
            self.assertEqual(json.loads(db.execute('SELECT payload FROM state').fetchone()[0])['schemaVersion'],999)
            db.close()

    def test_risk_pause_survives_restart_and_unsigned_approval_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'paper.db'
            store=Store(path)
            store.change('pause',lambda e:e.command('pause',{}))
            store.close()
            store=Store(path)
            self.assertTrue(store.state['halt'])
            with self.assertRaises(ApprovalError):
                Runtime(store).command('approve',{},'unsigned')
            self.assertTrue(store.state['halt'])
            store.close()


if __name__=='__main__':
    unittest.main()
