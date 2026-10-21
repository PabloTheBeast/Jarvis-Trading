"""Browser-independent paper orchestration.

Signals, order lifecycle, simulated broker and exact integer money ledger live in
separate components. The public state projection preserves the dashboard schema.
"""
import math
from datetime import date, datetime, timezone, timedelta
from .calendar import clock, session
from .strategies import signal
from .orders import OrderManager
from .broker import PaperBroker
from .accounting import Ledger
from .analysis import correlation as candle_correlation
from .advisory import review as review_advisories

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
             reportDate=None, schemaVersion=2, closedDates=[], expansionDates=[],
             marketAnalysis={}, signalDecisions={}, lastDecisions=[], revision=0,
             lastExpansionReview=0, intentOrders={}, approvals=[], capitalHistory=[], stopGeneration=0, paperBroker=dict(spreadBps=10, slippageBps=5, testFaults=False))
    for key in ('agentSpend', 'agentRealized', 'agentLosses', 'agentHalt', 'ownerCost',
                'agentStatus', 'agentTrades', 'agentWins', 'agentGrossProfit',
                'agentGrossLoss', 'agentPeak', 'agentMaxDD', 'agentEquityHist', 'riskBaseline'):
        s[key] = {}
    engine = Engine(s)
    for a in engine.agents():
        engine.init_agent(a, False)
    Ledger(engine).movement('OPENING', 10000*1_000_000_000, 0, 'Initial paper capital')
    return s


class Engine:
    def __init__(self, state):
        self.s = state
        self.now = datetime.now(timezone.utc)
        self.operator = None
        self.approval_id = None

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
        if 'book' in s:
            s['book']['agentRealized'][aid]=0
            s['book']['riskBaseline'][aid]=0
            s['book']['ownerCost'][aid]={}
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
        self.s['logs'].insert(0, dict(tick=self.s['tick'], agent=who, msg=msg, at=self.now.isoformat(), level='INFO'))
        del self.s['logs'][300:]

    def announce(self, msg):
        self.s['notifications'].insert(0, dict(tick=self.s['tick'], day=self.s['day'], msg=msg,
                                             date=self.now.isoformat(), read=False))
        self.log('CHEF', msg)

    def random(self):
        self.s['rngState'] = (1664525 * self.s['rngState'] + 1013904223) & 0xffffffff
        return self.s['rngState'] / 4294967296

    def regime(self, symbol):
        analysis = self.s.get('marketAnalysis', {}).get(symbol)
        if analysis:
            return analysis.get('trend', 'neutral') if analysis.get('status') == 'ready' else 'neutral'
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
        if self.s.get('marketAnalysis') and (crypto(x) or crypto(y)):
            if not (crypto(x) and crypto(y)):
                return 1.0
            return candle_correlation(self.s['marketAnalysis'].get(x,{}),self.s['marketAnalysis'].get(y,{}))
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
        return OrderManager(self).buy_room(a, symbol)

    def event(self, order, status, detail):
        return OrderManager(self).event(order, status, detail)

    def submit(self, aid, symbol, side, amount, reason='Strategisignal', intent_id=None):
        return OrderManager(self).submit(aid, symbol, side, amount, reason, intent_id)

    def fill(self, order, agent):
        return OrderManager(self).fill(order, agent)

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
            book[key] = dict(entry=PaperBroker(self).quote(symbol, 'BUY'), step=self.s['tick'], symbol=symbol)
        elif side == 'SELL' and key in book:
            self.close_shadow(q, key)

    def close_shadow(self, q, key):
        p = q['shadow'].pop(key)
        pnl = 200*(PaperBroker(self).quote(p['symbol'], 'SELL')/p['entry']-1)-400*FEE
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
            good = good and s['peak'] > 0 and (s['peak']-self.equity())/s['peak'] < .10
        if not good:
            q['next' if global_pause else 'until'] = s['tick']+20
        return good

    def signal(self, a, symbol):
        analysis=self.s.get('marketAnalysis',{}).get(symbol)
        if crypto(symbol) and analysis is not None:
            if analysis.get('status')!='ready':
                return None
            stamp=analysis['barId']
            key=a['id']+'|'+symbol
            if self.s.setdefault('signalDecisions',{}).get(key)==stamp:
                return None
            self.s['signalDecisions'][key]=stamp
            side=analysis.get('signals',{}).get(a['strategy'])
            self.s.setdefault('lastDecisions',[]).insert(0,dict(agent=a['name'],instrument=symbol,
                strategy=a['strategy'],side=side or 'WAIT',barId=stamp,at=self.now.isoformat(),
                reason=analysis.get('reasons',{}).get(a['strategy'],analysis.get('reason',''))))
            del self.s['lastDecisions'][200:]
            return side
        return signal(self.s, a, symbol)

    def observe(self,prices,metadata):
        """Public market refresh: mark holdings and analyse, never create a fill."""
        required={'BTC-SIM','ETH-SIM'}
        if set(prices)!=required or any(not math.isfinite(v) or v<=0 for v in prices.values()):
            raise ValueError('Invalid public market snapshot')
        if not metadata or metadata.get('connector')!='kraken-public':
            raise ValueError('A verified public Kraken connector is required')
        age=(self.now-datetime.fromisoformat(metadata['fetchedAt'])).total_seconds()
        if not 0<=age<=30 or set(metadata.get('orderBooks',{}))!=required:
            raise ValueError('Missing or stale public order books')
        if metadata.get('snapshotId')!=self.s.get('lastObservedSnapshot'):
            for symbol,price in prices.items():
                self.s['prices'][symbol].append(price)
                del self.s['prices'][symbol][:-256]
            self.s['lastObservedSnapshot']=metadata['snapshotId']
        self.s['marketData']=dict(metadata,status='ok',at=self.now.isoformat(),source='Kraken EUR / ECB SEK',error=None)
        self.s['marketAnalysis']=metadata.get('analysis',{})
        self.global_risk()
        review_advisories(self)

    def step(self, prices, now=None, metadata=None):
        s = self.s
        self.now = now or datetime.now(timezone.utc)
        if set(prices) != {'BTC-SIM', 'ETH-SIM'} or any(not math.isfinite(v) or v <= 0 for v in prices.values()):
            raise ValueError('A complete valid Kraken SEK snapshot is required')
        if metadata and metadata.get('snapshotId') and metadata['snapshotId'] == s.get('lastSnapshotId'):
            return
        if metadata and metadata.get('fetchedAt'):
            age = (self.now-datetime.fromisoformat(metadata['fetchedAt'])).total_seconds()
            if not 0 <= age <= 30:
                raise ValueError('Market snapshot is stale or from the future')
        date, _ = clock(self.now)
        if s['reportDate'] and date < s['reportDate']:
            raise ValueError('Clock moved backwards; no trading step executed')
        if s['reportDate'] and s['reportDate'] != date:
            self.close_day()
            self.open_day()
        s['reportDate'] = date
        self.open_day()
        if metadata and metadata.get('snapshotId'):
            s['lastSnapshotId'] = metadata['snapshotId']
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
        if metadata:
            s['marketData'].update(metadata)
            if 'analysis' in metadata:
                s['marketAnalysis']=metadata['analysis']
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
                Ledger(self).reset_risk_baseline(aid)
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
        review_advisories(self)
        del s['hist'][:-2000]
        self.audit()

    def sweep(self, trigger):
        return Ledger(self).sweep(trigger)

    def close_day(self):
        s = self.s
        report_date = s['reportDate'] or clock(self.now)[0]
        if s['dayClosed'] or report_date in s.setdefault('closedDates', []):
            return
        s['reportDate'] = report_date
        s['closedDates'].append(report_date)
        day = date.fromisoformat(report_date)
        anchor = date.fromisoformat(s.setdefault('weeklyLastDate', (day-timedelta(days=1)).isoformat()))
        eligible, swept = max(0, s['realizedTotal']-s['realizedHighWater']), 0
        if (day-anchor).days >= 7:
            eligible, swept = self.sweep('VECKOAVSLUT')
            s['weeklyLastDate'] = report_date
            anchor = day
        s['nextSweepDate'] = (anchor+timedelta(days=7)).isoformat()
        s['daysSinceSweep'] = (day-anchor).days
        s['dayReports'].insert(0, dict(day=s['day'], date=report_date, tick=s['tick'],
                                     dayNet=s['realizedTotal']-s['dayOpenRealized'], realized=s['realizedTotal'],
                                     eligible=eligible, swept=swept, trading=self.equity(), cash=s['cash'],
                                     invested=self.invested(), vault=s['vault'], total=self.equity()+s['vault'],
                                     openPositions=len(s['positions']), daysToSweep=max(0, 7-s['daysSinceSweep']),
                                     stockSession=bool(session(day))))
        s['dayClosed'] = True
        s['lastCloseTick'] = s['tick']
        if session(day):
            s.setdefault('expansionDates', []).append(report_date)
            self.expand()
        self.log('CAPITAL', 'Dagsavslut '+report_date)

    def open_day(self):
        s = self.s
        if s['dayClosed']:
            if clock(self.now)[0] <= s['reportDate']:
                raise ValueError('Dagen är avslutad. Nästa handelsdag kan öppnas först nästa Stockholm-datum.')
            s['day'] += 1
            s['dayStartTick'] = s['tick']
            s['dayOpenRealized'] = s['realizedTotal']
            s['dayOpenEquity'] = self.equity()
            s['dayClosed'] = False
            s['reportDate'] = clock(self.now)[0]

    def expand(self):
        s = self.s
        days = s.setdefault('expansionDates', [])
        previous = s.setdefault('lastExpansionReview', 0)
        if len(days)-previous < 5:
            return
        s['lastExpansionReview'] = len(days)
        recent = set(days[-5:])
        if s['halt'] or s['globalPause'] or sum(r['dayNet'] for r in s['dayReports'] if r['date'] in recent) <= 0:
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
        return Ledger(self).capital(amount, kind)

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
            s['stopGeneration'] = s.get('stopGeneration', 0)+1
            for order in s['orders']:
                if order['status'] in ('NEW', 'PARTIAL'):
                    self.event(order, 'CANCELLED', 'Manuellt nödstopp')
        elif name == 'pause':
            self.pause('Manuell riskpaus')
        elif name == 'approve':
            if not self.operator:
                raise ValueError('Signed owner approval required')
            self.global_risk()
            if not s['approvalPending'] or not s['globalPause'] or s['peak'] <= 0 or (s['peak']-self.equity())/s['peak'] >= .10:
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
        return Ledger(self).audit()
