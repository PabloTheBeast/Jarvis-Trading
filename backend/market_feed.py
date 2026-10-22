"""Native-EUR display feed, independent of trading signals and ECB conversion.

Ticker ~3s, OHLC ~15s, optional live BBO overlay. No private API or order access.
"""
import copy
import math
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal

from .market import Kraken, PAIRS, pair_result


def utc(): return datetime.now(timezone.utc)


def number(value, zero=False):
    n=Decimal(str(value))
    if not n.is_finite() or (n<0 if zero else n<=0):raise ValueError('Invalid market number')
    return n


def age(stamp, at):
    try:
        seconds=(at-datetime.fromisoformat(stamp)).total_seconds()
        return seconds if seconds>=0 else None
    except (ValueError,TypeError):return None


class MarketFeed:
    def __init__(self, stream, client=None, enabled=True):
        self.stream=stream;self.client=client or Kraken();self.enabled=enabled
        self.lock=threading.RLock();self.quit=threading.Event();self.wake=threading.Event();self.thread=None
        self.tickers={};self.candles={};self.ticker_error=None;self.candle_errors={};self.last_manual=0

    def ticker(self):
        raw=self.client.public('Ticker','pair=XBTEUR,ETHEUR');checked=utc().isoformat();result={}
        for symbol,pair in PAIRS.items():
            row=pair_result(raw,pair)
            bid=number(row['b'][0]);ask=number(row['a'][0]);last=number(row['c'][0])
            high=number(row['h'][1]);low=number(row['l'][1]);volume=number(row['v'][1],True)
            vwap=number(row['p'][1]);opening=number(row['o'])
            if bid>ask or ask/bid>Decimal('1.05') or low>high or not low<=last<=high:raise ValueError('Invalid ticker range/spread')
            result[symbol]=dict(pair='BTC/EUR' if symbol=='BTC-SIM' else 'ETH/EUR',bidEUR=str(bid),askEUR=str(ask),lastEUR=str(last),
                high24hEUR=str(high),low24hEUR=str(low),volume24h=str(volume),vwap24hEUR=str(vwap),
                openTodayEUR=str(opening),changeTodayPercent=str((last/opening-1)*100),checkedAt=checked)
        with self.lock:self.tickers=result;self.ticker_error=None

    def history(self, symbol):
        at=utc();raw=self.client.public('OHLC','pair='+PAIRS[symbol]+'&interval=5&since='+str(int(at.timestamp())-86400-300))
        rows=pair_result(raw,PAIRS[symbol]);candles=[];previous=None
        if not isinstance(rows,list) or not rows:raise ValueError('No OHLC data')
        for row in rows:
            stamp=int(row[0])
            opening,high,low,close=[number(row[i]) for i in (1,2,3,4)]
            volume=number(row[6],True)
            if stamp%300 or stamp>at.timestamp()+5 or previous is not None and stamp<=previous:raise ValueError('Invalid OHLC time')
            if not low<=min(opening,close)<=max(opening,close)<=high:raise ValueError('Invalid OHLC range')
            candles.append(dict(time=stamp,open=float(opening),high=float(high),low=float(low),close=float(close),volume=float(volume),closed=stamp+300<=at.timestamp()))
            previous=stamp
        if any(not math.isfinite(c[k]) for c in candles for k in ('open','high','low','close','volume')):raise ValueError('OHLC overflow')
        with self.lock:
            self.candles[symbol]=dict(rows=candles[-290:],checkedAt=utc().isoformat());self.candle_errors.pop(symbol,None)

    def status(self, at=None):
        at=at or utc()
        with self.lock:
            tickers=copy.deepcopy(self.tickers);candles=copy.deepcopy(self.candles)
            ticker_error=self.ticker_error;candle_errors=dict(self.candle_errors)
        stream=self.stream.status();instruments=[]
        for symbol,pair in PAIRS.items():
            ticker=tickers.get(symbol);name='BTC/EUR' if symbol=='BTC-SIM' else 'ETH/EUR'
            tick_age=age((ticker or {}).get('checkedAt'),at)
            rest_fresh=ticker is not None and ticker_error is None and tick_age is not None and tick_age<=10
            quote=stream['quotes'].get(name);stream_age=age((quote or {}).get('at'),at)
            streamed=bool(stream['connected'] and stream_age is not None and stream_age<=5)
            chart=candles.get(symbol,{});chart_age=age(chart.get('checkedAt'),at)
            rows=chart.get('rows',[])
            for c in rows:c['closed']=c['time']+300<=at.timestamp()
            last_age=at.timestamp()-rows[-1]['time'] if rows else None
            chart_fresh=bool(chart_age is not None and chart_age<=45 and last_age is not None and 0<=last_age<600 and not candle_errors.get(symbol))
            instruments.append(dict(symbol=symbol,pair=name,ticker=ticker,statsFresh=rest_fresh,
                quote=dict(bidEUR=quote['bid'],askEUR=quote['ask'],checkedAt=quote['at'],source='WebSocket',fresh=True) if streamed else
                    dict(bidEUR=(ticker or {}).get('bidEUR'),askEUR=(ticker or {}).get('askEUR'),checkedAt=(ticker or {}).get('checkedAt'),source='REST',fresh=rest_fresh),
                candles=rows,chartCheckedAt=chart.get('checkedAt'),chartFresh=chart_fresh,
                gaps=sum(b['time']-a['time']!=300 for a,b in zip(rows,rows[1:])),
                error=ticker_error,chartError=candle_errors.get(symbol),trades=copy.deepcopy(stream['trades'].get(name,[]))[-15:][::-1]))
        return dict(serverTime=at.isoformat(),enabled=self.enabled,currency='EUR',source='Kraken spot',
            tickerSeconds=3,chartSeconds=15,browserSeconds=1,instruments=instruments,stream=stream,
            semantics='24h high/low är rullande 24 timmar. Förändring i dag räknas från 00:00 UTC. Grafen inkluderar en separat markerad pågående femminutersstapel.')

    def request_refresh(self):
        with self.lock:
            if time.monotonic()-self.last_manual>=3:
                self.last_manual=time.monotonic();self.wake.set()

    def start(self):
        if self.enabled:
            self.thread=threading.Thread(target=self.loop,daemon=True);self.thread.start()

    def loop(self):
        ticker_due=chart_due=0;backoff=3
        while not self.quit.is_set():
            if self.wake.is_set():self.wake.clear();ticker_due=chart_due=0
            if time.monotonic()>=ticker_due:
                try:self.ticker();backoff=3
                except Exception:
                    with self.lock:self.ticker_error='Krakens ticker kunde inte verifieras. Senast kända data markeras.'
                    backoff=min(30,backoff*2)
                ticker_due=time.monotonic()+backoff
            if not self.quit.is_set() and time.monotonic()>=chart_due:
                for symbol in PAIRS:
                    if self.quit.is_set():break
                    try:self.history(symbol)
                    except Exception:
                        with self.lock:self.candle_errors[symbol]='Grafdata kunde inte verifieras.'
                chart_due=time.monotonic()+15
            self.quit.wait(.25)

    def stop(self):
        self.quit.set()
        if self.thread:
            self.thread.join(25)
            if self.thread.is_alive():raise RuntimeError('Market display worker did not stop')
