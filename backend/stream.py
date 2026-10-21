"""Kraken v2 ticker/trade stream. REST remains the authoritative order preflight.

Optional websockets dependency. No credentials or execution authority here.
A disconnected/stale enabled stream blocks automatic new buys, never sells.
"""
import json
import os
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal


class MarketStream:
    def __init__(self):
        self.enabled=os.environ.get('JARVIS_KRAKEN_STREAM')=='1'
        self.lock=threading.RLock();self.quit=threading.Event();self.thread=None
        self.quotes={};self.trades={};self.connected=False;self.error=None;self.socket=None

    def ingest(self, message, received=None):
        received=time.monotonic() if received is None else received
        if not isinstance(message,dict) or message.get('channel') not in ('ticker','trade'):
            return
        for row in message.get('data',[]):
            symbol=row.get('symbol')
            if symbol not in ('BTC/EUR','ETH/EUR'):continue
            if message['channel']=='ticker':
                bid=Decimal(str(row['bid']));ask=Decimal(str(row['ask']))
                if not bid.is_finite() or not ask.is_finite() or not 0<bid<=ask or ask/bid>Decimal('1.05'):
                    raise ValueError('Invalid streaming market')
                with self.lock:self.quotes[symbol]=dict(bid=str(bid),ask=str(ask),received=received,at=datetime.now(timezone.utc).isoformat())
            else:
                price=Decimal(str(row['price']));qty=Decimal(str(row['qty']))
                if not price.is_finite() or not qty.is_finite() or price<=0 or qty<=0:raise ValueError('Invalid stream trade')
                with self.lock:
                    self.trades.setdefault(symbol,[]).append(dict(price=str(price),quantity=str(qty),side=row.get('side'),at=row.get('timestamp')))
                    del self.trades[symbol][:-30]

    def healthy(self):
        with self.lock:
            return self.connected and all(symbol in self.quotes and 0<=time.monotonic()-self.quotes[symbol]['received']<=15 for symbol in ('BTC/EUR','ETH/EUR'))

    def status(self):
        with self.lock:
            return dict(enabled=self.enabled,connected=self.connected,healthy=self.healthy(),error=self.error,
                source='Kraken WebSocket v2 ticker/trade',quotes={s:{k:v for k,v in q.items() if k!='received'} for s,q in self.quotes.items()},trades=dict(self.trades),
                executionAuthority=False,orderBookSource='REST Depth, validated again before order')

    def start(self):
        if self.enabled:
            self.thread=threading.Thread(target=self.loop,daemon=True);self.thread.start()

    def loop(self):
        delay=1
        while not self.quit.is_set():
            try:
                from websockets.sync.client import connect
                with connect('wss://ws.kraken.com/v2',open_timeout=10,close_timeout=2,max_size=1024*1024) as sock:
                    self.socket=sock
                    with self.lock:
                        self.quotes={};self.connected=True;self.error=None
                    for channel in ('ticker','trade'):
                        sock.send(json.dumps(dict(method='subscribe',params=dict(channel=channel,symbol=['BTC/EUR','ETH/EUR'],snapshot=True))))
                    delay=1
                    while not self.quit.is_set():
                        try:raw=sock.recv(timeout=2)
                        except TimeoutError:continue
                        self.ingest(json.loads(raw))
            except ImportError:
                self.error='Installera requirements.txt för WebSocket.'
                break
            except Exception:
                self.error='Marknadsström avbruten; nya automatiska köp spärras tills färska data finns.'
            finally:
                with self.lock:self.connected=False;self.quotes={}
                self.socket=None
            self.quit.wait(delay);delay=min(30,delay*2)

    def stop(self):
        self.quit.set()
        if self.socket:self.socket.close()
        if self.thread:self.thread.join(15)


class ExecutionStream:
    """Private execution notifications only trigger authoritative REST refreshes.

    Tokens stay in memory and are never included in public status or logs.
    """
    def __init__(self, private):
        self.private=private;self.enabled=os.environ.get('JARVIS_KRAKEN_PRIVATE_STREAM')=='1'
        self.quit=threading.Event();self.changed=threading.Event();self.thread=None
        self.socket=None;self.connected=False;self.error=None;self.last_event=None

    def status(self):
        return dict(enabled=self.enabled,connected=self.connected,error=self.error,lastEventAt=self.last_event,
                    authoritativeFills='REST QueryOrders/QueryTrades')

    def start(self):
        if self.enabled:
            self.thread=threading.Thread(target=self.loop,daemon=True);self.thread.start()

    def loop(self):
        delay=1
        while not self.quit.is_set():
            try:
                from websockets.sync.client import connect
                result=self.private.call('GetWebSocketsToken')
                token=result.get('token')
                if not isinstance(token,str) or not token:raise ValueError('Missing private stream token')
                with connect('wss://ws-auth.kraken.com/v2',open_timeout=10,close_timeout=2,max_size=1024*1024) as sock:
                    self.socket=sock
                    sock.send(json.dumps(dict(method='subscribe',params=dict(channel='executions',token=token,snap_orders=True,snap_trades=True))))
                    self.error=None;delay=1
                    while not self.quit.is_set():
                        try:message=json.loads(sock.recv(timeout=2))
                        except TimeoutError:continue
                        if message.get('method')=='subscribe' and message.get('success') is False:raise ValueError('Subscription rejected')
                        if message.get('channel')=='executions':
                            self.connected=True;self.last_event=datetime.now(timezone.utc).isoformat();self.changed.set()
            except ImportError:
                self.error='Installera requirements.txt för privata börshändelser.';break
            except Exception:
                self.error='Privata börshändelser saknas; REST-avstämning fortsätter.'
            finally:
                self.connected=False;self.socket=None
            self.quit.wait(delay);delay=min(60,delay*2)

    def stop(self):
        self.quit.set()
        if self.socket:self.socket.close()
        if self.thread:self.thread.join(15)
