import copy
import unittest
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
from backend.market_feed import MarketFeed
from backend.stream import MarketStream

class FeedClient:
    def __init__(self):self.calls=[];self.failure=False;self.invalid=False
    def public(self,method,query):
        self.calls.append((method,query))
        if self.failure:raise OSError('offline')
        if method=='Ticker':
            result={}
            for pair,p in [('XXBTZEUR',100),('XETHZEUR',2000)]:
                result[pair]=dict(b=[str(p-1)],a=[str(p+1)],c=[str(p)],h=[str(p+5),str(p+20)],l=[str(p-5),str(p-20)],
                    v=['10','40'],p=[str(p),str(p+1)],o=str(p-10))
            if self.invalid:result['XXBTZEUR']['l'][1]='NaN'
            return result
        end=int(datetime.now(timezone.utc).timestamp()//300)*300
        rows=[[end-(288-i)*300,'100','102','98','100','100','1',1] for i in range(289)]
        if self.invalid:rows[-1][2]='90'
        pair='XXBTZEUR' if 'XBTEUR' in query else 'XETHZEUR'
        return {pair:rows,'last':end}

class MarketFeedTests(unittest.TestCase):
    def setUp(self):self.client=FeedClient();self.stream=MarketStream();self.feed=MarketFeed(self.stream,self.client)
    def test_native_eur_rolling_high_low_are_not_today_indices(self):
        self.feed.ticker();row=self.feed.status()['instruments'][0]
        self.assertEqual(row['ticker']['high24hEUR'],'120');self.assertEqual(row['ticker']['low24hEUR'],'80')
        self.assertEqual(row['ticker']['volume24h'],'40');self.assertEqual(row['ticker']['openTodayEUR'],'90')
        self.assertTrue(row['quote']['fresh']);self.assertEqual(self.client.calls[0][0],'Ticker')
        self.assertEqual(len(self.client.calls),1) # no ECB, credentials or private order call
    def test_ohlc_includes_current_bar_and_preserves_real_high_low(self):
        self.feed.history('BTC-SIM');row=self.feed.status()['instruments'][0]
        self.assertEqual(len(row['candles']),289);self.assertFalse(row['candles'][-1]['closed'])
        self.assertEqual(row['candles'][-1]['high'],102);self.assertEqual(row['candles'][-1]['low'],98)
        self.assertTrue(row['chartFresh']);self.assertEqual(row['gaps'],0)
    def test_stale_and_future_snapshots_are_never_live(self):
        self.feed.ticker();self.feed.history('BTC-SIM')
        later=self.feed.status(datetime.now(timezone.utc)+timedelta(seconds=50))['instruments'][0]
        self.assertFalse(later['quote']['fresh']);self.assertFalse(later['statsFresh']);self.assertFalse(later['chartFresh'])
        earlier=self.feed.status(datetime.now(timezone.utc)-timedelta(days=1))['instruments'][0]
        self.assertFalse(earlier['quote']['fresh']);self.assertFalse(earlier['chartFresh'])
    def test_quote_overlay_requires_current_connected_stream_and_does_not_fake_statistics(self):
        self.feed.ticker();self.stream.connected=True
        self.stream.ingest(dict(channel='ticker',data=[dict(symbol='BTC/EUR',bid='101',ask='102')]))
        row=self.feed.status()['instruments'][0]
        self.assertEqual(row['quote']['source'],'WebSocket');self.assertEqual(row['quote']['bidEUR'],'101')
        self.assertEqual(row['ticker']['high24hEUR'],'120')
        self.stream.connected=False
        self.assertEqual(self.feed.status()['instruments'][0]['quote']['source'],'REST')
    def test_malformed_data_never_overwrites_verified_snapshot(self):
        self.feed.ticker();self.feed.history('BTC-SIM');before=copy.deepcopy(self.feed.tickers)
        self.client.invalid=True
        with self.assertRaises(ValueError):self.feed.ticker()
        with self.assertRaises(ValueError):self.feed.history('BTC-SIM')
        self.assertEqual(self.feed.tickers,before)
    def test_missing_ticker_or_ohlc_and_network_failure_not_synthetic(self):
        data=self.feed.status()['instruments'][0]
        self.assertIsNone(data['ticker']);self.assertFalse(data['chartFresh']);self.assertFalse(data['quote']['fresh'])
        self.client.failure=True
        with self.assertRaises(OSError):self.feed.ticker()
        self.assertIsNone(self.feed.status()['instruments'][0]['quote']['bidEUR'])
    def test_get_status_has_no_network_side_effect_and_refresh_is_coalesced(self):
        self.feed.status();self.assertFalse(self.client.calls)
        self.feed.request_refresh();self.assertTrue(self.feed.wake.is_set())
        self.feed.wake.clear();self.feed.request_refresh();self.assertFalse(self.feed.wake.is_set())
    def test_current_bar_becomes_closed_without_fabricating_new_bar(self):
        self.feed.history('BTC-SIM');rows=self.feed.candles['BTC-SIM']['rows']
        future=datetime.fromtimestamp(rows[-1]['time']+301,timezone.utc)
        data=self.feed.status(future)['instruments'][0]
        self.assertTrue(data['candles'][-1]['closed']);self.assertEqual(len(data['candles']),len(rows))

if __name__=='__main__':unittest.main()
