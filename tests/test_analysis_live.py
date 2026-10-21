import base64
import copy
import io
import json
import math
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch

from backend.analysis import analyse,correlation
from backend.approval import issue
from backend.engine import Engine,new_state
from backend.kraken_private import KrakenPrivate,api_signature,ExchangeError,ExchangeUnknown,load_credentials,CredentialsMissing
from backend.live import LiveService,fingerprint
from backend.server import Store,Runtime

NOW=datetime(2026,10,21,12,0,tzinfo=timezone.utc)


def candles(count=90,start=None,scale=1):
    start=int((NOW-timedelta(seconds=count*300)).timestamp()) if start is None else start
    result=[]
    for i in range(count):
        close=(100+i*.04+math.sin(i/5)*.2)*scale
        result.append([start+i*300,str(close-.03*scale),str(close+.2*scale),str(close-.2*scale),str(close),'0','3',4])
    return result


class AnalysisTests(unittest.TestCase):
    def test_only_closed_candles_and_interval_independent_signals(self):
        rows=candles()
        current=[int(NOW.timestamp()),'9000','10000','8000','9999','0','4',1]
        original=analyse(rows,NOW)
        self.assertEqual(analyse(rows+[current],NOW),original)
        self.assertEqual(original['status'],'ready')
        self.assertTrue(0<=original['rsi14']<=100)
        self.assertGreater(original['atr14'],0)
        self.assertEqual(original['barId'],int(NOW.timestamp())-300)

    def test_gap_stale_invalid_and_future_candles(self):
        rows=candles()
        self.assertEqual(analyse(rows[:20],NOW)['status'],'warming')
        self.assertEqual(analyse(rows,NOW+timedelta(minutes=11))['status'],'stale')
        self.assertEqual(analyse(rows[:70]+rows[71:],NOW)['status'],'gap')
        for invalid in [
            rows+[[int(NOW.timestamp())+300,'1','2','1','1','0','1',1]],
            [[1,'1','2','1','1','0','1',1]],
            [[int(NOW.timestamp())-300,'1','0','1','1','0','1',1]],
        ]:
            with self.assertRaises(ValueError):
                analyse(invalid,NOW)

    def test_native_eur_indicators_do_not_depend_on_fx_or_poll_rate(self):
        e=Engine(new_state());e.now=NOW
        analysis=analyse(candles(),NOW)
        analysis['signals']['Trend']='BUY'
        e.s['marketAnalysis']={'BTC-SIM':analysis}
        agent=next(a for a in e.agents() if a['id']=='iota')
        self.assertEqual(e.signal(agent,'BTC-SIM'),'BUY')
        e.s['autoPace']=30000
        self.assertIsNone(e.signal(agent,'BTC-SIM'))
        self.assertEqual(len(e.s['lastDecisions']),1)

    def test_correlation_aligns_closed_bar_timestamps(self):
        left=analyse(candles(),NOW)
        right=analyse(candles(scale=3),NOW)
        self.assertAlmostEqual(correlation(left,right),1)
        right['candles']=[dict(row,time=row['time']+150) for row in right['candles']]
        self.assertEqual(correlation(left,right),1)  # Conservative unknown.

    def test_new_agent_can_leave_quarantine_without_missing_ledger_baseline(self):
        e=Engine(new_state())
        e.s['agentCount']=11
        agent=e.agents()[-1]
        e.init_agent(agent,True)
        self.assertEqual(e.s['book']['agentRealized']['extra11'],0)
        e.s['quarantine']['extra11'].update(until=0,trades=3,wins=3,pnl=10,maxDD=1)
        e.step({'BTC-SIM':600000,'ETH-SIM':25000},NOW)
        self.assertEqual(e.s['agentStatus']['extra11'],'AKTIV')
        self.assertTrue(e.audit())

    def test_real_connector_never_synthesizes_a_missing_crypto_book(self):
        from backend.broker import PaperBroker
        e=Engine(new_state())
        e.s['prices']['BTC-SIM']=[600000]
        e.s['marketData']['connector']='kraken-public'
        with self.assertRaises(ValueError):
            PaperBroker(e).quote('BTC-SIM','BUY')

    def test_public_refresh_changes_marks_without_trading_or_advancing_days(self):
        e=Engine(new_state())
        source=Public();prices=source.fetch()
        source.metadata['snapshotId']='refresh-only'
        e.now=datetime.now(timezone.utc)
        before=copy.deepcopy(e.s['book'])
        e.observe(prices,source.metadata)
        e.observe(prices,source.metadata)
        self.assertEqual(e.s['tick'],0)
        self.assertFalse(e.s['orders'])
        self.assertEqual(e.s['book'],before)


class Public:
    def __init__(self):
        self.metadata={}
        self.market_pause=None

    def fetch(self):
        now=datetime.now(timezone.utc).isoformat()
        rules={symbol:dict(pair=pair,base=base,quote='EUR',priceStep='0.1',quantityStep='0.00000001',
                          minimumVolume='0.00001',minimumCost='0.5',status='online')
               for symbol,pair,base in [('BTC-SIM','XBTEUR','XBT'),('ETH-SIM','ETHEUR','ETH')]}
        self.metadata=dict(connector='kraken-public',snapshotId=str(time.time_ns()),eurSek=10,fetchedAt=now,pairRules=rules,orderBooks={
            'BTC-SIM':dict(bids=[[599990,2]],asks=[[600010,2]]),
            'ETH-SIM':dict(bids=[[24990,20]],asks=[[25010,20]])})
        if self.market_pause:self.market_pause()
        return {'BTC-SIM':600000,'ETH-SIM':25000}


class Private:
    def __init__(self):
        self.calls=[]
        self.failure=None
        self.orders={}
        self.trades={}
        self.balance={'ZEUR':dict(balance='1000',hold_trade='0'),'XXBT':dict(balance='0',hold_trade='0'),'XETH':dict(balance='0',hold_trade='0')}

    def call(self,method,parameters=None):
        data=dict(parameters or {})
        self.calls.append((method,data))
        if method=='BalanceEx':return copy.deepcopy(self.balance)
        if method=='OpenOrders':return dict(open=copy.deepcopy(self.orders))
        if method=='ClosedOrders':return dict(closed={},count=0)
        if method=='QueryTrades':return {key:copy.deepcopy(self.trades[key]) for key in data['txid'].split(',') if key in self.trades}
        if method=='QueryOrders':return {data['txid']:copy.deepcopy(self.orders[data['txid']])} if data['txid'] in self.orders else {}
        if method=='CancelOrder':
            if self.failure=='cancel':raise ExchangeUnknown('timeout')
            self.orders[data['txid']]['status']='canceled'
            return dict(count=1)
        if method=='AddOrder':
            if data['validate']=='true':
                if self.failure=='validate':raise ExchangeUnknown('validation timeout')
                return dict(descr={'order':'validated'})
            if self.failure=='reject':raise ExchangeError('Rejected')
            self.orders['KRAKEN-ORDER']=dict(status='open',cl_ord_id=data['cl_ord_id'],
                vol=data['volume'],vol_exec='0',cost='0',fee='0',trades=[],
                descr=dict(pair=data['pair'],type=data['type'],ordertype='limit',price=data['price']))
            if self.failure=='timeout':raise ExchangeUnknown('timeout after send')
            return dict(txid=['KRAKEN-ORDER'])
        raise AssertionError(method)


class SigningTests(unittest.TestCase):
    def test_known_rejections_identify_operation_and_reason_without_raw_data(self):
        for code in ['EGeneral:Permission denied','EAPI:Invalid key','EAPI:Invalid signature','EAPI:Invalid nonce']:
            with self.subTest(code=code):
                def opener(request,timeout):
                    return io.BytesIO(json.dumps(dict(error=[code,'sensitive-api-secret'],result={})).encode())
                client=KrakenPrivate('/unused',lambda:123,opener,lambda _:('unit',bytes([1])*32))
                with self.assertRaises(ExchangeError) as caught:client.call('BalanceEx')
                self.assertIn('BalanceEx',str(caught.exception))
                self.assertIn(code,str(caught.exception))
                self.assertNotIn('sensitive-api-secret',str(caught.exception))

    def test_unknown_error_text_is_never_displayed(self):
        def opener(request,timeout):
            return io.BytesIO(json.dumps(dict(error=['EGeneral:secret-copied-from-request'],result={})).encode())
        client=KrakenPrivate('/unused',lambda:123,opener,lambda _:('unit',bytes([1])*32))
        with self.assertRaises(ExchangeError) as caught:client.call('OpenOrders')
        self.assertIn('OpenOrders',str(caught.exception))
        self.assertNotIn('secret-copied',str(caught.exception))

    def test_official_kraken_sdk_golden_signature_vector(self):
        secret=bytes([1])*32
        signature=api_signature(secret,1700000000000000000,'/0/private/Balance',b'nonce=1700000000000000000')
        self.assertEqual(signature,'6i7+cpXVLDDnDOekgsRcgFQhkYFyPDSSxhSVA8OrA6vjziXcy2K1DP3n7f9B/Kgni71WjBSQusZKxduRsdF20Q==')

    def test_private_client_signs_exact_body_never_retries_timeout(self):
        calls=[]
        def opener(request,timeout):
            calls.append(request)
            raise OSError('offline')
        client=KrakenPrivate('/unused',lambda:123,opener,lambda _:('test-key',bytes([1])*32))
        with self.assertRaises(ExchangeUnknown):
            client.call('AddOrder',dict(validate='false',pair='XBTEUR'))
        self.assertEqual(len(calls),1)
        request=calls[0]
        self.assertEqual(request.get_header('Api-sign'),api_signature(bytes([1])*32,123,'/0/private/AddOrder',request.data))
        self.assertNotIn(b'test-key',request.data)

    def test_credentials_file_permissions_and_missing_values(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict('os.environ',{},clear=True):
            path=Path(tmp)/'paper.db'
            with self.assertRaises(CredentialsMissing):load_credentials(path)
            file=Path(tmp)/'kraken.credentials.json'
            file.write_text(json.dumps(dict(apiKey='unit-key',apiSecret=base64.b64encode(bytes([1])*32).decode())))
            file.chmod(0o644)
            with self.assertRaises(CredentialsMissing):load_credentials(path)
            file.chmod(0o600)
            key,secret=load_credentials(path)
            self.assertEqual(key,'unit-key')
            self.assertEqual(secret,bytes([1])*32)

    def test_error_with_order_id_is_ambiguous(self):
        def opener(request,timeout):
            return io.BytesIO(json.dumps(dict(error=['EGeneral:unknown'],result=dict(txid=['ORDER']))).encode())
        client=KrakenPrivate('/unused',lambda:123,opener,lambda _:('unit',bytes([1])*32))
        with self.assertRaises(ExchangeUnknown):client.call('AddOrder',dict(validate='false'))

    def test_internal_exchange_error_never_proves_order_was_rejected(self):
        def opener(request,timeout):
            return io.BytesIO(json.dumps(dict(error=['EGeneral:Internal error'],result={})).encode())
        client=KrakenPrivate('/unused',lambda:123,opener,lambda _:('unit',bytes([1])*32))
        with self.assertRaises(ExchangeUnknown):client.call('AddOrder',dict(validate='false'))


class LiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.tmp.name)/'paper.db')
        self.public=Public();self.private=Private()
        self.live=LiveService(self.store,self.public,self.private,enabled=True)
        self.request=dict(pair='XBTEUR',side='buy',volume='0.0001',price='60000.0')

    def tearDown(self):
        self.store.close();self.tmp.cleanup()

    def prepared(self):
        ticket=self.live.run('preview',self.request,'preview')
        return self.live.run('validate',dict(ticket=ticket['id']),'validate')

    def approval(self,ticket,action='live-order'):
        return issue(self.store.operator_key,action,subject=fingerprint(ticket)+(':cancel' if action=='live-cancel' else ''))

    def sends(self):
        return [data for method,data in self.private.calls if method=='AddOrder' and data.get('validate')=='false']

    def test_preview_and_validation_never_place_or_change_paper_money(self):
        before=copy.deepcopy(self.store.state['book'])
        ticket=self.prepared()
        self.assertEqual(ticket['state'],'VALIDATED')
        self.assertFalse(self.sends())
        self.assertEqual(self.store.state['book'],before)
        for method,data in self.private.calls:
            if method=='AddOrder':self.assertEqual(data['validate'],'true')

    def test_enabled_signed_submit_exactly_once_and_reconcile(self):
        ticket=self.prepared();token=self.approval(ticket)
        data=dict(ticket=ticket['id'])
        first=self.live.run('submit',data,'send',token)
        self.assertEqual(first['state'],'ACKNOWLEDGED')
        self.assertEqual(self.live.run('submit',data,'send',token)['exchangeOrderId'],'KRAKEN-ORDER')
        self.assertEqual(len(self.sends()),1)
        self.assertEqual(self.sends()[0]['timeinforce'],'GTD')
        self.assertEqual(self.sends()[0]['expiretm'],'+60')
        self.assertNotIn('leverage',self.sends()[0])
        self.private.orders['KRAKEN-ORDER'].update(status='closed',vol_exec='0.0001',cost='6',fee='0.024',trades=['FILL-ID'])
        result=self.live.run('reconcile',data,'reconcile')
        self.assertEqual(result['state'],'CLOSED')
        self.assertEqual(result['feeEUR'],'0.024')

    def test_live_disabled_or_unsigned_never_sends(self):
        ticket=self.prepared();data=dict(ticket=ticket['id'])
        with self.assertRaises(ValueError):self.live.run('submit',data,'unsigned')
        self.live.enabled=False
        with self.assertRaises(ExchangeError):self.live.run('submit',data,'disabled',self.approval(ticket))
        self.assertFalse(self.sends())

    def test_timeout_is_unknown_and_never_resent_even_after_restart(self):
        ticket=self.prepared();data=dict(ticket=ticket['id'])
        self.private.failure='timeout'
        result=self.live.run('submit',data,'send',self.approval(ticket))
        self.assertEqual(result['state'],'UNKNOWN')
        self.live=LiveService(self.store,self.public,self.private,enabled=True)
        self.assertEqual(self.live.run('submit',data,'send')['state'],'UNKNOWN')
        with self.assertRaises(ExchangeError):self.live.run('submit',data,'retry',self.approval(ticket))
        self.assertEqual(len(self.sends()),1)
        with self.assertRaises(ExchangeError):self.live.run('preview',self.request,'second-preview')
        found=self.live.run('reconcile',data,'reconcile')
        self.assertEqual(found['state'],'OPEN')
        self.assertEqual(found['exchangeOrderId'],'KRAKEN-ORDER')

    def test_crash_after_send_keeps_durable_sending_then_unknown(self):
        ticket=self.prepared();original=self.live.save
        def crash(value,event):
            if event=='ACKNOWLEDGED':raise RuntimeError('simulated hard crash before ack commit')
            return original(value,event)
        self.live.save=crash
        with self.assertRaises(RuntimeError):self.live.run('submit',dict(ticket=ticket['id']),'send',self.approval(ticket))
        restored=LiveService(self.store,self.public,self.private,enabled=True)
        self.assertEqual(restored.get(ticket['id'])['state'],'UNKNOWN')
        self.assertEqual(restored.run('submit',dict(ticket=ticket['id']),'send')['state'],'UNKNOWN')
        self.assertEqual(len(self.sends()),1)

    def test_stop_or_risk_pause_blocks_send_even_during_preflight(self):
        ticket=self.prepared()
        self.public.market_pause=lambda:self.store.change('stop',lambda e:e.command('stop',{}))
        with self.assertRaises(ExchangeError):self.live.run('submit',dict(ticket=ticket['id']),'stop-race',self.approval(ticket))
        self.assertFalse(self.sends())

    def test_invalid_steps_insufficient_cash_limit_and_exposure(self):
        for change in [dict(volume='0.000100001'),dict(price='60000.01'),dict(volume='1'),dict(pair='BTCUSD'),dict(volume='NaN')]:
            with self.subTest(change=change),self.assertRaises(ExchangeError):
                self.live.run('preview',{**self.request,**change},'bad-'+str(change))
        self.private.balance['ZEUR']['hold_trade']='999'
        with self.assertRaises(ExchangeError):self.live.run('preview',self.request,'no-cash')
        self.private.balance['ZEUR']['hold_trade']='0'
        self.private.balance['XXBT']['balance']='0.1'
        with self.assertRaises(ExchangeError):self.live.run('preview',self.request,'too-exposed')

    def test_signed_cancel_queries_final_exchange_state(self):
        ticket=self.prepared();data=dict(ticket=ticket['id'])
        self.live.run('submit',data,'send',self.approval(ticket))
        cancelled=self.live.run('cancel',data,'cancel',self.approval(ticket,'live-cancel'))
        self.assertEqual(cancelled['state'],'CANCEL_REQUESTED')
        self.assertEqual(self.live.run('reconcile',data,'check-cancel')['state'],'CANCELLED')

    def test_unknown_not_found_does_not_allow_new_order(self):
        ticket=self.prepared();data=dict(ticket=ticket['id'])
        self.private.failure='timeout'
        self.live.run('submit',data,'send',self.approval(ticket))
        self.private.orders={}
        result=self.live.run('reconcile',data,'look-again')
        self.assertEqual(result['state'],'UNKNOWN')
        self.assertEqual(len(self.sends()),1)

    def test_validation_timeout_can_be_revalidated_but_not_submitted(self):
        ticket=self.live.run('preview',self.request,'preview')
        self.private.failure='validate'
        with self.assertRaises(ExchangeUnknown):self.live.run('validate',dict(ticket=ticket['id']),'validate')
        self.assertEqual(self.live.get(ticket['id'])['state'],'VALIDATION_FAILED')
        self.assertFalse(self.sends())
        self.private.failure=None
        result=self.live.run('validate',dict(ticket=ticket['id']),'retry-validation')
        self.assertEqual(result['state'],'VALIDATED')

    def test_corrupted_ticket_cannot_use_old_approval(self):
        ticket=self.prepared()
        changed=dict(ticket,volume='1')
        with self.store.lock,self.store.db:
            self.store.db.execute('UPDATE live_tickets SET payload=? WHERE id=?',(json.dumps(changed),ticket['id']))
        with self.assertRaises(ExchangeError):self.live.run('submit',dict(ticket=ticket['id']),'corrupt',self.approval(ticket))
        self.assertFalse(self.sends())

    def test_monotonic_nonce_survives_wall_clock_rollback(self):
        with patch('backend.live.time.time_ns',return_value=2000000):
            a=self.live.nonce()
        with patch('backend.live.time.time_ns',return_value=1000000):
            b=self.live.nonce()
        self.assertGreater(b,a)

    def test_partial_fill_and_mismatched_exchange_order(self):
        ticket=self.prepared();data=dict(ticket=ticket['id'])
        self.live.run('submit',data,'send',self.approval(ticket))
        self.private.orders['KRAKEN-ORDER'].update(vol_exec='0.00005',cost='3',fee='0.012')
        self.assertEqual(self.live.run('reconcile',data,'partial')['state'],'PARTIAL')
        self.private.orders['KRAKEN-ORDER']['descr']['price']='1'
        self.assertEqual(self.live.run('reconcile',data,'mismatch')['state'],'UNKNOWN')
        with self.assertRaises(ExchangeError):self.live.run('preview',self.request,'another')

    def test_sell_requires_actual_unreserved_crypto_and_does_not_short(self):
        self.request['side']='sell'
        with self.assertRaises(ExchangeError):self.live.run('preview',self.request,'no-holdings')
        self.private.balance['XXBT']['balance']='0.0002'
        ticket=self.prepared()
        self.store.state['halt']=True
        self.assertEqual(self.live.run('submit',dict(ticket=ticket['id']),'sell',self.approval(ticket))['state'],'ACKNOWLEDGED')

    def test_ethereum_native_pair_and_idempotency_conflict(self):
        self.request=dict(pair='ETHEUR',side='buy',volume='0.002',price='2500.0')
        ticket=self.prepared()
        self.live.run('submit',dict(ticket=ticket['id']),'send',self.approval(ticket))
        self.assertEqual(self.sends()[0]['pair'],'ETHEUR')
        with self.assertRaises(ExchangeError):self.live.run('check',{},'send')


class MonitorTests(unittest.TestCase):
    def test_monitor_runs_without_browser_or_funds_and_blocks_order_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'paper.db')
            with patch.dict('os.environ',{'JARVIS_MONITOR_ONLY':'1','JARVIS_KRAKEN_LIVE':'1'}):
                runtime=Runtime(store,Public())
            private=Private();private.balance['ZEUR']['balance']='0'
            runtime.exchange.private=private
            try:
                runtime.start()
                deadline=time.monotonic()+3
                while runtime.exchange.last_account is None and time.monotonic()<deadline:
                    time.sleep(.01)
                self.assertIsNotNone(runtime.exchange.last_account)
                self.assertEqual(store.state['marketData']['connector'],'kraken-public')
                self.assertEqual(store.state['tick'],0)
                self.assertFalse(store.state['orders'])
                self.assertFalse(store.state['running'])
                self.assertFalse(runtime.exchange.enabled)
                self.assertEqual({method for method,_ in private.calls},{'BalanceEx','OpenOrders'})
                for action in ('start','step'):
                    with self.assertRaises(ValueError):runtime.command(action,{},'blocked-'+action)
                for action in ('preview','validate','submit'):
                    with self.assertRaises(ExchangeError):runtime.exchange.run(action,{},'blocked-'+action)
            finally:
                runtime.stop();store.close()

    def test_market_outage_does_not_stop_account_monitor(self):
        from backend.market import MarketError
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'paper.db')
            with patch.dict('os.environ',{'JARVIS_MONITOR_ONLY':'1'}):
                runtime=Runtime(store,Public())
            runtime.market.fetch=lambda:(_ for _ in ()).throw(MarketError('offline'))
            runtime.exchange.private=Private()
            try:
                runtime.start()
                deadline=time.monotonic()+3
                while runtime.exchange.last_account is None and time.monotonic()<deadline:
                    time.sleep(.01)
                self.assertIsNotNone(runtime.exchange.last_account)
                self.assertEqual(store.state['tick'],0)
            finally:
                runtime.stop();store.close()


if __name__=='__main__':
    unittest.main()
