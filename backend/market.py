"""Public Kraken EUR quotes, closed OHLC, pair rules and ECB SEK translation."""
import json
import math
import time
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from urllib.request import urlopen
from xml.etree import ElementTree

from .analysis import analyse

PAIRS={'BTC-SIM':'XBTEUR','ETH-SIM':'ETHEUR'}


class MarketError(Exception):
    pass


def canonical(value):
    return value.replace('XXBT','XBT').replace('XETH','ETH').replace('ZEUR','EUR')


def pair_result(result,pair):
    matches=[value for key,value in result.items() if canonical(key)==pair]
    if len(matches)!=1:
        raise ValueError('Missing or ambiguous Kraken pair')
    return matches[0]


class Kraken:
    url='https://api.kraken.com/0/public/Ticker?pair=XBTEUR,ETHEUR'
    fx_url='https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml'

    def __init__(self,opener=urlopen):
        self.opener=opener
        self.fx=None
        self.fx_checked=0
        self.metadata={}
        self._rules=None
        self._rules_checked=0
        self._candles={}
        self._candles_checked={}
        self._candles_bucket={}

    def public(self,method,query):
        with self.opener('https://api.kraken.com/0/public/'+method+'?'+query,timeout=10) as response:
            payload=json.load(response)
        if payload.get('error') or not isinstance(payload.get('result'),dict):
            raise ValueError('Kraken '+method+' returned an error')
        return payload['result']

    def rules(self,force=False):
        if not force and self._rules and time.monotonic()-self._rules_checked<21600:
            return dict(self._rules)
        result=self.public('AssetPairs','pair=XBTEUR,ETHEUR')
        rules={}
        for symbol,pair in PAIRS.items():
            raw=pair_result(result,pair)
            price_step=Decimal(str(raw.get('tick_size',Decimal(10)**-int(raw['pair_decimals']))))
            quantity_step=Decimal(10)**-int(raw['lot_decimals'])
            minimum=Decimal(str(raw['ordermin']))
            cost=Decimal(str(raw.get('costmin','0')))
            if any(not x.is_finite() or x<=0 for x in (price_step,quantity_step,minimum)) or not cost.is_finite() or cost<0:
                raise ValueError('Invalid exchange precision/minimums')
            rules[symbol]=dict(pair=pair,base='XBT' if symbol=='BTC-SIM' else 'ETH',quote='EUR',
                priceStep=str(price_step),quantityStep=str(quantity_step),minimumVolume=str(minimum),
                minimumCost=str(cost),status=raw.get('status','unknown'))
        self._rules,self._rules_checked=rules,time.monotonic()
        return dict(rules)

    def candles(self,symbol,now):
        bucket=int(now.timestamp())//300
        if symbol not in self._candles or self._candles_bucket.get(symbol)!=bucket or time.monotonic()-self._candles_checked.get(symbol,0)>=30:
            result=self.public('OHLC','pair='+PAIRS[symbol]+'&interval=5')
            rows=pair_result(result,PAIRS[symbol])
            # Validate the entire response before retaining it.
            analyse(rows,now)
            self._candles[symbol]=rows
            self._candles_checked[symbol]=time.monotonic()
            self._candles_bucket[symbol]=bucket
        return analyse(self._candles[symbol],now)

    def exchange_rate(self):
        today=date.today()
        if self.fx and time.monotonic()-self.fx_checked<3600 and 0<=(today-date.fromisoformat(self.fx[1])).days<=7:
            return self.fx
        with self.opener(self.fx_url,timeout=10) as response:
            root=ElementTree.fromstring(response.read())
        dated=[node for node in root.iter() if 'time' in node.attrib]
        if len(dated)!=1:
            raise ValueError('Missing ECB date')
        day=date.fromisoformat(dated[0].attrib['time'])
        if not 0<=(today-day).days<=7:
            raise ValueError('ECB reference rate is stale')
        rates=[float(node.attrib['rate']) for node in dated[0] if node.attrib.get('currency')=='SEK']
        if len(rates)!=1 or not math.isfinite(rates[0]) or rates[0]<=0:
            raise ValueError('Invalid ECB SEK rate')
        self.fx,self.fx_checked=(rates[0],day.isoformat()),time.monotonic()
        return self.fx

    def fetch(self):
        try:
            rate,day=self.exchange_rate()
            rules=self.rules()
            analysis={symbol:self.candles(symbol,datetime.now(timezone.utc)) for symbol in PAIRS}
            tickers=self.public('Ticker','pair=XBTEUR,ETHEUR')
            books,prices,received,last_trade={},{},[],{}
            for symbol,pair in PAIRS.items():
                ticker=pair_result(tickers,pair)
                trade=float(ticker['c'][0])
                if not math.isfinite(trade) or trade<=0:
                    raise ValueError('Invalid Kraken last trade')
                last_trade[symbol]=trade
                depth=self.public('Depth','pair='+pair+'&count=10')
                received.append(datetime.now(timezone.utc))
                raw=pair_result(depth,pair)
                book={}
                for side in ('bids','asks'):
                    levels=[[float(row[0])*rate,float(row[1])] for row in raw[side]]
                    if not levels or any(not math.isfinite(p) or not math.isfinite(q) or p<=0 or q<=0 for p,q in levels):
                        raise ValueError('Invalid Kraken depth')
                    if levels!=sorted(levels,key=lambda row:row[0],reverse=side=='bids'):
                        raise ValueError('Unsorted Kraken depth')
                    book[side]=levels
                if book['bids'][0][0]>=book['asks'][0][0] or book['asks'][0][0]/book['bids'][0][0]-1>.05:
                    raise ValueError('Crossed or excessive-spread Kraken depth')
                books[symbol]=book
                # Midpoint of a current book, not a possibly old last trade.
                prices[symbol]=(book['bids'][0][0]+book['asks'][0][0])/2
            now=datetime.now(timezone.utc)
            if (max(received)-min(received)).total_seconds()>15:
                raise ValueError('Kraken book snapshots are too far apart')
            self.metadata=dict(connector='kraken-public',orderBooks=books,analysis=analysis,pairRules=rules,
                snapshotId=uuid.uuid4().hex,fetchedAt=min(received).isoformat(),receivedAt=now.isoformat(),
                fxSource='ECB daily reference',eurSek=rate,fxDate=day,
                quoteCurrency='EUR',accountCurrency='SEK',lastTradeEUR=last_trade)
            return prices
        except Exception as exc:
            raise MarketError('Kraken/ECB market data unavailable or invalid') from exc
