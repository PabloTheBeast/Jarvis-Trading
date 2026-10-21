"""Browser-independent port of the original rule-based paper engine.

The state schema intentionally matches the existing dashboard. Financial values
use Python doubles, as in the original JS; reconciliation uses explicit tolerances.
"""
import math
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

INITIAL, FEE = 10000, .0015
ASSETS = [
    dict(id='STOCK-A', type='Aktie', p=210, vol=.014, drift=.00025),
    dict(id='STOCK-B', type='Aktie', p=95, vol=.018, drift=.00015),
    dict(id='ETF-WORLD', type='ETF', p=460, vol=.007, drift=.00018),
    dict(id='ETF-BOND', type='ETF', p=120, vol=.003, drift=.00005),
    dict(id='BTC-SIM', type='Krypto', p=0, vol=0, drift=0),
    dict(id='ETH-SIM', type='Krypto', p=0, vol=0, drift=0),
]
BASE_AGENTS = [
    ('alpha', 'Trend', 1200, ['STOCK-A', 'STOCK-B']),
    ('beta', 'ETF', 1200, ['ETF-WORLD', 'ETF-BOND']),
    ('gamma', 'Momentum', 950, ['BTC-SIM', 'ETH-SIM']),
    ('delta', 'Mean reversion', 850, ['STOCK-A', 'ETF-WORLD']),
    ('epsilon', 'Trend', 900, ['ETF-WORLD']),
    ('zeta', 'Mean reversion', 700, ['BTC-SIM', 'ETH-SIM']),
    ('eta', 'Momentum', 900, ['STOCK-A', 'STOCK-B']),
    ('theta', 'ETF', 800, ['ETF-BOND']),
    ('iota', 'Trend', 750, ['BTC-SIM']),
    ('kappa', 'Mean reversion', 750, ['STOCK-B']),
]


def crypto(symbol):
    return symbol in ('BTC-SIM', 'ETH-SIM')


def clock(now):
    local = now.astimezone(ZoneInfo('Europe/Stockholm'))
    return local.date().isoformat(), local.weekday() < 5 and 540 <= local.hour * 60 + local.minute < 1050


def new_state():
    s = dict(tick=0, cash=INITIAL, vault=0, ownerNetDeposits=INITIAL,
             ownerMovements=[], positions={}, prices={a['id']: ([] if crypto(a['id']) else [a['p']]) for a in ASSETS},
             returns={a['id']: 0 for a in ASSETS}, hist=[INITIAL], peak=INITIAL, maxdd=0,
             logs=[], memory=[], lessons={}, learningDecisions=[], managerNotes=[],
             halt=False, liquidated=False, globalPause=None, approvalPending=False,
             notifications=[], activeMs=0, runStarted=None, dayStartTick=0,
             dayOpenRealized=0, dayOpenEquity=INITIAL, dayOpenedAt='', dayClosed=False,
             marketAuto=True, autoPace=5000, cryptoReportDate=None, marketSessionDate=None,
             marketClosedDate=None, manualStopDate=None, realizedTotal=0,
             realizedHighWater=0, day=1, lastCloseTick=0, dayReports=[], vaultMovements=[],
             orderHistory=[], orders=[], orderEvents=[], orderSeq=0, sweepHistory=[],
             daysSinceSweep=0, seedValue=42, rngState=42, agentCount=10,
             quarantine={}, shadow={}, entryModes={}, riskEvents=[], running=False,
             marketData=dict(source='Kraken EUR / ECB SEK', status='waiting', at=None, error=None),
             reportDate=None)
    for key in ('agentSpend', 'agentRealized', 'agentLosses', 'agentHalt', 'ownerCost',
                'agentStatus', 'agentTrades', 'agentWins', 'agentGrossProfit',
                'agentGrossLoss', 'agentPeak', 'agentMaxDD', 'agentEquityHist', 'riskBaseline'):
        s[key] = {}
    engine = Engine(s)
    for a in engine.agents():
        engine.init_agent(a, False)
    return s


class Engine:
    def __init__(self, state):
        self.s = state
        self.now = datetime.now(timezone.utc)

    def agents(self):
        scale = max(.4, min(5, self.equity() / INITIAL)) * 10 / self.s['agentCount']
        result = []
        for i in range(self.s['agentCount']):
            name, strategy, budget, ids = BASE_AGENTS[i % 10]
            aid = name if i < 10 else 'extra' + str(i + 1)
            result.append(dict(id=aid, name=name.upper() if i < 10 else 'AGENT ' + str(i + 1),
                               strategy=strategy, budget=max(250, budget * scale), ids=ids,
                               desc='Regelbaserad ' + strategy))
        return result

    def init_agent(self, a, quarantine):
        s, aid = self.s, a['id']
        for key in ('agentSpend', 'agentRealized', 'agentLosses', 'agentTrades', 'agentWins',
                    'agentGrossProfit', 'agentGrossLoss', 'agentMaxDD', 'riskBaseline'):
            s[key][aid] = 0
        s['agentPeak'][aid] = a['budget']
        s['agentHalt'][aid] = False
        s['agentStatus'][aid] = 'AKTIV'
        s['ownerCost'][aid] = {}
        s['agentEquityHist'][aid] = []
        if quarantine:
            self.quarantine(a)

    def last(self, symbol):
        p = self.s['prices'][symbol]
        return p[-1] if p else 0

    def value(self, symbol):
        return self.s['positions'].get(symbol, {}).get('qty', 0) * self.last(symbol)

    def invested(self):
        return sum(self.value(x) for x in self.s['positions'])

    def equity(self):
        return self.s['cash'] + self.invested()

    def agent_value(self, aid):
        return sum(p['owners'].get(aid, 0) * self.last(symbol) for symbol, p in self.s['positions'].items())

    def log(self, who, msg):
        self.s['logs'].insert(0, dict(tick=self.s['tick'], agent=who, msg=msg))
        del self.s['logs'][300:]

    def announce(self, msg):
        self.s['notifications'].insert(0, dict(tick=self.s['tick'], day=self.s['day'], msg=msg,
                                             date=self.now.isoformat(), read=False))
        self.log('CHEF', msg)

    def random(self):
        self.s['rngState'] = (1664525 * self.s['rngState'] + 1013904223) & 0xffffffff
        return self.s['rngState'] / 4294967296

    def regime(self, symbol):
        p = self.s['prices'][symbol][-7:]
        change = p[-1] / p[0] - 1 if len(p) >= 7 else 0
        return 'upptrend' if change > .018 else 'nedtrend' if change < -.018 else 'neutral' if len(p) < 7 else 'sidledes'

    def lesson_key(self, a, symbol, mode):
        group = 'Krypto' if crypto(symbol) else 'ETF' if symbol.startswith('ETF') else 'Aktie'
        return '|'.join([group, a['strategy'], mode])

    def learning(self, a, symbol):
        stats = self.s['lessons'].get(self.lesson_key(a, symbol, self.regime(symbol)))
        if not stats or stats['count'] < 3:
            return 1
        rate = (stats['wins'] + 2) / (stats['count'] + 4)
        return max(.35, min(1.15, .65 + rate * .65 + (.08 if stats['total'] > 0 else -.08)))

    def correlation(self, x, y):
        p, q = self.s['prices'][x], self.s['prices'][y]
        n = min(len(p), len(q), 31) - 1
        if n < 12:
            return 0
        u = [p[-k] / p[-k-1] - 1 for k in range(n, 0, -1)]
        v = [q[-k] / q[-k-1] - 1 for k in range(n, 0, -1)]
        mu, mv = sum(u) / n, sum(v) / n
        aa, bb = sum((x-mu)**2 for x in u), sum((y-mv)**2 for y in v)
        return sum((x-mu)*(y-mv) for x, y in zip(u, v)) / math.sqrt(aa*bb) if aa*bb else 0

    def allowed(self, symbol):
        return crypto(symbol) or not self.s['marketAuto'] or clock(self.now)[1]

    def buy_room(self, a, symbol):
        s, eq = self.s, self.equity()
        pending = [o for o in s['orders'] if o['status'] in ('NEW', 'PARTIAL') and o['side'] == 'BUY']
        def reserved(predicate):
            return sum((o['requestedQty']-o['filledQty'])*self.last(o['instrument']) for o in pending if predicate(o))
        exposure = reserved(lambda o: True)
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

    def submit(self, aid, symbol, side, amount, reason='Strategisignal'):
        s = self.s
        a = next((a for a in self.agents() if a['id'] == aid), None)
        s['orderSeq'] += 1
        o = dict(orderId='SIM-'+str(s['orderSeq']), agentId=aid, instrument=symbol, side=side,
                 amount=amount, reason=reason, day=s['day'], tick=s['tick'], requestedQty=0,
                 filledQty=0, filledNotional=0, feeTotal=0, status='NEW', entryMode=None)
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
        o['requestedQty'] = room/self.last(symbol)
        o['limitPrice'] = self.last(symbol)
        self.event(o, 'NEW', 'Paper-order accepterad')
        self.fill(o, a)
        return o

    def fill(self, o, a):
        s, aid, symbol = self.s, a['id'], o['instrument']
        if not self.allowed(symbol):
            return
        if o['side'] == 'BUY' and (s['halt'] or s['agentStatus'][aid] != 'AKTIV'):
            self.event(o, 'CANCELLED', 'Riskspärr')
            return
        number = int(o['orderId'][4:])
        if o['filledQty'] == 0 and number % 19 == 0:
            self.event(o, 'REJECTED', 'Simulerad mäklaravvisning')
            return
        price = self.last(symbol)
        qty = o['requestedQty'] - o['filledQty']
        if o['filledQty'] == 0 and number % 7 == 0:
            qty *= .6
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
        p = s['positions'].setdefault(symbol, dict(qty=0, cost=0, owners={}))
        fee = qty*price*FEE
        pnl = None
        if o['side'] == 'BUY':
            cost = qty*price+fee
            s['cash'] -= cost
            p['qty'] += qty
            p['cost'] += cost
            p['owners'][aid] = p['owners'].get(aid, 0)+qty
            s['ownerCost'][aid][symbol] = s['ownerCost'][aid].get(symbol, 0)+cost
            s['entryModes'][aid+'|'+symbol] = o['entryMode']
        else:
            owned = p['owners'].get(aid, 0)
            if qty > owned+1e-9 or owned <= 0:
                self.event(o, 'CANCELLED', 'Otillräckligt innehav')
                return
            cost = s['ownerCost'][aid][symbol]*qty/owned
            proceeds = qty*price-fee
            p['qty'] -= qty
            p['cost'] -= cost  # Same cost basis as the selling owner.
            p['owners'][aid] -= qty
            s['ownerCost'][aid][symbol] -= cost
            s['cash'] += proceeds
            pnl = proceeds-cost
            s['realizedTotal'] += pnl
            s['agentRealized'][aid] += pnl
            self.outcome(a, symbol, pnl, o['reason'])
            if p['qty'] < 1e-9:
                del s['positions'][symbol]
        o['filledNotional'] = o.get('filledNotional', sum(
            fill['qty']*fill['price'] for fill in s['orderHistory'] if fill['orderId'] == o['orderId']))+qty*price
        o['filledQty'] += qty
        o['feeTotal'] += fee
        self.event(o, 'FILLED' if o['requestedQty']-o['filledQty'] <= 1e-9 else 'PARTIAL', 'Paper-avslut')
        s['orderHistory'].insert(0, dict(orderId=o['orderId'], tick=s['tick'], day=s['day'], agent=aid,
                                       id=symbol, side='KÖP' if o['side'] == 'BUY' else 'SÄLJ', amount=qty*price,
                                       qty=qty, price=price, fee=fee, pnl=pnl, reason=o['reason']))
        self.log(a['name'], o['side']+' '+symbol+' '+o['status'])

    def outcome(self, a, symbol, pnl, reason):
        s, aid = self.s, a['id']
        mode = s['entryModes'].get(aid+'|'+symbol, self.regime(symbol))
        key = self.lesson_key(a, symbol, mode)
        stats = s['lessons'].setdefault(key, dict(count=0, wins=0, total=0, positive=0, negative=0))
        stats['count'] += 1
        stats['wins'] += pnl > 0
        stats['total'] += pnl
        stats['positive'] += max(0, pnl)
        stats['negative'] += max(0, -pnl)
        s['memory'].insert(0, dict(tick=s['tick'], agent=a['name'], id=symbol, strategy=a['strategy'], mode=mode, pnl=pnl, reason=reason))
        del s['memory'][120:]
        s['agentTrades'][aid] += 1
        s['agentWins'][aid] += pnl > 0
        s['agentLosses'][aid] += pnl < 0
        s['agentGrossProfit'][aid] += max(0, pnl)
        s['agentGrossLoss'][aid] += max(0, -pnl)
        if s['agentLosses'][aid] >= 3 or s['agentRealized'][aid]-s['riskBaseline'][aid] <= -a['budget']*.08:
            self.quarantine(a)

    def shadow_book(self, start):
        return dict(start=start, until=start+40, next=start+40, shadow={}, trades=0, wins=0, pnl=0, peak=0, maxDD=0, reviewCount=0)

    def quarantine(self, a):
        s, aid = self.s, a['id']
        if s['agentStatus'][aid] == 'KARANTÄN':
            return
        s['agentStatus'][aid] = 'KARANTÄN'
        s['agentHalt'][aid] = True
        s['quarantine'][aid] = self.shadow_book(s['tick'])
        s['shadow'][aid] = s['quarantine'][aid]['shadow']

    def pause(self, reason):
        s = self.s
        if s['globalPause']:
            return
        s['halt'], s['approvalPending'] = True, False
        s['globalPause'] = self.shadow_book(s['tick'])
        s['globalPause']['reason'] = reason
        self.announce('Handeln pausad: '+reason)
        for o in s['orders']:
            if o['side'] == 'BUY' and o['status'] in ('NEW', 'PARTIAL'):
                self.event(o, 'CANCELLED', 'Global riskpaus')

    def global_risk(self):
        s = self.s
        eq = self.equity()
        s['peak'] = max(s['peak'], eq)
        dd = (s['peak']-eq)/s['peak'] if s['peak'] > 0 else 0
        s['maxdd'] = max(s['maxdd'], dd)
        if dd >= .10:
            self.pause('Portföljdrawdown minst 10 %')
        if dd >= .10 and s['approvalPending']:
            s['approvalPending'] = False
            s['globalPause']['next'] = s['tick']+20

    def shadow_signal(self, q, key, side, symbol):
        book = q['shadow']
        if side == 'BUY' and key not in book:
            book[key] = dict(entry=self.last(symbol), step=self.s['tick'], symbol=symbol)
        elif side == 'SELL' and key in book:
            self.close_shadow(q, key)

    def close_shadow(self, q, key):
        p = q['shadow'].pop(key)
        pnl = 200*(self.last(p['symbol'])/p['entry']-1)-400*FEE
        q['trades'] += 1
        q['wins'] += pnl > 0
        q['pnl'] += pnl
        q['peak'] = max(q['peak'], q['pnl'])
        q['maxDD'] = max(q['maxDD'], q['peak']-q['pnl'])

    def review_shadow(self, q, global_pause=False):
        s = self.s
        for key, p in list(q['shadow'].items()):
            if self.allowed(p['symbol']) and (s['tick']-p['step'] >= 12 or self.last(p['symbol']) < p['entry']*.95):
                self.close_shadow(q, key)
        when = q['next'] if global_pause else q['until']
        if s['tick'] < when:
            return False
        for key, p in list(q['shadow'].items()):
            if self.allowed(p['symbol']):
                self.close_shadow(q, key)
        q['reviewCount'] += 1
        good = (q['trades'] >= (5 if global_pause else 3) and q['pnl'] > 0 and
                q['wins']/max(1, q['trades']) >= .5 and q['maxDD'] <= (100 if global_pause else 40))
        if global_pause:
            good = good and (s['peak']-self.equity())/s['peak'] < .10
        if not good:
            q['next' if global_pause else 'until'] = s['tick']+20
        return good

    def signal(self, a, symbol):
        p = self.s['prices'][symbol]
        if len(p) < 7:
            return None
        curr, prev, strategy = p[-1], p[-2], a['strategy']
        avg = lambda n: sum(p[-n:])/len(p[-n:])
        if strategy == 'Trend':
            return 'BUY' if curr > avg(6)*1.008 and curr > prev else 'SELL' if curr < avg(6)*.992 else None
        if strategy == 'Momentum':
            return 'BUY' if curr > avg(5)*1.018 else 'SELL' if curr < avg(5)*.985 else None
        if strategy == 'Mean reversion':
            return 'BUY' if curr < avg(6)*.977 else 'SELL' if curr > avg(6)*1.024 else None
        if strategy == 'ETF':
            if (symbol == 'ETF-BOND' and self.s['tick'] % 8 == 0 or
                    symbol == 'ETF-WORLD' and curr > avg(6) and self.s['tick'] % 5 == 0):
                return 'BUY'
            return 'SELL' if curr < avg(6)*.975 else None

    def step(self, prices, now=None):
        s = self.s
        self.now = now or datetime.now(timezone.utc)
        if set(prices) != {'BTC-SIM', 'ETH-SIM'} or any(not math.isfinite(v) or v <= 0 for v in prices.values()):
            raise ValueError('A complete valid Kraken SEK snapshot is required')
        date, _ = clock(self.now)
        if s['reportDate'] and s['reportDate'] != date:
            self.close_day()
            self.open_day()
        s['reportDate'] = date
        self.open_day()
        s['tick'] += 1
        for a in ASSETS:
            symbol = a['id']
            old = self.last(symbol)
            if crypto(symbol):
                price = prices[symbol]
            elif self.allowed(symbol):
                normal = (sum(self.random() for _ in range(6))-3)*1.41421356
                price = max(.01, old*(1+a['drift']+normal*a['vol']))
            else:
                s['returns'][symbol] = 0
                continue
            s['returns'][symbol] = price/old-1 if old else 0
            s['prices'][symbol].append(price)
            del s['prices'][symbol][:-256]
        s['marketData'] = dict(source='Kraken EUR / ECB SEK', status='ok', at=self.now.isoformat(), error=None)
        self.global_risk()
        agents = self.agents()
        for o in list(s['orders']):
            if o['status'] in ('NEW', 'PARTIAL'):
                self.fill(o, next(a for a in agents if a['id'] == o['agentId']))
        for a in agents:
            aid = a['id']
            for symbol in a['ids']:
                if not self.allowed(symbol):
                    continue
                qty = s['positions'].get(symbol, {}).get('owners', {}).get(aid, 0)
                cost = s['ownerCost'][aid].get(symbol, 0)
                if qty > 0 and qty*self.last(symbol) < cost*.95:
                    self.submit(aid, symbol, 'SELL', qty*self.last(symbol)+1, 'Chefens stop-loss (5 %)')
                side = self.signal(a, symbol)
                if not side:
                    continue
                if s['agentStatus'][aid] == 'KARANTÄN':
                    self.shadow_signal(s['quarantine'][aid], symbol, side, symbol)
                elif s['globalPause']:
                    if not s['approvalPending']:
                        self.shadow_signal(s['globalPause'], aid+'|'+symbol, side, symbol)
                    if side == 'SELL':
                        self.submit(aid, symbol, side, 350)
                else:
                    self.submit(aid, symbol, side, min(350, a['budget']*.28) if side == 'BUY' else 350)
            q = s['quarantine'].get(aid)
            if q and self.review_shadow(q):
                s['agentStatus'][aid] = 'AKTIV'
                s['agentHalt'][aid] = False
                s['agentLosses'][aid] = 0
                # Preserve lifetime accounting; reset only the risk evaluation baseline.
                s['riskBaseline'][aid] = s['agentRealized'][aid]
                del s['quarantine'][aid]
                s['shadow'].pop(aid, None)
            v = a['budget']+s['agentRealized'][aid]+self.agent_value(aid)-sum(s['ownerCost'][aid].values())
            s['agentPeak'][aid] = max(s['agentPeak'][aid], v)
            s['agentMaxDD'][aid] = max(s['agentMaxDD'][aid], (s['agentPeak'][aid]-v)/s['agentPeak'][aid])
        if s['globalPause'] and not s['approvalPending'] and self.review_shadow(s['globalPause'], True):
            s['approvalPending'] = True
            self.announce('Skuggtest godkänt. Väntar på manuellt godkännande.')
        self.global_risk()
        s['hist'].append(self.equity())
        del s['hist'][:-2000]
        if not s['marketAuto'] and s['tick']-s['dayStartTick'] >= 100:
            s['running'] = False
            self.close_day()
        self.audit()

    def sweep(self, trigger):
        s = self.s
        eligible = max(0, s['realizedTotal']-s['realizedHighWater'])
        swept = min(eligible*.2, max(0, s['cash']))
        s['realizedHighWater'] += swept/.2
        s['cash'] -= swept
        s['vault'] += swept
        s['peak'] = max(self.equity(), s['peak']-swept)
        s['daysSinceSweep'] = 0
        s['sweepHistory'].insert(0, dict(day=s['day'], tick=s['tick'], trigger=trigger, eligible=eligible, swept=swept, date=self.now.date().isoformat()))
        if swept:
            s['vaultMovements'].insert(0, dict(day=s['day'], tick=s['tick'], type=trigger, amount=swept))
        return eligible, swept

    def close_day(self):
        s = self.s
        if s['dayClosed']:
            return
        s['daysSinceSweep'] += 1
        eligible, swept = self.sweep('VECKOAVSLUT') if s['daysSinceSweep'] >= 5 else (max(0, s['realizedTotal']-s['realizedHighWater']), 0)
        s['dayReports'].insert(0, dict(day=s['day'], date=s['reportDate'] or clock(self.now)[0], tick=s['tick'],
                                     dayNet=s['realizedTotal']-s['dayOpenRealized'], realized=s['realizedTotal'],
                                     eligible=eligible, swept=swept, trading=self.equity(), cash=s['cash'],
                                     invested=self.invested(), vault=s['vault'], total=self.equity()+s['vault'],
                                     openPositions=len(s['positions']), daysToSweep=5-s['daysSinceSweep']))
        s['dayClosed'] = True
        s['lastCloseTick'] = s['tick']
        self.expand()

    def open_day(self):
        s = self.s
        if s['dayClosed']:
            s['day'] += 1
            s['dayStartTick'] = s['tick']
            s['dayOpenRealized'] = s['realizedTotal']
            s['dayOpenEquity'] = self.equity()
            s['dayClosed'] = False

    def expand(self):
        s = self.s
        if s['halt'] or len(s['dayReports']) % 5 or sum(r['dayNet'] for r in s['dayReports'][:5]) <= 0:
            return
        if sum(v == 'AKTIV' for v in s['agentStatus'].values()) < math.ceil(s['agentCount']*.6):
            return
        target = next((n for value, n in [(200000, 30), (100000, 25), (50000, 20), (25000, 15), (15000, 12)] if self.equity() >= value), 10)
        for _ in range(min(2, max(0, target-s['agentCount']))):
            s['agentCount'] += 1
            a = self.agents()[-1]
            self.init_agent(a, True)
            self.announce(a['name']+' skapad i skuggläge')

    def capital(self, amount, kind):
        s = self.s
        if type(amount) not in (int, float) or not math.isfinite(amount) or amount <= 0 or amount > 1e8:
            raise ValueError('Invalid positive amount')
        if kind == 'release':
            if amount > s['vault']:
                raise ValueError('Insufficient protected balance')
            s['vault'] -= amount
            s['cash'] += amount
            s['peak'] += amount
            s['vaultMovements'].insert(0, dict(day=s['day'], tick=s['tick'], type='MANUELLT GODKÄND ÅTERFÖRING', amount=-amount))
        else:
            if kind == 'withdraw' and amount > s['cash']:
                raise ValueError('Insufficient free cash')
            delta = amount if kind == 'deposit' else -amount
            s['cash'] += delta
            s['ownerNetDeposits'] += delta
            s['peak'] = max(self.equity(), s['peak']+delta)
            s['dayOpenEquity'] += delta
            s['ownerMovements'].insert(0, dict(day=s['day'], tick=s['tick'], type='ÄGARINSÄTTNING' if delta > 0 else 'ÄGARUTTAG', amount=delta))

    def command(self, name, data):
        s = self.s
        if name in ('deposit', 'withdraw', 'release'):
            if name == 'release' and data.get('approved') is not True:
                raise ValueError('Explicit approval required')
            self.capital(data.get('amount'), name)
        elif name == 'start':
            self.open_day()
            s['running'] = True
        elif name == 'stop':
            s['running'] = False
            for order in s['orders']:
                if order['status'] in ('NEW', 'PARTIAL'):
                    self.event(order, 'CANCELLED', 'Manuellt nödstopp')
        elif name == 'pause':
            self.pause('Manuell riskpaus')
        elif name == 'approve':
            self.global_risk()
            if not s['approvalPending'] or not s['globalPause'] or (s['peak']-self.equity())/s['peak'] >= .10:
                raise ValueError('Recovery test has not passed')
            s['halt'], s['approvalPending'], s['globalPause'] = False, False, None
            self.announce('Du godkände återstart')
        elif name == 'close-day':
            s['running'] = False
            self.close_day()
        elif name == 'sweep':
            self.sweep('EXTRA VINSTSKYDD')
        elif name == 'settings':
            pace = data.get('autoPace', s['autoPace'])
            # Public Kraken polling is deliberately capped at one snapshot / 5s.
            if pace not in (5000, 30000):
                raise ValueError('Kraken interval must be 5000 or 30000 ms')
            s['autoPace'] = pace
            if 'marketAuto' in data:
                if type(data['marketAuto']) is not bool:
                    raise ValueError('marketAuto must be boolean')
                s['marketAuto'] = data['marketAuto']
        elif name == 'read-notices':
            for notice in s['notifications']:
                notice['read'] = True
        elif name == 'save':
            pass  # Every command is durably saved already.
        else:
            raise ValueError('Unknown command')
        self.audit()

    def audit(self):
        s = self.s
        for symbol, p in s['positions'].items():
            if abs(sum(p['owners'].values())-p['qty']) > 1e-7 or p['qty'] < -1e-9:
                raise ValueError('Position ownership mismatch')
            if abs(sum(cost.get(symbol, 0) for cost in s['ownerCost'].values())-p['cost']) > 1e-6:
                raise ValueError('Position cost basis mismatch')
        if min(s['cash'], s['vault']) < -1e-6:
            raise ValueError('Negative cash or protected balance')
        expected = s['ownerNetDeposits']+s['realizedTotal']-sum(p['cost'] for p in s['positions'].values())
        if abs(s['cash']+s['vault']-expected) > 1e-5:
            raise ValueError('Capital ledger mismatch')
        return True
