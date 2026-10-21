"""Paper order lifecycle and reservations, separated from strategies/accounting."""
import math
from .accounting import Ledger
from .broker import PaperBroker
from datetime import timedelta

FEE = .0015
INITIAL = 10000


class OrderManager:
    def __init__(self, engine):
        self.engine = engine

    def __getattr__(self, name):
        return getattr(self.engine, name)

    def buy_room(self, a, symbol):
        s, eq = self.s, self.equity()
        pending = [o for o in s['orders'] if o['status'] in ('NEW', 'PARTIAL') and o['side'] == 'BUY']
        def reserved(predicate):
            return sum((o['requestedQty']-o['filledQty'])*self.last(o['instrument']) for o in pending if predicate(o))
        exposure = reserved(lambda o: True)
        from .engine import ASSETS
        correlated = {x['id'] for x in ASSETS if x['id'] == symbol or self.correlation(symbol, x['id']) > .75}
        return max(0, min(2000, 650*max(1, eq/INITIAL),
                          (s['cash']-exposure*(1+FEE))/(1+FEE),
                          # Include fee in post-fill equity-based limits.
                          (eq*.7-self.invested()-exposure)/(1+.7*FEE),
                          (eq*.25-self.value(symbol)-reserved(lambda o: o['instrument'] == symbol))/(1+.25*FEE),
                          a['budget']-self.agent_value(a['id'])-reserved(lambda o: o['agentId'] == a['id']),
                          (eq*.4-sum(self.value(x) for x in correlated)-reserved(lambda o: o['instrument'] in correlated))/(1+.4*FEE)))

    def event(self, order, status, detail):
        order['status'] = status
        self.s['orderEvents'].insert(0, dict(orderId=order['orderId'], tick=self.s['tick'],
                                             day=self.s['day'], status=status, detail=detail, at=self.now.isoformat()))

    def submit(self, aid, symbol, side, amount, reason='Strategisignal', intent_id=None):
        s = self.s
        intent_id = intent_id or '|'.join(map(str, (s['tick'], aid, symbol, side, reason)))
        prior = s.setdefault('intentOrders', {}).get(intent_id)
        if prior:
            return next(order for order in s['orders'] if order['orderId'] == prior)
        a = next((a for a in self.agents() if a['id'] == aid), None)
        s['orderSeq'] += 1
        o = dict(orderId='SIM-'+str(s['orderSeq']), agentId=aid, instrument=symbol, side=side,
                 amount=amount, reason=reason, day=s['day'], tick=s['tick'], requestedQty=0,
                 filledQty=0, filledNotional=0, feeTotal=0, status='NEW', entryMode=None)
        o['intentId'] = intent_id
        s['intentOrders'][intent_id] = o['orderId']
        s['orders'].insert(0, o)
        if (not a or symbol not in a['ids'] or side not in ('BUY', 'SELL') or
                type(amount) not in (int, float) or not math.isfinite(amount) or amount <= 0 or
                not self.allowed(symbol) or self.last(symbol) <= 0):
            self.event(o, 'REJECTED', 'Ogiltig order eller stängd marknad')
            return o
        if any(x is not o and x['status'] in ('NEW', 'PARTIAL') and x['agentId'] == aid and x['instrument'] == symbol for x in s['orders']):
            self.event(o, 'REJECTED', 'Väntande order finns')
            return o
        if side == 'BUY':
            self.global_risk()
            if s['halt'] or s['agentStatus'][aid] != 'AKTIV':
                self.event(o, 'REJECTED', 'Riskspärr')
                return o
            room = min(amount*self.learning(a, symbol), self.buy_room(a, symbol))
            if room < 90:
                self.event(o, 'REJECTED', 'Riskutrymme under 90 SEK')
                return o
            o['entryMode'] = self.regime(symbol)
        else:
            owned = s['positions'].get(symbol, {}).get('owners', {}).get(aid, 0)
            room = min(amount, owned*self.last(symbol))
            if room < 30 and not reason.startswith('Chefens'):
                self.event(o, 'REJECTED', 'För litet innehav')
                return o
        execution_price = PaperBroker(self.engine).quote(symbol, side)
        o['requestedQty'] = room/execution_price if side == 'BUY' else min(owned, amount/execution_price)
        o['expiresAt'] = (self.now+timedelta(seconds=60)).isoformat()
        o['orderType'] = 'PAPER_MARKET'
        o['limitPrice'] = self.last(symbol)
        self.event(o, 'NEW', 'Paper-order accepterad')
        self.fill(o, a)
        return o

    def fill(self, o, a):
        if o['status'] not in ('NEW', 'PARTIAL'):
            return
        s, aid, symbol = self.s, a['id'], o['instrument']
        if not self.allowed(symbol):
            return
        if o['side'] == 'BUY' and (s['halt'] or s['agentStatus'][aid] != 'AKTIV'):
            self.event(o, 'CANCELLED', 'Riskspärr')
            return
        broker = PaperBroker(self.engine)
        qty, price, failure = broker.plan(o)
        if failure:
            self.event(o, 'CANCELLED' if failure == 'EXPIRED' else 'REJECTED', failure)
            return
        if qty <= 0:
            return
        if o['side'] == 'BUY':
            # Exclude this order's reservation, then recheck every limit at execution price.
            old_status = o['status']
            o['status'] = 'CHECKING'
            already_filled = o.get('filledNotional', sum(
                fill['qty']*fill['price'] for fill in s['orderHistory'] if fill['orderId'] == o['orderId']))
            room = min(self.buy_room(a, symbol), 2000-already_filled,
                       650*max(1, self.equity()/INITIAL)-already_filled)
            o['status'] = old_status
            if qty*price > room + 1e-7:
                self.event(o, 'CANCELLED', 'Riskutrymme ändrades före avslut')
                return
        if o['side'] == 'SELL' and qty > s['positions'].get(symbol, {}).get('owners', {}).get(aid, 0)+1e-12:
            self.event(o, 'CANCELLED', 'Insufficient position')
            return
        fee, pnl = Ledger(self.engine).fill(o, a, qty, price)
        broker.consume(o, qty)
        if pnl is not None:
            self.outcome(a, symbol, pnl, o['reason'])
        o['filledNotional'] = o.get('filledNotional', sum(
            fill['qty']*fill['price'] for fill in s['orderHistory'] if fill['orderId'] == o['orderId']))+qty*price
        o['filledQty'] += qty
        o['feeTotal'] += fee
        self.event(o, 'FILLED' if o['requestedQty']-o['filledQty'] <= 1e-9 else 'PARTIAL', 'Paper-avslut')
        s['orderHistory'].insert(0, dict(fillId=o['orderId']+'-F'+str(1+sum(f['orderId'] == o['orderId'] for f in s['orderHistory'])), orderId=o['orderId'], tick=s['tick'], day=s['day'], agent=aid,
                                       id=symbol, side='KÖP' if o['side'] == 'BUY' else 'SÄLJ', amount=qty*price,
                                       qty=qty, price=price, fee=fee, pnl=pnl, reason=o['reason']))
        self.log(a['name'], o['side']+' '+symbol+' '+o['status'])
