"""Live-capital supervision. Independent of archived simulator state.

No network research text can become executable code or an order authorization.
Savings are an internal EUR reserve, never an exchange-level withdrawal lock.
"""
import hashlib
import json
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_DOWN
from urllib.parse import urlsplit

from .kraken_private import ExchangeError
from .trade_history import history
from .approval import verify

AGENTS = {'gamma': Decimal('.4'), 'zeta': Decimal('.4'), 'iota': Decimal('.2')}
D = Decimal

def utc():
    return datetime.now(timezone.utc)


class Governance:
    def __init__(self, exchange):
        self.e = exchange
        self.store = exchange.store
        with self.store.lock:
            self.store.db.executescript('''
                CREATE TABLE IF NOT EXISTS supervision(key TEXT PRIMARY KEY,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS supervision_events(seq INTEGER PRIMARY KEY,at TEXT NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL);
                CREATE TRIGGER IF NOT EXISTS supervision_no_update BEFORE UPDATE ON supervision_events
                BEGIN SELECT RAISE(ABORT,'Supervision journal is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS supervision_no_delete BEFORE DELETE ON supervision_events
                BEGIN SELECT RAISE(ABORT,'Supervision journal is append-only'); END;
            ''')
            if self.load('savings') is None:
                self.save('savings',dict(startedAt=utc().isoformat(),reservedEUR='0',highWaterEUR='0',lastWeek=None),'SAVINGS_INITIALIZED')

    def load(self, key, default=None):
        with self.store.lock:
            row = self.store.db.execute('SELECT payload FROM supervision WHERE key=?',(key,)).fetchone()
        return json.loads(row[0]) if row else default

    def save(self, key, value, kind):
        with self.store.lock, self.store.db:
            self.store.db.execute('INSERT OR REPLACE INTO supervision VALUES(?,?)',(key,json.dumps(value)))
            self.store.db.execute('INSERT INTO supervision_events(at,kind,payload) VALUES(?,?,?)',(utc().isoformat(),kind,json.dumps(dict(key=key,value=value))))
        return value

    def fills(self):
        with self.store.lock:
            return [json.loads(r[0]) for r in self.store.db.execute('SELECT payload FROM live_fills')]

    def metrics(self):
        fills=self.fills();book = history(fills)
        fills_by_id={f['tradeId']:f for f in fills}
        result = {}
        for aid in AGENTS:
            # Partial fills and FIFO segments from one sell order count once.
            grouped = {}
            for row in book['closed']:
                if row.get('agentId') == aid:
                    grouped.setdefault(fills_by_id[row['sellTradeId']].get('orderId',row['sellTradeId']),[]).append(row)
            trades = [dict(id=ident,net=sum((D(r['netEUR']) for r in rows),D(0)),
                           at=max(r['soldAt'] for r in rows),pair=rows[0]['pair']) for ident,rows in grouped.items()]
            trades.sort(key=lambda r:(r['at'],r['id']))
            net = peak = drawdown = D(0)
            risk_net=risk_peak=risk_dd=D(0);risk_count=0;risk_streak=0
            since=self.agent(aid).get('riskSince')
            streak = 0
            for row in trades:
                net += row['net']; peak = max(peak,net); drawdown = max(drawdown,peak-net)
                streak = streak+1 if row['net']<0 else 0
                if not since or row['at']>=since:
                    risk_count+=1;risk_net+=row['net'];risk_peak=max(risk_peak,risk_net);risk_dd=max(risk_dd,risk_peak-risk_net)
                    risk_streak=risk_streak+1 if row['net']<0 else 0
            result[aid] = dict(riskNetEUR=str(risk_net),riskDrawdownEUR=str(risk_dd),riskTrades=risk_count,riskLossStreak=risk_streak,netEUR=str(net),drawdownEUR=str(drawdown),closedTrades=len(trades),
                wins=sum(t['net']>0 for t in trades),lossStreak=streak,
                averageWinEUR=str(sum((t['net'] for t in trades if t['net']>0),D(0))/max(1,sum(t['net']>0 for t in trades))),
                averageLossEUR=str(sum((t['net'] for t in trades if t['net']<0),D(0))/max(1,sum(t['net']<0 for t in trades))),
                feesEUR=str(sum((D(f['feeEUR']) for f in self.fills() if f.get('agentId')==aid),D(0))),
                openCostEUR=str(sum((D(l['costIncludingFeeEUR']) for l in book['open'] if l.get('agentId')==aid),D(0))))
        metadata=getattr(self.e.public,'metadata',{})
        try:fresh=0<=(utc()-datetime.fromisoformat(metadata['fetchedAt'])).total_seconds()<=30
        except (KeyError,ValueError,TypeError):fresh=False
        for aid,m in result.items():
            marked=D(0);known=fresh
            for lot in book['open']:
                if lot.get('agentId')!=aid:continue
                symbol='BTC-SIM' if lot['pair']=='XBTEUR' else 'ETH-SIM'
                try:marked+=D(lot['quantity'])*D(str(metadata['orderBooks'][symbol]['bids'][0][0]))/D(str(metadata['eurSek']))
                except (KeyError,ValueError,TypeError):known=False
            m['markedValueEUR']=str(marked) if known else None
            m['unrealizedEUR']=str(marked-D(m['openCostEUR'])) if known else None
            m['valuationAt']=metadata.get('fetchedAt') if known else None
        return result

    def agent(self, aid):
        if aid not in AGENTS:
            raise ExchangeError('Okänd liveagent.')
        return self.load('agent:'+aid,dict(agentId=aid,status='ACTIVE',reason='Pilotbudget; ingen automatisk budgetökning.',since=utc().isoformat(),version='rules-v1'))

    def supervise(self, budgets=None):
        metrics = self.metrics()
        for aid, m in metrics.items():
            a = self.agent(aid)
            if a['status'] in ('ACTIVE','LIMITED'):
                budget = D((budgets or {}).get(aid,'0'))
                # Absolute safety fallback; losses must be net of actual fees.
                threshold = max(D('1'),budget*D('.08'))
                if (m['riskTrades']>=10 and m['riskLossStreak']>=3 and D(m['riskNetEUR'])<0) or D(m['riskDrawdownEUR'])>=threshold:
                    a.update(status='QUARANTINE',since=utc().isoformat(),reason='Förlustbudget överskriden eller negativt netto och tre förluster i följd efter minst tio avslut.',baseline=m,budgetEUR=str(budget if budget>0 else D('10')),riskCapitalEUR=(self.e.sessions.active() or {}).get('capitalEUR','100'))
                    self.save('agent:'+aid,a,'AGENT_QUARANTINED')
                elif a['status']=='LIMITED' and m['riskTrades']>=20 and D(m['riskNetEUR'])>0 and utc()-datetime.fromisoformat(a['since'])>=timedelta(days=7):
                    a.update(status='ACTIVE',reason='Begränsad provperiod granskad; större budget kräver fortfarande en ny godkänd sessionsplan.')
                    self.save('agent:'+aid,a,'TRIAL_REVIEW_PASSED')
        return metrics

    def can_buy(self, aid):
        return self.agent(aid)['status'] in ('ACTIVE','LIMITED')

    def weights(self):
        # Reduce, never reward a short winning streak. Unassigned cash stays cash.
        metrics = self.metrics()
        return {aid:(D(0) if not self.can_buy(aid) else weight*(D('.5') if D(metrics[aid]['riskNetEUR'])<0 or self.agent(aid)['status']=='LIMITED' else D(1))) for aid,weight in AGENTS.items()}

    def reserve(self):
        return D(self.load('savings')['reservedEUR'])

    def trade_reviews(self, closed):
        fills={f['tradeId']:f for f in self.fills()}
        tickets={t['id']:t for t in self.e.tickets()}
        rows=[]
        for lot in closed:
            buy=fills[lot['buyTradeId']];sell=fills[lot['sellTradeId']]
            entry=tickets.get(buy.get('ticketId'),{}).get('decision',{})
            exit=tickets.get(sell.get('ticketId'),{}).get('decision',{})
            qty=D(lot['quantity'])
            allocated_fees=D(buy['feeEUR'])*qty/D(buy['quantity'])+D(sell['feeEUR'])*qty/D(sell['quantity'])
            net=D(lot['netEUR']);gross=net+allocated_fees
            finding='Prisrörelsen var negativ; granska signal och marknadsläge.' if gross<0 else 'Avgifter åt upp en positiv prisrörelse.' if net<0 else 'Positivt realiserat netto.'
            rows.append(dict(**lot,allocatedFeesEUR=str(allocated_fees),grossEUR=str(gross),finding=finding,
                entryReason=entry.get('report',{}).get('reason','Beslutsunderlag saknas för äldre/manuell order.'),
                exitReason=exit.get('report',{}).get('reason','Beslutsunderlag saknas för äldre/manuell order.'),
                strategyVersion=entry.get('version'),entryPlan=entry.get('exitPlan'),
                proposedReview='Testa kostnadströskel och färre återinträden på separat historik.' if net<0 else 'Behåll som observation; ingen automatisk budgethöjning.'))
        return rows

    def weekly(self, available, at=None):
        with self.e.lock,self.store.lock,self.store.db:
            return self._weekly(available,at)

    def _weekly(self, available, at=None):
        at = at or utc()
        savings = self.load('savings')
        start = datetime.fromisoformat(savings['startedAt'])
        period = int((at-start).total_seconds()//(7*86400))
        if period < 1 or savings['lastWeek'] == period:
            return savings
        book = history(self.fills())
        cutoff=start+timedelta(days=period*7)
        eligible=[r for r in book['closed'] if datetime.fromisoformat(r['soldAt'])<=cutoff]
        net = sum((D(r['netEUR']) for r in eligible),D(0))
        # Unknown acquisition costs prevent a trustworthy net-profit sweep.
        if book['unmatchedSales']:
            return savings
        incremental = max(D(0),net-D(savings['highWaterEUR']))
        exact_due=incremental*D('.2')+D(savings.get('carryEUR','0'))
        due = exact_due.quantize(D('.01'),rounding=ROUND_DOWN)
        free = max(D(0),D(str(available))-self.reserve())
        # Defer the whole sweep if cash is unavailable; don't lose the entitlement.
        if due>free:
            return savings
        prior = D(savings.get('lastNetEUR','0'))
        report = dict(period=period,at=at.isoformat(),periodEnd=cutoff.isoformat(),netEUR=str(net-prior),cumulativeNetEUR=str(net),
            reservedEUR=str(due),agents=self.metrics(),trades=self.trade_reviews([r for r in eligible if not savings.get('lastCutoff') or r['soldAt']>savings['lastCutoff']]),
            reviewRequired=net<prior,conclusion='Förlustperiod: granska signaler, avgifter och exekvering; orsaker kan inte bevisas av resultatet ensamt.' if net<prior else 'Ingen automatisk strategiändring.')
        savings.update(reservedEUR=str(self.reserve()+due),highWaterEUR=str(max(D(savings['highWaterEUR']),net)),lastWeek=period,lastNetEUR=str(net),carryEUR=str(exact_due-due),lastCutoff=cutoff.isoformat())
        self.save('weekly:'+str(period),report,'WEEKLY_REVIEW')
        return self.save('savings',savings,'WEEKLY_RESERVE')

    def check_cash(self, available, spending):
        if D(str(available))-D(str(spending))<self.reserve():
            raise ExchangeError('Köpet skulle använda CHEF:s skyddade EUR-reserv.')

    def allow_idea(self, aid, pair, bar, side):
        if side != 'buy':
            return True
        if not self.can_buy(aid):
            return False
        fills = self.fills()
        # One shared idea per pair/bar, across agents and session restarts.
        for ticket in self.e.tickets():
            if ticket.get('pair')==pair and ticket.get('side')=='buy' and ticket.get('signalBar')==bar and ticket.get('state') not in ('REJECTED','VALIDATION_FAILED'):
                return False
        # Do not add to an existing shared position without a separately approved pyramiding policy.
        if any(lot['pair']==pair for lot in history(fills)['open']):return False
        closed = [r for r in history(fills)['closed'] if r['pair']==pair and D(r['netEUR'])<0]
        if closed:
            latest = max(datetime.fromisoformat(r['soldAt']) for r in closed)
            if utc()-latest<timedelta(minutes=30):
                return False
        return True

    def exit_plan(self, analysis):
        # ATR is in EUR. Freeze this distance at entry, do not move stops away.
        close = D(str(analysis.get('close',0)))
        atr = D(str(analysis.get('atr14',0)))
        distance = min(D('.08'),max(D('.02'),D(3)*atr/close)) if close>0 and atr>0 else D('.05')
        return dict(version='atr-v1',lossFraction=str(distance),reason='Tre ATR, begränsat till 2–8 %; fast vid inträde.',riskFraction='.005',protection='LOCAL_SESSION_ONLY')

    def stop_fraction(self, aid, pair):
        # Strictest remaining entry plan; legacy positions keep their original 5%.
        ids={lot['buyTradeId'] for lot in history(self.fills())['open'] if lot.get('agentId')==aid and lot['pair']==pair}
        orders={f['orderId'] for f in self.fills() if f['tradeId'] in ids and 'orderId' in f}
        fractions=[]
        for t in self.e.tickets():
            if t.get('exchangeOrderId') in orders and t.get('decision',{}).get('exitPlan'):
                fractions.append(D(t['decision']['exitPlan']['lossFraction']))
        return min(fractions,default=D('.05'))

    def shadow_tick(self, state, metadata):
        for aid in AGENTS:
            agent = self.agent(aid)
            if agent['status'] not in ('QUARANTINE','SHADOW'):
                continue
            shadow = self.load('shadow:'+aid,dict(agentId=aid,startedAt=utc().isoformat(),lastBars={},positions={},trades=[],equityEUR='0',peakEUR='0',maxDrawdownEUR='0'))
            if shadow.get('cycleAt')!=agent['since']:
                if shadow.get('trades') or shadow.get('positions'):
                    self.save('shadow-archive:'+aid+':'+shadow['startedAt'],shadow,'SHADOW_ARCHIVED')
                shadow=dict(agentId=aid,cycleAt=agent['since'],startedAt=utc().isoformat(),lastBars={},positions={},trades=[],equityEUR='0',peakEUR='0',maxDrawdownEUR='0')
            before=json.dumps(shadow,sort_keys=True)
            for symbol,pair in {'BTC-SIM':'XBTEUR','ETH-SIM':'ETHEUR'}.items():
                if aid=='iota' and symbol!='BTC-SIM':continue
                analysis=state.get('marketAnalysis',{}).get(symbol,{})
                report=state.get('agentReports',{}).get(aid+'|'+symbol,{})
                try:age=(utc()-datetime.fromisoformat(analysis['closedAt'])).total_seconds()
                except (KeyError,ValueError,TypeError):continue
                if analysis.get('status')!='ready' or not 0<=age<300:continue
                try:
                    raw=metadata['orderBooks'][symbol];rate=D(str(metadata['eurSek']))
                    agebook=(utc()-datetime.fromisoformat(metadata['fetchedAt'])).total_seconds()
                    if not 0<=agebook<=15:continue
                    book={side:[[str(D(str(level[0]))/rate),str(level[1])] for level in raw[side]] for side in ('bids','asks')}
                    from .shadow import update
                    update(shadow,pair,book,metadata['pairRules'][symbol],report.get('side'),analysis['barId'],self.exit_plan(analysis),utc(),agent.get('budgetEUR','10'),agent.get('riskCapitalEUR','100'))
                except (KeyError,ValueError,TypeError):continue
            net=sum((D(t['netEUR']) for t in shadow['trades']),D(0));unrealized=D(0)
            for pair,p in shadow['positions'].items():
                symbol='BTC-SIM' if pair=='XBTEUR' else 'ETH-SIM'
                try:unrealized+=D(p['quantity'])*D(str(metadata['orderBooks'][symbol]['bids'][0][0]))/D(str(metadata['eurSek']))*D('.996')-D(p['costEUR'])
                except KeyError:continue
            equity=net+unrealized;peak=max(D(shadow['peakEUR']),equity)
            shadow.update(equityEUR=str(equity),peakEUR=str(peak),maxDrawdownEUR=str(max(D(shadow['maxDrawdownEUR']),peak-equity)),netEUR=str(net))
            if json.dumps(shadow,sort_keys=True)!=before:self.save('shadow:'+aid,shadow,'SHADOW_OBSERVATION')
            duration=utc()-datetime.fromisoformat(shadow['startedAt'])
            ready=self.shadow_ready(shadow)
            if ready and agent['status']!='REVIEW_READY':
                agent.update(status='REVIEW_READY',reason='Minst sju dagar och 20 skuggavslut; mänsklig granskning krävs.')
                self.save('agent:'+aid,agent,'SHADOW_REVIEW_READY')
            elif agent['status']=='REVIEW_READY' and not ready:
                agent.update(status='SHADOW',reason='Skuggprovet uppfyller inte längre granskningskraven.')
                self.save('agent:'+aid,agent,'SHADOW_REVIEW_REVOKED')
            elif agent['status']=='QUARANTINE':
                agent.update(status='SHADOW');self.save('agent:'+aid,agent,'SHADOW_STARTED')

    def shadow_ready(self, shadow):
        return bool(shadow and utc()-datetime.fromisoformat(shadow['startedAt'])>=timedelta(days=7) and len(shadow['trades'])>=20 and D(shadow.get('netEUR','0'))>0 and D(shadow.get('equityEUR','0'))>0 and D(shadow['maxDrawdownEUR'])<=D('2') and not shadow.get('pending') and not shadow.get('positions'))

    def review_subject(self, aid):
        return hashlib.sha256(json.dumps(dict(agent=self.agent(aid),shadow=self.load('shadow:'+aid)),sort_keys=True).encode()).hexdigest()

    def quarantine(self,data):
        if set(data)!={'agent','reason'} or not isinstance(data['reason'],str) or not 1<=len(data['reason'])<=500:raise ExchangeError('Agent och orsak krävs.')
        aid=data['agent']
        with self.store.lock:
            a=self.agent(aid)
            if a['status'] not in ('ACTIVE','LIMITED'):return a
            session=self.e.sessions.active()
            budget=(session or {}).get('budgets',{}).get(aid,'10')
            a.update(status='QUARANTINE',since=utc().isoformat(),reason=data['reason'],budgetEUR=budget,riskCapitalEUR=(session or {}).get('capitalEUR','100'),baseline=self.metrics()[aid])
            return self.save('agent:'+aid,a,'OWNER_QUARANTINE')

    def review(self, data, approval):
        with self.e.lock:
            return self._review(data,approval)

    def _review(self, data, approval):
        if set(data)!={'agent'}:raise ExchangeError('Exakt agent-ID krävs.')
        aid=data['agent'];agent=self.agent(aid)
        if self.e.sessions.active():raise ExchangeError('Avsluta sessionen före agentgranskning.')
        if agent['status']!='REVIEW_READY' or not self.shadow_ready(self.load('shadow:'+aid)):raise ExchangeError('Agenten har inte klarat skugggranskningen.')
        subject=self.review_subject(aid)
        token=verify(self.store.operator_key,approval or '', 'agent-review',subject=subject)
        with self.store.lock,self.store.db:
            if self.review_subject(aid)!=subject:raise ExchangeError('Granskningsunderlaget ändrades.')
            try:self.store.db.execute('INSERT INTO live_approvals VALUES(?,?)',(token['nonce'],'agent:'+aid))
            except Exception:raise ExchangeError('Godkännandet har redan använts.') from None
            agent.update(status='LIMITED',since=utc().isoformat(),riskSince=utc().isoformat(),reason='Ägaren godkände begränsad provbudget. Ny session krävs.',reviewedBy=token['actor'])
            return self.save('agent:'+aid,agent,'AGENT_REVIEW_APPROVED')

    def research(self, data):
        if set(data)!={'title','url','hypothesis','entry','exit','risk'}:
            raise ExchangeError('Titel, källa, hypotes samt köp-, sälj- och riskregler krävs.')
        if any(not isinstance(v,str) or not 1<=len(v)<=1500 for v in data.values()):
            raise ExchangeError('Alla researchfält måste vara text på 1–1500 tecken.')
        url=urlsplit(data['url'])
        if url.scheme!='https' or not url.hostname or url.username or url.password:
            raise ExchangeError('Ange en HTTPS-källa utan inloggningsuppgifter.')
        ident=hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()[:24]
        existing=self.load('research:'+ident)
        if existing:return existing
        row=dict(id=ident,**data,status='UNVERIFIED',createdAt=utc().isoformat(),liveEligible=False,
                 nextStep='Oberoende historiskt test, separat testperiod och skugghandel krävs. Innehållet kan inte aktivera kod eller order.')
        return self.save('research:'+ident,row,'RESEARCH_SUBMITTED')

    def status(self):
        with self.store.lock:
            reports=[json.loads(r[0]) for r in self.store.db.execute("SELECT payload FROM supervision WHERE key LIKE 'weekly:%' ORDER BY rowid DESC LIMIT 12")]
            research=[json.loads(r[0]) for r in self.store.db.execute("SELECT payload FROM supervision WHERE key LIKE 'research:%' ORDER BY rowid DESC LIMIT 50")]
        metrics=self.metrics()
        return dict(agents=[dict(**self.agent(aid),metrics=metrics[aid],shadow=self.load('shadow:'+aid)) for aid in AGENTS],
            savings=self.load('savings'),weeklyReports=reports,research=research,
            market=dict(source='Kraken REST',refreshSeconds=15,signals='Avslutade femminutersstaplar',stream=self.e.stream.status(),privateStream=self.e.execution_stream.status()),
            limitations=['EUR-reserven är intern; separata pengar kräver ett konto utanför API-nyckelns åtkomst.',
                          'Skugghandel uppskattar delavslut från 10 % av synligt djup, med 60 sekunders giltighet, 0,4 % avgift och 0,1 % prisglidning per sida. Verklig köposition är okänd.',
                          'Ingen automatisk internet-/YouTube-inhämtning eller strategiaktivering.',
                          'Skydd är lokalt och kräver aktiv session/server. Inga börslagrade stopporder eller svensk aktieanslutning.'])
