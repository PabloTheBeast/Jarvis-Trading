"""Paper-only execution against public depth, with configurable adverse slippage.

No credential, private endpoint, transport submission or live broker is present.
"""
import math
from datetime import datetime


class PaperBroker:
    def __init__(self, engine):
        self.e = engine
        self.s = engine.s

    def quote(self, symbol, side):
        config = self.s.get('paperBroker', {})
        book = self.s['marketData'].get('orderBooks', {}).get(symbol)
        slip = config.get('slippageBps', 5)/10000
        if symbol in ('BTC-SIM','ETH-SIM') and self.s['marketData'].get('connector')=='kraken-public' and not book:
            raise ValueError('Verified Kraken crypto cannot fall back to synthetic execution')
        if book:
            base = book['asks' if side == 'BUY' else 'bids'][0][0]
        else:
            # Explicit synthetic model for synthetic equities and offline tests.
            half_spread = config.get('spreadBps', 10)/20000
            base = self.e.last(symbol)*(1+half_spread if side == 'BUY' else 1-half_spread)
        return base*(1+slip if side == 'BUY' else 1-slip)

    def plan(self, order):
        now = self.e.now
        if order.get('expiresAt') and now >= datetime.fromisoformat(order['expiresAt']):
            return 0, 0, 'EXPIRED'
        left = order['requestedQty']-order['filledQty']
        number = int(order['orderId'][4:])
        # Opt-in deterministic fault model, used by regression tests only.
        if self.s.get('paperBroker', {}).get('testFaults'):
            if order['filledQty'] == 0 and number % 19 == 0:
                return 0, 0, 'REJECTED'
            if order['filledQty'] == 0 and number % 7 == 0:
                left *= .6
        symbol, side = order['instrument'], order['side']
        books = self.s['marketData'].get('orderBooks', {})
        book = books.get(symbol)
        if not book:
            return left, self.quote(symbol, side), None
        key = symbol+'|'+side
        snapshot = self.s['marketData'].get('snapshotId')
        usage = self.s.setdefault('paperLiquidity', dict(snapshotId=snapshot, used={}))
        if usage['snapshotId'] != snapshot:
            usage.update(snapshotId=snapshot, used={})
        consumed = usage['used'].get(key, 0)
        remaining, filled, notional = left, 0, 0
        for price, size in book['asks' if side == 'BUY' else 'bids']:
            skip = min(consumed, size)
            consumed -= skip
            available = size-skip
            take = min(available, remaining)
            filled += take
            notional += take*price
            remaining -= take
            if remaining <= 1e-12:
                break
        if filled <= 1e-12:
            return 0, 0, None
        slip = self.s.get('paperBroker', {}).get('slippageBps', 5)/10000
        price = notional/filled*(1+slip if side == 'BUY' else 1-slip)
        if not math.isfinite(price) or price <= 0:
            return 0, 0, 'REJECTED'
        return filled, price, None

    def consume(self, order, qty):
        if order['instrument'] not in self.s['marketData'].get('orderBooks', {}):
            return
        key = order['instrument']+'|'+order['side']
        usage = self.s['paperLiquidity']['used']
        usage[key] = usage.get(key, 0)+qty
