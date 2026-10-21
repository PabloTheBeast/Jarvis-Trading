"""Authoritative integer nano-SEK ledger; Decimal conversion at all money boundaries.

Prices/quantities and risk analytics remain floating point. Monetary balances,
cost basis, fees, realized P/L and protected profit are integers at 1e-9 SEK.
The dashboard's numeric amounts are projections, never the source of truth.
"""
from decimal import Decimal, ROUND_HALF_EVEN

SCALE = 1_000_000_000
MONEY_KEYS = ('cash', 'vault', 'ownerNetDeposits', 'realizedTotal', 'realizedHighWater')
MAP_KEYS = ('agentRealized', 'riskBaseline')


def units(value):
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError('Nonfinite money')
    return int((number*SCALE).to_integral_value(rounding=ROUND_HALF_EVEN))


def amount(value):
    return value / SCALE


def ratio_cost(cost, qty, owned):
    if abs(qty-owned) < 1e-12*max(1, owned):
        return cost
    return int((Decimal(cost)*Decimal(str(qty))/Decimal(str(owned))).to_integral_value(rounding=ROUND_HALF_EVEN))


class Ledger:
    def __init__(self, engine):
        self.engine = engine
        self.s = engine.s
        if 'book' not in self.s:
            self.initialize()

    def initialize(self):
        s = self.s
        book = {key: units(s[key]) for key in MONEY_KEYS}
        book.update({key: {aid: units(value) for aid, value in s[key].items()} for key in MAP_KEYS})
        book['ownerCost'] = {aid: {symbol: units(value) for symbol, value in costs.items()}
                             for aid, costs in s['ownerCost'].items()}
        realized = sum(book['agentRealized'].values())
        realized_rounding = realized-book['realizedTotal']
        if abs(realized_rounding) > units(.00002):
            raise ValueError('Legacy agent results do not reconcile; inspect pre-migration backup')
        book['realizedTotal'] = realized
        s['migrationRealizedRoundingNanoSek'] = realized_rounding
        total_cost = sum(sum(costs.values()) for costs in book['ownerCost'].values())
        reconciled = book['ownerNetDeposits']+book['realizedTotal']-total_cost-book['vault']
        rounding = reconciled-book['cash']
        if abs(rounding) > units(.00002):
            raise ValueError('Legacy ledger cannot be reconciled; restore backup and inspect')
        book['cash'] = reconciled
        s['migrationRoundingNanoSek'] = rounding
        s['book'] = book
        self.project()

    def project(self):
        s, book = self.s, self.s['book']
        for key in MONEY_KEYS:
            s[key] = amount(book[key])
        for key in MAP_KEYS:
            # Newly created agents begin with exactly zero balances.
            for aid in s[key]:
                book[key].setdefault(aid, units(s[key][aid]))
            s[key] = {aid: amount(value) for aid, value in book[key].items()}
        for aid in s['ownerCost']:
            book['ownerCost'].setdefault(aid, {})
        s['ownerCost'] = {aid: {symbol: amount(value) for symbol, value in costs.items()}
                          for aid, costs in book['ownerCost'].items()}
        for symbol, position in s['positions'].items():
            position['cost'] = amount(sum(costs.get(symbol, 0) for costs in book['ownerCost'].values()))

    def fill(self, order, agent, qty, price):
        s, book = self.s, self.s['book']
        aid, symbol = agent['id'], order['instrument']
        notional = units(Decimal(str(qty))*Decimal(str(price)))
        fee = int((Decimal(notional)*Decimal('.0015')).to_integral_value(rounding=ROUND_HALF_EVEN))
        position = s['positions'].setdefault(symbol, dict(qty=0, cost=0, owners={}))
        costs = book['ownerCost'].setdefault(aid, {})
        pnl = None
        if order['side'] == 'BUY':
            if notional+fee > book['cash']:
                raise ValueError('Insufficient cash at execution')
            book['cash'] -= notional+fee
            position['qty'] += qty
            position['owners'][aid] = position['owners'].get(aid, 0)+qty
            costs[symbol] = costs.get(symbol, 0)+notional+fee
            s['entryModes'][aid+'|'+symbol] = order['entryMode']
        else:
            owned = position['owners'].get(aid, 0)
            if owned <= 0 or qty > owned+1e-12:
                raise ValueError('Insufficient position at execution')
            cost = ratio_cost(costs[symbol], qty, owned)
            position['qty'] -= qty
            position['owners'][aid] -= qty
            costs[symbol] -= cost
            book['cash'] += notional-fee
            pnl = notional-fee-cost
            book['realizedTotal'] += pnl
            book['agentRealized'][aid] += pnl
            if position['qty'] < 1e-12:
                del s['positions'][symbol]
        self.project()
        entry = self.movement('TRADE_'+order['side'], -(notional+fee) if order['side']=='BUY' else notional-fee,
                              0, 'Paper execution', 'agent:'+aid)
        entry.update(orderId=order['orderId'], fillId=order['orderId']+'-F'+str(1+sum(f['orderId']==order['orderId'] for f in s['orderHistory'])),
                     feeNanoSek=fee)
        return amount(fee), None if pnl is None else amount(pnl)

    def free_cash(self):
        from .broker import PaperBroker
        reserved = 0
        for order in self.s['orders']:
            if order['side']=='BUY' and order['status'] in ('NEW','PARTIAL'):
                notional = units((order['requestedQty']-order['filledQty'])*PaperBroker(self.engine).quote(order['instrument'],'BUY'))
                reserved += notional+int((Decimal(notional)*Decimal('.0015')).to_integral_value(rounding=ROUND_HALF_EVEN))
        return max(0,self.s['book']['cash']-reserved)

    def sweep(self, trigger):
        s, book, e = self.s, self.s['book'], self.engine
        eligible = max(0, book['realizedTotal']-book['realizedHighWater'])
        swept = min(eligible//5, self.free_cash())
        book['cash'] -= swept
        book['vault'] += swept
        book['realizedHighWater'] += swept*5
        self.project()
        value = amount(swept)
        s['peak'] = max(e.equity(), s['peak']-value)
        entry = dict(day=s['day'], tick=s['tick'], trigger=trigger, eligible=amount(eligible),
                     swept=value, date=e.now.isoformat())
        s['sweepHistory'].insert(0, entry)
        if swept:
            self.movement('PROFIT_PROTECTION', -swept, swept, trigger)
            s['vaultMovements'].insert(0, dict(day=s['day'], tick=s['tick'], type=trigger, amount=value))
        return amount(eligible), value

    def movement(self, kind, cash_delta, vault_delta, reason, actor=None, approval_id=None):
        e, s = self.engine, self.s
        entry = dict(
            id='CAP-'+str(len(s.get('capitalHistory', []))+1), at=e.now.isoformat(),
            type=kind, reason=reason, cashDeltaNanoSek=cash_delta, vaultDeltaNanoSek=vault_delta,
            cashNanoSek=s['book']['cash'], vaultNanoSek=s['book']['vault'],
            ownerNetNanoSek=s['book']['ownerNetDeposits'], realizedNanoSek=s['book']['realizedTotal'],
            actor=actor, approvalId=approval_id, tick=s['tick'], day=s['day'])
        s.setdefault('capitalHistory', []).insert(0, entry)
        return entry

    def capital(self, value, kind):
        s, book, e = self.s, self.s['book'], self.engine
        if type(value) not in (int, float) or not 0 < value <= 1e8:
            raise ValueError('Invalid positive amount')
        value = units(value)
        if value <= 0:
            raise ValueError('Amount below monetary precision')
        actor = getattr(e, 'operator', None)
        if kind == 'release':
            if not actor:
                raise ValueError('Signed owner approval required')
            if value > book['vault']:
                raise ValueError('Insufficient protected balance')
            book['vault'] -= value
            book['cash'] += value
            s['peak'] += amount(value)
            s['vaultMovements'].insert(0, dict(day=s['day'], tick=s['tick'], type='MANUELLT GODKÄND ÅTERFÖRING', amount=-amount(value), actor=actor))
            self.movement('VAULT_RELEASE', value, -value, 'Manuellt godkänd återföring', actor, getattr(e, 'approval_id', None))
        elif kind in ('deposit', 'withdraw'):
            if kind == 'withdraw' and value > self.free_cash():
                raise ValueError('Insufficient free cash')
            delta = value if kind == 'deposit' else -value
            book['cash'] += delta
            book['ownerNetDeposits'] += delta
            self.project()
            s['peak'] = max(e.equity(), s['peak']+amount(delta))
            s['dayOpenEquity'] += amount(delta)
            s['ownerMovements'].insert(0, dict(day=s['day'], tick=s['tick'], type='ÄGARINSÄTTNING' if delta > 0 else 'ÄGARUTTAG', amount=amount(delta), at=e.now.isoformat()))
            self.movement(kind.upper(), delta, 0, 'Ägarkapital', actor)
        else:
            raise ValueError('Unknown capital movement')
        self.project()

    def reset_risk_baseline(self, aid):
        self.s['book']['riskBaseline'][aid] = self.s['book']['agentRealized'][aid]
        self.project()

    def audit(self):
        s, book = self.s, self.s['book']
        if any(type(book[key]) is not int for key in MONEY_KEYS):
            raise ValueError('Noninteger ledger balance')
        cost = sum(sum(costs.values()) for costs in book['ownerCost'].values())
        if book['cash']+book['vault'] != book['ownerNetDeposits']+book['realizedTotal']-cost:
            raise ValueError('Exact capital ledger mismatch')
        if min(book['cash'], book['vault']) < 0:
            raise ValueError('Negative account balance')
        for key in MONEY_KEYS:
            if s[key] != amount(book[key]):
                raise ValueError('Money projection mismatch: '+key)
        for key in MAP_KEYS:
            for aid, value in book[key].items():
                if s[key].get(aid) != amount(value):
                    raise ValueError('Agent monetary projection mismatch')
        if sum(book['agentRealized'].values()) != book['realizedTotal']:
            raise ValueError('Realized agent totals mismatch')
        for aid, costs in book['ownerCost'].items():
            for symbol, value in costs.items():
                if value < 0 or s['ownerCost'][aid].get(symbol) != amount(value):
                    raise ValueError('Owner cost mismatch')
                if value and symbol not in s['positions']:
                    raise ValueError('Orphan cost basis')
        for symbol, p in s['positions'].items():
            if any(q < -1e-12 for q in p['owners'].values()) or abs(sum(p['owners'].values())-p['qty']) > 1e-9:
                raise ValueError('Position ownership mismatch')
            if p['cost'] != amount(sum(costs.get(symbol, 0) for costs in book['ownerCost'].values())):
                raise ValueError('Position cost mismatch')
        return True
