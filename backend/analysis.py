"""Deterministic analysis of closed native-EUR five-minute candles.

Indicators describe historical observations, not probabilities or return promises.
"""
import math
from datetime import datetime, timezone

INTERVAL = 300


def analyse(rows, now=None):
    now = now or datetime.now(timezone.utc)
    candles = []
    previous = None
    for row in rows:
        stamp = int(row[0])
        opening, high, low, close, volume = map(float, (row[1], row[2], row[3], row[4], row[6]))
        if stamp % INTERVAL or previous is not None and stamp <= previous:
            raise ValueError('Unaligned or unordered OHLC candles')
        if stamp > now.timestamp()+5:
            raise ValueError('Future OHLC timestamp')
        if any(not math.isfinite(v) or v <= 0 for v in [opening, high, low, close]):
            raise ValueError('Invalid OHLC price')
        if not math.isfinite(volume) or volume < 0 or not low <= min(opening,close) <= max(opening,close) <= high:
            raise ValueError('Invalid OHLC range/volume')
        previous = stamp
        if stamp+INTERVAL <= now.timestamp():
            candles.append(dict(time=stamp, open=opening, high=high, low=low, close=close, volume=volume))
    candles = candles[-120:]
    result = dict(status='warming', intervalSeconds=INTERVAL, currency='EUR', source='Kraken OHLC',
                  candles=candles, count=len(candles), barId=None, signals={}, reasons={})
    if len(candles)<60:
        result['reason']='Minst 60 avslutade femminutersstaplar krävs.'
        return result
    if any(b['time']-a['time']!=INTERVAL for a,b in zip(candles[-60:],candles[-59:])):
        result.update(status='gap',reason='Lucka i den senaste femtimmarshistoriken.')
        return result
    last=candles[-1]
    result.update(barId=last['time'], closedAt=datetime.fromtimestamp(last['time']+INTERVAL,timezone.utc).isoformat())
    if now.timestamp()-(last['time']+INTERVAL)>INTERVAL*2:
        result.update(status='stale',reason='Senaste avslutade femminutersstapeln är för gammal.')
        return result
    closes=[r['close'] for r in candles]
    sma20, sma50=sum(closes[-20:])/20,sum(closes[-50:])/50
    differences=[b-a for a,b in zip(closes,closes[1:])]
    gain=sum(max(0,d) for d in differences[:14])/14
    loss=sum(max(0,-d) for d in differences[:14])/14
    for change in differences[14:]:
        gain=(gain*13+max(0,change))/14
        loss=(loss*13+max(0,-change))/14
    rsi=50 if gain==loss==0 else 100 if loss==0 else 100-100/(1+gain/loss)
    ranges=[max(row['high']-row['low'],abs(row['high']-prev['close']),abs(row['low']-prev['close']))
            for prev,row in zip(candles,candles[1:])]
    atr=sum(ranges[:14])/14
    for value in ranges[14:]:
        atr=(atr*13+value)/14
    momentum=closes[-1]/closes[-6]-1
    trend='upptrend' if sma20>sma50*1.002 else 'nedtrend' if sma20<sma50*.998 else 'sidledes'
    signals=dict(Trend=None,Momentum=None,**{'Mean reversion':None})
    if sma20>sma50 and closes[-1]>sma20 and rsi<70:
        signals['Trend']='BUY'
    elif closes[-1]<sma20:
        signals['Trend']='SELL'
    if momentum>max(.003,atr/closes[-1]) and rsi<75:
        signals['Momentum']='BUY'
    elif momentum<-.003:
        signals['Momentum']='SELL'
    if rsi<30 and closes[-1]<sma20:
        signals['Mean reversion']='BUY'
    elif rsi>55 and closes[-1]>sma20:
        signals['Mean reversion']='SELL'
    result.update(status='ready',reason='Analys av avslutade staplar; nya beslut högst en gång per stapel och agent.',
                  sma20=sma20,sma50=sma50,rsi14=rsi,atr14=atr,atrPercent=atr/closes[-1]*100,
                  momentum25m=momentum,trend=trend,close=closes[-1],signals=signals,
                  reasons={name:f'{name}: SMA20 {sma20:.2f}, SMA50 {sma50:.2f}, RSI14 {rsi:.1f}, momentum {momentum*100:.2f} %'
                           for name in signals})
    return result


def correlation(left,right):
    a={r['time']:r['close'] for r in left.get('candles',[])}
    b={r['time']:r['close'] for r in right.get('candles',[])}
    stamps=sorted(a.keys() & b.keys())[-31:]
    if len(stamps)<13 or any(y-x!=INTERVAL for x,y in zip(stamps,stamps[1:])):
        return 1.0  # Unknown alignment is conservatively treated as shared exposure.
    u=[a[y]/a[x]-1 for x,y in zip(stamps,stamps[1:])]
    v=[b[y]/b[x]-1 for x,y in zip(stamps,stamps[1:])]
    mu,mv=sum(u)/len(u),sum(v)/len(v)
    aa,bb=sum((x-mu)**2 for x in u),sum((y-mv)**2 for y in v)
    return sum((x-mu)*(y-mv) for x,y in zip(u,v))/math.sqrt(aa*bb) if aa*bb else 1.0
