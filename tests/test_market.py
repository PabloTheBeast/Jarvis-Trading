import io
import json
import unittest
from datetime import date, timedelta
import time

from backend.market import Kraken, MarketError


class MarketTests(unittest.TestCase):
    def opener(self, payload=None, day=None):
        xml = ('<Envelope><Cube><Cube time=\"'+(day or date.today().isoformat())+'\">'
               '<Cube currency=\"SEK\" rate=\"11.1\"/></Cube></Cube></Envelope>').encode()
        def open_url(url, timeout):
            self.assertEqual(timeout, 10)
            self.assertTrue(url.startswith('https://'))
            if url == Kraken.fx_url:
                return io.BytesIO(xml)
            if '/AssetPairs?' in url:
                return io.BytesIO(json.dumps(dict(error=[],result={pair:dict(pair_decimals=1,lot_decimals=8,
                    tick_size='0.1',ordermin='0.00005',costmin='0.5',status='online') for pair in ('XXBTZEUR','XETHZEUR')})).encode())
            if '/OHLC?' in url:
                pair='XXBTZEUR' if 'XBTEUR' in url else 'XETHZEUR'
                value=60000 if 'XBTEUR' in url else 2500
                start=int(time.time()//300)*300-70*300
                rows=[[start+i*300,str(value),str(value+1),str(value-1),str(value),str(value),'1',1] for i in range(70)]
                return io.BytesIO(json.dumps(dict(error=[],result={pair:rows,'last':start+70*300})).encode())
            if '/Depth?' in url:
                pair='XXBTZEUR' if 'XBTEUR' in url else 'XETHZEUR'
                value=60000 if 'XBTEUR' in url else 2500
                return io.BytesIO(json.dumps(dict(error=[],result={pair:dict(bids=[[str(value-1),'2','1']],asks=[[str(value+1),'2','1']])})).encode())
            return io.BytesIO(json.dumps(payload if payload is not None else dict(error=[], result={
                'XXBTZEUR':dict(c=['60000','1']), 'XETHZEUR':dict(c=['2500','1'])})).encode())
        return open_url

    def test_canonical_kraken_pairs_and_ecb_conversion(self):
        market = Kraken(self.opener())
        self.assertEqual(market.fetch(), {'BTC-SIM':666000, 'ETH-SIM':27750})
        self.assertEqual(market.metadata['fxDate'], date.today().isoformat())
        self.assertEqual(market.metadata['eurSek'], 11.1)

    def test_reject_errors_incomplete_nonfinite_and_invalid_prices(self):
        for payload in [
            dict(error=['EQuery:Unknown asset pair']),
            dict(error=[], result={}),
            dict(error=[], result={'XBTEUR':dict(c=['NaN']), 'ETHEUR':dict(c=['2500'])}),
            dict(error=[], result={'XBTEUR':dict(c=['-1']), 'ETHEUR':dict(c=['2500'])}),
        ]:
            with self.subTest(payload=payload), self.assertRaises(MarketError):
                Kraken(self.opener(payload)).fetch()

    def test_reject_stale_and_future_fx(self):
        for delta in [-8, 1]:
            with self.assertRaises(MarketError):
                Kraken(self.opener(day=(date.today()+timedelta(days=delta)).isoformat())).fetch()

    def test_transport_failure_never_falls_back_to_synthetic_crypto(self):
        def fail(*args, **kwargs):
            raise OSError('offline')
        with self.assertRaises(MarketError):
            Kraken(fail).fetch()


if __name__ == '__main__':
    unittest.main()
