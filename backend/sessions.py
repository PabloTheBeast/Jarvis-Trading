"""Owner-approved, bounded Kraken trading sessions. No automatic restart consent.

Only verified exchange fills create agent inventory; deposits are never profits or
new authority. One in-flight ticket globally, durable intents, no transport retries.
"""
import hashlib
import json
import os
import threading
import uuid
from datetime import datetime,timedelta,timezone
from decimal import Decimal,ROUND_DOWN,ROUND_UP
from zoneinfo import ZoneInfo

from .approval import verify
from .live import ACTIVE,decimal,fingerprint
from .kraken_private import ExchangeError,ExchangeUnknown
from .market import PAIRS
from .trade_history import history

AGENTS={'gamma':Decimal('.4'),'zeta':Decimal('.4'),'iota':Decimal('.2')}
ACTIVE_SESSIONS={'RUNNING','STOPPING','PAUSED','RECOVERY'}
PLAN_FIELDS=('id','createdAt','expiresAt','durationMinutes','capitalEUR','newCashEUR','budgets','baseline','endPolicy','maxLossEUR','maxOrderEUR')


class StaleDecision(ExchangeError):
    """A bar expired before any order was sent; wait for a fresh decision."""


def now():return datetime.now(timezone.utc)

def digest(plan):
    return hashlib.sha256(json.dumps({key:plan[key] for key in PLAN_FIELDS},sort_keys=True,separators=(',',':')).encode()).hexdigest()


class Sessions:
    def __init__(self,exchange,enabled=None):
        self.e=exchange;self.store=exchange.store;self.worker_lock=threading.Lock()
        self.enabled=os.environ.get('JARVIS_AUTO_TRADING')=='1' if enabled is None else enabled
        with self.store.lock:
            self.store.db.executescript('''
                CREATE TABLE IF NOT EXISTS trading_sessions(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS session_intents(id TEXT PRIMARY KEY,session TEXT NOT NULL,ticket TEXT,status TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS session_requests(id TEXT PRIMARY KEY,request TEXT NOT NULL,session TEXT);
                CREATE TABLE IF NOT EXISTS session_events(seq INTEGER PRIMARY KEY,at TEXT NOT NULL,session TEXT NOT NULL,event TEXT NOT NULL,payload TEXT NOT NULL);
                CREATE TRIGGER IF NOT EXISTS session_events_no_update BEFORE UPDATE ON session_events
                BEGIN SELECT RAISE(ABORT,'Session journal is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS session_events_no_delete BEFORE DELETE ON session_events
                BEGIN SELECT RAISE(ABORT,'Session journal is append-only'); END;
            ''')
            for s in self.all():
                if s['status'] in ACTIVE_SESSIONS:
                    s.update(status='RECOVERY',allowEndSales=False,reason='Processen startades om. Ingen handel återupptas. Avsluta och stäm av sessionen.')
                    self.save(s,'RESTART_PAUSED')

    def all(self):
        with self.store.lock:return [json.loads(row[0]) for row in self.store.db.execute('SELECT payload FROM trading_sessions ORDER BY rowid DESC')]

    def get(self,ident):
        with self.store.lock:row=self.store.db.execute('SELECT payload FROM trading_sessions WHERE id=?',(ident,)).fetchone()
        if not row:raise ExchangeError('Sessionen finns inte.')
        s=json.loads(row[0])
        if s.get('fingerprint')!=digest(s):raise ExchangeError('Sessionsplanen har ändrats. Ny granskning krävs.')
        return s

    def save(self,s,event):
        s['updatedAt']=now().isoformat()
        with self.store.lock,self.store.db:
            self.store.db.execute('INSERT OR REPLACE INTO trading_sessions VALUES(?,?)',(s['id'],json.dumps(s)))
            self.store.db.execute('INSERT INTO session_events(at,session,event,payload) VALUES(?,?,?,?)',(s['updatedAt'],s['id'],event,json.dumps(s)))
        return s

    def active(self):return next((s for s in self.all() if s['status'] in ACTIVE_SESSIONS),None)

    def status(self):
        session=self.active() or next(iter(self.all()),None)
        spent=sum((decimal(f['costEUR'])+decimal(f['feeEUR']) for f in self.fills() if session and f.get('sessionId')==session['id'] and f['side']=='buy'),Decimal(0))
        return dict(enabled=self.enabled and self.e.enabled and not self.e.monitor_only,session=session,grossBuysEUR=str(spent),
            remainingNewBuysEUR=str(max(Decimal(0),decimal(session['newCashEUR'])-spent)) if session else '0',
            automaticRestart=False,scope='Kraken BTC/EUR and ETH/EUR only',maximumCapitalEUR='100',
            instructions='Förbered plan, skapa session-start-kod lokalt och klicka Starta trading. Ingen insättning startar en session.')

    def fills(self):
        with self.store.lock:return [json.loads(r[0]) for r in self.store.db.execute('SELECT payload FROM live_fills')]

    def owned(self):
        return [lot for lot in history(self.fills())['open'] if lot.get('agentId') in AGENTS]

    def quotes(self):
        self.e.public.fetch();m=self.e.public.metadata
        age=(now()-datetime.fromisoformat(m['fetchedAt'])).total_seconds()
        if not 0<=age<=15:raise ExchangeError('Aktuella verifierade orderböcker krävs.')
        rate=decimal(m['eurSek'],True)
        return m,{pair:decimal(m['orderBooks'][symbol]['bids'][0][0],True)/rate for symbol,pair in PAIRS.items()}

    def plan(self,data):
        if self.active():raise ExchangeError('Avsluta tidigare session först.')
        if set(data)!={'capitalEUR','endPolicy','durationMinutes'}:raise ExchangeError('Ogiltiga planfält.')
        cap=decimal(data['capitalEUR'],True)
        if cap>100:raise ExchangeError('Pilotens totala kapitaltak är 100 EUR.')
        minutes=data['durationMinutes']
        if type(minutes) is not int or not 15<=minutes<=720:raise ExchangeError('Välj 15–720 minuter.')
        if data['endPolicy'] not in ('assess','hold'):raise ExchangeError('Välj CHEF-bedömning eller behåll alla.')
        with self.e.lock:
            self.e.inspect()
            if any(t['state'] in ACTIVE or t.get('historyError') for t in self.e.tickets()):raise ExchangeError('Alla tidigare order och avslut måste stämmas av först.')
            m,bids=self.quotes();balances,orders=self.e.account()
            if orders:raise ExchangeError('Kontot får inte ha öppna order när sessionen startas.')
            baseline={asset:values['balance'] for asset,values in balances.items()}
            equity=decimal(baseline['EUR'])+decimal(baseline['XBT'])*bids['XBTEUR']+decimal(baseline['ETH'])*bids['ETHEUR']
            invested=equity-decimal(baseline['EUR'])
            owned=self.owned();carry={aid:sum((decimal(lot['costIncludingFeeEUR']) for lot in owned if lot['agentId']==aid),Decimal(0)) for aid in AGENTS}
            for asset,pair in [('XBT','XBTEUR'),('ETH','ETHEUR')]:
                qty=sum((decimal(lot['quantity']) for lot in owned if lot['pair']==pair),Decimal(0))
                if qty>decimal(baseline[asset])+Decimal('.00000001'):raise ExchangeError('Agenternas registrerade innehav stämmer inte med Kraken. Manuell avstämning krävs.')
            if sum(carry.values())>cap:raise ExchangeError('Befintliga agentinnehav överstiger godkänt kapitaltak.')
            cash=max(Decimal(0),min(cap-sum(carry.values()),decimal(balances['EUR']['available'])*Decimal('.99'),(equity*Decimal('.4')-invested)*Decimal('.99')))
            created=now();local=created.astimezone(ZoneInfo('Europe/Stockholm'))
            midnight=(local+timedelta(days=1)).replace(hour=0,minute=0,second=0,microsecond=0).astimezone(timezone.utc)
            s=dict(id='SESSION-'+uuid.uuid4().hex,createdAt=created.isoformat(),expiresAt=min(created+timedelta(minutes=minutes),midnight).isoformat(),durationMinutes=minutes,
                capitalEUR=str(cap),newCashEUR=str(cash),budgets={aid:str(carry[aid]+cash*weight) for aid,weight in AGENTS.items()},
                baseline=baseline,endPolicy=data['endPolicy'],maxLossEUR=str(min(Decimal('10'),equity*Decimal('.03'))),maxOrderEUR=str(self.e.maximum),
                initialEquityEUR=str(equity),peakEquityEUR=str(equity),status='PLANNED',allowEndSales=True,endDecisions=[],reason='Inväntar ditt godkännande.')
            s['fingerprint']=digest(s)
            return self.save(s,'PLAN')

    def run(self,action,data,key,approval=None):
        request=json.dumps(dict(action=action,data=data),sort_keys=True)
        with self.store.lock:prior=self.store.db.execute('SELECT request,session FROM session_requests WHERE id=?',(key,)).fetchone()
        if prior:
            if prior[0]!=request:raise ExchangeError('Kommandonyckeln har redan använts.')
            return self.get(prior[1])
        if action=='plan':result=self.plan(data)
        else:
            if set(data)!={'session'}:raise ExchangeError('Exakt sessions-ID krävs.')
            s=self.get(data['session'])
            if action=='start':
                if not self.status()['enabled']:raise ExchangeError('Automatisk livehandel är avstängd i serverkonfigurationen.')
                token=verify(self.store.operator_key,approval or '','session-start',subject=s['fingerprint'])
                if s['status']!='PLANNED' or not 0<=(now()-datetime.fromisoformat(s['createdAt'])).total_seconds()<=300 or now()>=datetime.fromisoformat(s['expiresAt']):raise ExchangeError('Ny sessionsplan krävs.')
                if decimal(s['newCashEUR'])<=0 and not self.owned():raise ExchangeError('Inget kapital finns för sessionen. Ingen insättning krävs för bevakning.')
                with self.e.lock:
                    self.e.inspect();balances,orders=self.e.account()
                    if orders or any(t['state'] in ACTIVE for t in self.e.tickets()):raise ExchangeError('Öppna eller okända order blockerar start.')
                    self.check_balances(s,balances)
                    with self.store.lock,self.store.db:
                        if self.active():raise ExchangeError('En annan session pågår.')
                        if self.get(s['id'])['status']!='PLANNED':raise ExchangeError('Starten har avbrutits. Skapa en ny plan.')
                        if now()>=datetime.fromisoformat(s['expiresAt']):raise ExchangeError('Sessionens sluttid har passerat. Skapa en ny plan.')
                        verify(self.store.operator_key,approval or '','session-start',subject=s['fingerprint'])
                        self.store.db.execute('INSERT INTO live_approvals VALUES(?,?)',(token['nonce'],s['id']))
                        s.update(status='RUNNING',actor=token['actor'],approvalId=token['nonce'],startedAt=now().isoformat(),reason='Trading godkänd. Budgetar är interna gränser; pengarna ligger kvar på Kraken.')
                        self.save(s,'START')
                result=s
            elif action=='stop':
                # Must not wait for the worker/network lock: revoke new buys immediately.
                with self.store.lock:
                    s=self.get(s['id'])
                    if s['status'] in ACTIVE_SESSIONS:
                        s.setdefault('stopRequestedAt',now().isoformat())
                        s.update(status='STOPPING',reason='Nya köp stoppade. Annullering och innehavsbedömning pågår.')
                        self.save(s,'STOP_REQUESTED')
                    elif s['status']=='PLANNED':
                        s.update(status='CANCELLED',reason='Planen avbruten. Ingen handel startades.');self.save(s,'PLAN_CANCELLED')
                result=s
            else:raise ExchangeError('Okänd sessionsåtgärd.')
        with self.store.lock,self.store.db:
            self.store.db.execute('INSERT OR IGNORE INTO session_requests VALUES(?,?,?)',(key,request,result['id']))
        return result

    def check_balances(self,s,balances):
        expected={asset:decimal(value) for asset,value in s['baseline'].items()}
        for f in self.fills():
            if f.get('sessionId')!=s['id']:continue
            asset='XBT' if f['pair']=='XBTEUR' else 'ETH';qty=decimal(f['quantity']);cost=decimal(f['costEUR']);fee=decimal(f['feeEUR'])
            expected[asset]+=qty if f['side']=='buy' else -qty
            expected['EUR']+=-cost-fee if f['side']=='buy' else cost-fee
        for asset,value in expected.items():
            tolerance=Decimal('.01') if asset=='EUR' else Decimal('.00000001')
            if abs(decimal(balances[asset]['balance'])-value)>tolerance:
                raise ExchangeError('Kraken-saldot avviker från avstämda avslut. Insättning, uttag eller extern handel kräver en ny granskning.')

    def authorize(self,ticket,action):
        s=self.get(ticket.get('sessionId'))
        if ticket.get('fingerprint')!=fingerprint(ticket):raise ExchangeError('Orderinnehållet har ändrats.')
        if ticket.get('agentId') not in AGENTS:raise ExchangeError('Agenten är inte godkänd för sessionen.')
        if ticket.get('pair') not in PAIRS.values() or ticket['agentId']=='iota' and ticket['pair']!='XBTEUR':raise ExchangeError('Agentens handelspar är inte godkänt.')
        with self.store.lock:
            intent=self.store.db.execute('SELECT session,ticket FROM session_intents WHERE id=?',(ticket['intentId'],)).fetchone()
        if not intent or tuple(intent)!=(s['id'],ticket['id']):raise ExchangeError('Ordern saknar ett beständigt sessionsintent.')
        if action=='cancel':
            if s['status'] not in ACTIVE_SESSIONS:raise ExchangeError('Sessionen är avslutad.')
        else:
            if not self.status()['enabled'] or not self.e.enabled or self.e.monitor_only:raise ExchangeError('Automatisk handel är avstängd.')
            if decimal(ticket['volume'])*decimal(ticket['price'])>decimal(s['maxOrderEUR']):raise ExchangeError('Ordern överskrider den signerade ordergränsen.')
            analysis=self.store.state.get('marketAnalysis',{}).get(next(k for k,v in PAIRS.items() if v==ticket['pair']),{})
            if analysis.get('status')!='ready' or analysis.get('barId')!=ticket['signalBar'] or not 0<=(now()-datetime.fromisoformat(analysis['closedAt'])).total_seconds()<300:
                raise StaleDecision('Handelssignalen har löpt ut.')
            current=self.e.last_account
            if not current or current['openOrderCount']!=0:raise ExchangeError('Färskt konto utan andra öppna order krävs.')
            self.check_balances(s,current['balances'])
            if ticket['side']=='buy':
                if s['status']!='RUNNING' or now()>=datetime.fromisoformat(s['expiresAt']):raise ExchangeError('Sessionen tillåter inte nya köp.')
                used=sum((decimal(f['costEUR'])+decimal(f['feeEUR']) for f in self.fills() if f.get('sessionId')==s['id'] and f['side']=='buy'),Decimal(0))
                owned=sum((decimal(lot['costIncludingFeeEUR']) for lot in self.owned() if lot['agentId']==ticket['agentId']),Decimal(0))
                reserve=decimal(ticket['volume'])*decimal(ticket['price'])*Decimal('1.01')
                if used+reserve>decimal(s['newCashEUR']) or owned+reserve>decimal(s['budgets'][ticket['agentId']]):raise ExchangeError('Agent- eller sessionsbudgeten är förbrukad.')
                m=self.e.public.metadata;rate=decimal(m['eurSek'])
                equity=decimal(current['balances']['EUR']['balance'])+sum((decimal(current['balances'][asset]['balance'])*decimal(m['orderBooks'][symbol]['bids'][0][0])/rate for asset,symbol in [('XBT','BTC-SIM'),('ETH','ETH-SIM')]),Decimal(0))
                if max(decimal(s['initialEquityEUR'])-equity,decimal(s['peakEquityEUR'])-equity)+reserve*Decimal('.01')>=decimal(s['maxLossEUR']):raise ExchangeError('Den verkliga förlustgränsen tillåter inte ett nytt köp.')
            else:
                if s['status'] not in ('RUNNING','STOPPING'):raise ExchangeError('Sessionen tillåter inte försäljning.')
                if s['status']=='RUNNING' and now()>=datetime.fromisoformat(s['expiresAt']):raise ExchangeError('Sessionstiden har löpt ut; avslutsbedömning krävs.')
                if s['status']=='STOPPING' and (not s['allowEndSales'] or s['endPolicy']!='assess' or now()>min(datetime.fromisoformat(s['stopRequestedAt']),datetime.fromisoformat(s['expiresAt']))+timedelta(minutes=15)):
                    raise ExchangeError('Avslutstillståndet tillåter bara att behålla och annullera.')
                qty=sum((decimal(lot['quantity']) for lot in self.owned() if lot['agentId']==ticket['agentId'] and lot['pair']==ticket['pair']),Decimal(0))
                if decimal(ticket['volume'])>qty:raise ExchangeError('Agenten äger inte tillräcklig verifierad volym. Ingen blankning tillåts.')
            if not s.get('reconciledAt') or not 0<=(now()-datetime.fromisoformat(s['reconciledAt'])).total_seconds()<=45:raise ExchangeError('Färsk kontoavstämning krävs.')
        return dict(actor=s.get('actor','owner'),nonce='session:'+s['id']+':'+ticket['intentId']+':'+action)

    def pause(self,s,reason):
        with self.store.lock:
            current=self.get(s['id'])
            if current['status']=='RUNNING':current.update(status='PAUSED',reason=reason);self.save(current,'RISK_PAUSE')
        return self.get(s['id'])

    def shutdown(self):
        with self.store.lock:
            s=self.active()
            if s:
                s.update(status='RECOVERY',allowEndSales=False,reason='Servern stoppades. Ingen handel återupptas automatiskt; kontrollera Kraken och avsluta sessionen.')
                self.save(s,'SERVER_STOP')

    def order(self,s,aid,pair,side,bar,volume,price,intent):
        with self.store.lock,self.store.db:
            if self.store.db.execute('SELECT 1 FROM session_intents WHERE id=?',(intent,)).fetchone():return None
            self.store.db.execute('INSERT INTO session_intents VALUES(?,?,NULL,?)',(intent,s['id'],'PREPARING'))
        params=dict(pair=pair,side=side,volume=str(volume),price=str(price))
        risk=self.e.preflight(params)
        ticket=dict(id='LIVE-'+uuid.uuid4().hex,**params,clientId='J'+uuid.uuid4().hex[:17],createdAt=now().isoformat(),
            state='PREVIEW',risk=risk,stopGeneration=self.store.state.get('stopGeneration',0),sessionId=s['id'],agentId=aid,intentId=intent,signalBar=bar)
        ticket['fingerprint']=fingerprint(ticket)
        self.e.save(ticket,'SESSION_PREVIEW')
        with self.store.lock,self.store.db:self.store.db.execute('UPDATE session_intents SET ticket=?,status=? WHERE id=?',(ticket['id'],'PREVIEW',intent))
        # Validation is never an authorization to place an order.
        try:
            reply=self.e.private.call('AddOrder',self.e.parameters(ticket,True))
        except ExchangeUnknown as exc:
            ticket['state']='UNKNOWN' if exc.possible_order else 'VALIDATION_FAILED'
            self.e.save(ticket,'SESSION_VALIDATION_UNKNOWN');raise
        if reply.get('txid'):ticket['state']='UNKNOWN';self.e.save(ticket,'UNEXPECTED_VALIDATION_ORDER_ID');raise ExchangeUnknown('Oväntat order-ID under validering.',True)
        ticket.update(state='VALIDATED',validatedAt=now().isoformat());self.e.save(ticket,'SESSION_VALIDATED')
        try:self.e.mutate(ticket,'submit',None,'session-send:'+intent,session_authorizer=self.authorize)
        except StaleDecision:
            ticket.update(state='REJECTED',error='Stapeln löpte ut innan ordern skickades. Inget handelsanrop gjordes.')
            self.e.save(ticket,'DECISION_EXPIRED');raise
        if ticket['state'] in ('UNKNOWN','REJECTED'):
            self.pause(s,'Orderutfallet är okänt eller avvisat. Sessionen återstartas inte automatiskt.')
        return ticket

    def tick(self):
        if not self.worker_lock.acquire(blocking=False):return
        try:
            with self.e.lock:
                s=self.active()
                if not s:return
                if s['status']=='RUNNING' and not self.status()['enabled']:
                    s=self.pause(s,'Automatisk handel stängdes av. Ingen automatisk återstart.')
                if s['status']=='RUNNING' and now()>=datetime.fromisoformat(s['expiresAt']):
                    s=self.run('stop',dict(session=s['id']),'expiry:'+s['id'])
                self.e.inspect();s=self.get(s['id'])
                active=[t for t in self.e.tickets() if t['state'] in ACTIVE]
                if s['status'] in ('STOPPING','PAUSED','RECOVERY'):
                    for t in active:
                        if t.get('sessionId')==s['id'] and t.get('exchangeOrderId') and t['state'] not in ('CANCEL_REQUESTED','UNKNOWN'):
                            self.e.mutate(t,'cancel',None,'session-cancel:'+t['id'],session_authorizer=self.authorize)
                    if active:return
                    if s['status'] in ('PAUSED','RECOVERY'):return
                elif active:
                    if any(t['state']=='UNKNOWN' or t.get('sessionId')!=s['id'] for t in active):self.pause(s,'Okänd eller extern order. Nya köp pausade; manuell avstämning krävs.')
                    return
                if any(t.get('historyError') for t in self.e.tickets()):
                    self.pause(s,'Ofullständig avslutshistorik blockerar handel.');return
                balances,orders=self.e.account()
                if orders:self.pause(s,'Öppna order utanför den avstämda sessionskedjan.');return
                self.check_balances(s,balances)
                try:m,bids=self.quotes()
                except Exception:
                    if s['status']!='STOPPING':raise
                    s=self.get(s['id']);s.update(status='STOPPED_HOLDINGS' if self.owned() else 'STOPPED',
                        reason='Trading avslutad. Inga färska kurser för säljbedömning; innehav behålls.',
                        endDecisions=[dict(agentId=l['agentId'],pair=l['pair'],quantity=l['quantity'],decision='HOLD',reason='Färska kurser saknas.',at=now().isoformat()) for l in self.owned()])
                    self.save(s,'STOPPED_NO_QUOTES');return
                equity=decimal(balances['EUR']['balance'])+decimal(balances['XBT']['balance'])*bids['XBTEUR']+decimal(balances['ETH']['balance'])*bids['ETHEUR']
                with self.store.lock:
                    s=self.get(s['id']);s['reconciledAt']=now().isoformat();s['equityEUR']=str(equity)
                    s['peakEquityEUR']=str(max(equity,decimal(s['peakEquityEUR'])))
                    if s['status']=='RUNNING' and max(decimal(s['initialEquityEUR'])-equity,decimal(s['peakEquityEUR'])-equity)>=decimal(s['maxLossEUR']):
                        self.pause(s,'Sessionsförlust/drawdown nådde den signerade förlustgränsen.');return
                    self.save(s,'RECONCILED')
                owned=self.owned();decisions=[];candidates=[]
                for aid in AGENTS:
                    for symbol,pair in PAIRS.items():
                        if aid=='iota' and pair!='XBTEUR':continue
                        analysis=self.store.state.get('marketAnalysis',{}).get(symbol,{})
                        try:fresh=analysis.get('status')=='ready' and 0<=(now()-datetime.fromisoformat(analysis['closedAt'])).total_seconds()<300
                        except (KeyError,ValueError,TypeError):fresh=False
                        lots=[lot for lot in owned if lot['agentId']==aid and lot['pair']==pair]
                        qty=sum((decimal(l['quantity']) for l in lots),Decimal(0));basis=sum((decimal(l['costIncludingFeeEUR']) for l in lots),Decimal(0))
                        chef=self.store.state.get('chefSignals',{}).get(symbol,{})
                        report=self.store.state.get('agentReports',{}).get(aid+'|'+symbol,{})
                        loss_exit=qty>0 and fresh and qty*bids[pair]<basis*Decimal('.95')
                        sell=qty>0 and fresh and (chef.get('side')=='SELL' or loss_exit)
                        if s['status']=='STOPPING':
                            sell=sell and s['endPolicy']=='assess' and s['allowEndSales']
                            intent=s['id']+'|end|'+aid+'|'+pair
                            with self.store.lock:tried=self.store.db.execute('SELECT 1 FROM session_intents WHERE id=?',(intent,)).fetchone()
                            sell=sell and not tried
                            if not fresh:reason='Behåll: färska slutna staplar saknas; inget säljbeslut gissas.'
                            elif s['endPolicy']=='hold':reason='Behåll: du valde att behålla alla innehav vid avslut.'
                            elif not s['allowEndSales']:reason='Behåll: återställningsläge saknar mandat för nya försäljningar.'
                            elif tried:reason='Behåll återstående del: ett begränsat exitförsök har redan gjorts; inga automatiska omsändningar.'
                            elif loss_exit:reason='Sälj: positionsvärdet ligger minst 5 % under verifierad kostnadsbas.'
                            elif sell:reason='Sälj: minst två olika strategier ger färsk säljsignal utan motsatt signal.'
                            else:reason='Behåll: varken gemensam säljsignal eller 5 % positionsförlust. Ingen garanti att fortsatt innehav blir lönsamt.'
                            if qty:decisions.append(dict(agentId=aid,pair=pair,quantity=str(qty),decision='SELL_PENDING' if sell else 'HOLD',
                                reason=reason,at=now().isoformat()))
                            if sell:candidates.append((aid,symbol,pair,'sell',qty,intent))
                        elif s['status']=='RUNNING' and fresh:
                            side='sell' if sell and (loss_exit or report.get('side')=='SELL') else 'buy' if chef.get('side')=='BUY' and report.get('side')=='BUY' else None
                            if side:
                                intent=s['id']+'|'+aid+'|'+pair+'|'+str(analysis['barId'])+'|'+side
                                candidates.append((aid,symbol,pair,side,qty,intent))
                if s['status']=='STOPPING':
                    s['endDecisions']=decisions
                    if not candidates:
                        s.update(status='STOPPED_HOLDINGS' if owned else 'STOPPED',reason='Trading avslutad. Inga nya order tillåts. Kvarvarande innehav ligger kvar på Kraken.')
                        self.save(s,'STOPPED');return
                    self.save(s,'END_REVIEW')
                candidates.sort(key=lambda item:item[3]!='sell')
                for aid,symbol,pair,side,qty,intent in candidates:
                    with self.store.lock:
                        if self.store.db.execute('SELECT 1 FROM session_intents WHERE id=?',(intent,)).fetchone():continue
                    rule=m['pairRules'][symbol];step=decimal(rule['priceStep']);qstep=decimal(rule['quantityStep'])
                    rate=decimal(m['eurSek']);book=m['orderBooks'][symbol]
                    raw=decimal(book['asks' if side=='buy' else 'bids'][0][0])/rate
                    price=(raw/step).to_integral_value(rounding=ROUND_UP if side=='buy' else ROUND_DOWN)*step
                    if side=='buy':
                        used=sum((decimal(f['costEUR'])+decimal(f['feeEUR']) for f in self.fills() if f.get('sessionId')==s['id'] and f['side']=='buy'),Decimal(0))
                        basis=sum((decimal(l['costIncludingFeeEUR']) for l in owned if l['agentId']==aid),Decimal(0))
                        money=max(Decimal(0),min(decimal(s['newCashEUR'])-used,decimal(s['budgets'][aid])-basis,decimal(s['maxOrderEUR'])))
                        qty=money/(price*Decimal('1.01'))
                    qty=min(qty,decimal(s['maxOrderEUR'])/price)
                    qty=(qty/qstep).to_integral_value(rounding=ROUND_DOWN)*qstep
                    if qty<decimal(rule['minimumVolume']) or qty*price<decimal(rule['minimumCost']):
                        if s['status']=='STOPPING':
                            with self.store.lock,self.store.db:self.store.db.execute('INSERT OR IGNORE INTO session_intents VALUES(?,?,NULL,?)',(intent,s['id'],'BELOW_MINIMUM'))
                        continue
                    self.order(s,aid,pair,side,self.store.state['marketAnalysis'][symbol]['barId'],qty,price,intent)
                    return
        except StaleDecision:
            pass # New bar => new intent; never retry the expired intent.
        except (ExchangeError,ExchangeUnknown):
            s=self.active()
            if s:
                if s['status']=='STOPPING':s['reason']='Avslut väntar på verifierbar konto-/orderavstämning. Kontrollera Kraken; inga nya köp tillåts.';self.save(s,'END_WAITING')
                else:self.pause(s,'Avstämning eller orderkontroll misslyckades. Ingen automatisk omsändning. Granska Kraken innan återstart.')
        except Exception:
            s=self.active()
            if s:self.pause(s,'Internt sessionsfel. Handel pausad; granska journalen och avsluta sessionen.')
        finally:self.worker_lock.release()
