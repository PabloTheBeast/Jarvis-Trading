"""Owner-approved Kraken limit tickets, separate from the paper ledger.

Durable SENDING is committed before the only AddOrder(validate=false) call.
Unknown outcomes are never resent, including after a crash. No agent invokes this
service. Only one nonterminal live ticket can be active at a time.
"""
import hashlib
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from .approval import verify
from .kraken_private import KrakenPrivate, ExchangeError, ExchangeUnknown
from .market import Kraken, PAIRS, canonical, MarketError
from .trade_history import history

TERMINAL={'CLOSED','CANCELLED','EXPIRED','REJECTED'}
ACTIVE={'VALIDATION_UNKNOWN','SENDING','UNKNOWN','ACKNOWLEDGED','OPEN','PARTIAL','CANCEL_REQUESTED'}
ALIASES={'ZEUR':'EUR','EUR':'EUR','XXBT':'XBT','XBT':'XBT','BTC':'XBT','XETH':'ETH','ETH':'ETH'}


def decimal(value,positive=False):
    if not isinstance(value,(str,int,float)) or isinstance(value,bool):
        raise ExchangeError('Belopp måste vara ett decimaltal.')
    try:
        if len(str(value))>64:
            raise ValueError()
        number=Decimal(str(value))
        if not number.is_finite() or abs(number.adjusted())>30 or positive and number<=0:
            raise ValueError()
    except (InvalidOperation,ValueError):
        raise ExchangeError('Ogiltigt decimaltal.') from None
    return number


def fingerprint(ticket):
    fields={key:ticket[key] for key in ('id','pair','side','volume','price','clientId','stopGeneration')}
    for key in ('sessionId','agentId','intentId','signalBar'):
        if key in ticket:fields[key]=ticket[key]
    return hashlib.sha256(json.dumps(fields,sort_keys=True,separators=(',',':')).encode()).hexdigest()


class LiveService:
    def __init__(self,store,public=None,private=None,enabled=None,maximum=None):
        self.store=store
        self.public=public or Kraken()
        self.lock=threading.RLock()
        self.enabled=(os.environ.get('JARVIS_KRAKEN_LIVE')=='1') if enabled is None else enabled
        self.maximum=decimal(maximum if maximum is not None else os.environ.get('JARVIS_KRAKEN_MAX_ORDER_EUR','25'),True)
        # Configuration cannot raise the initial pilot ceiling above 100 EUR/order.
        if self.maximum>100:
            raise ExchangeError('Pilotgränsen får vara högst 100 EUR per order.')
        self.private=private or KrakenPrivate(store.path,self.nonce)
        self.last_account=None
        self.last_check=None
        self.last_error=None
        self.monitor_only=False
        self.monitoring=False
        with store.lock:
            store.db.executescript('''
                CREATE TABLE IF NOT EXISTS live_meta(key TEXT PRIMARY KEY,value INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS live_tickets(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS live_requests(id TEXT PRIMARY KEY,request TEXT NOT NULL,ticket TEXT);
                CREATE TABLE IF NOT EXISTS live_approvals(nonce TEXT PRIMARY KEY,ticket TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS live_events(seq INTEGER PRIMARY KEY,at TEXT NOT NULL,ticket TEXT,event TEXT NOT NULL,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS live_fills(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
                CREATE TRIGGER IF NOT EXISTS live_fills_no_update BEFORE UPDATE ON live_fills
                BEGIN SELECT RAISE(ABORT,'Live fills are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS live_fills_no_delete BEFORE DELETE ON live_fills
                BEGIN SELECT RAISE(ABORT,'Live fills are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS live_events_no_update BEFORE UPDATE ON live_events
                BEGIN SELECT RAISE(ABORT,'Live journal is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS live_events_no_delete BEFORE DELETE ON live_events
                BEGIN SELECT RAISE(ABORT,'Live journal is append-only'); END;
            ''')
            rows=store.db.execute('SELECT payload FROM live_tickets').fetchall()
            for (payload,) in rows:
                ticket=json.loads(payload)
                if ticket['state'] in ('SENDING','CANCEL_REQUESTED'):
                    ticket['state']='UNKNOWN'
                    self.save(ticket,'RECOVERED_UNKNOWN')
        from .sessions import Sessions
        self.sessions=Sessions(self)

    def nonce(self):
        with self.store.lock,self.store.db:
            row=self.store.db.execute("SELECT value FROM live_meta WHERE key='nonce'").fetchone()
            value=max(time.time_ns()//1000,(row[0]+1) if row else 0)
            self.store.db.execute("INSERT OR REPLACE INTO live_meta VALUES('nonce',?)",(value,))
            return value

    def tickets(self):
        with self.store.lock:
            return [json.loads(row[0]) for row in self.store.db.execute('SELECT payload FROM live_tickets ORDER BY rowid DESC')]

    def get(self,ticket_id):
        with self.store.lock:
            row=self.store.db.execute('SELECT payload FROM live_tickets WHERE id=?',(ticket_id,)).fetchone()
        if not row:
            raise ExchangeError('Orderbiljetten finns inte.')
        ticket=json.loads(row[0])
        if ticket.get('fingerprint')!=fingerprint(ticket):
            raise ExchangeError('Orderbiljettens innehåll har ändrats; signaturen kan inte användas.')
        return ticket

    def save(self,ticket,event):
        ticket['updatedAt']=datetime.now(timezone.utc).isoformat()
        with self.store.lock,self.store.db:
            self.store.db.execute('INSERT OR REPLACE INTO live_tickets VALUES(?,?)',(ticket['id'],json.dumps(ticket)))
            self.store.db.execute('INSERT INTO live_events(at,ticket,event,payload) VALUES(?,?,?,?)',
                (ticket['updatedAt'],ticket['id'],event,json.dumps(ticket)))
        return ticket

    def status(self):
        with self.store.lock:
            fills=[json.loads(row[0]) for row in self.store.db.execute('SELECT payload FROM live_fills')]
        return dict(liveEnabled=self.enabled,maximumOrderEUR=str(self.maximum),mode='OWNER_APPROVED_SESSIONS_AND_MANUAL_LIMITS',
            monitorOnly=self.monitor_only,monitoringActive=self.monitoring,marketRefreshSeconds=15 if self.monitoring else None,accountRefreshSeconds=60 if self.monitoring else None,
            connected=self.last_account is not None,checkedAt=self.last_check,error=self.last_error,
            account=self.last_account,tickets=self.tickets()[:100],agentsUsePaperOnly=False,agentsRequireSessionApproval=True,tradeHistory=history(fills),
            instructions='Konfigurera lokalt med python3 -m backend.ops configure-kraken. Börja med en nyckel utan uttagsrättigheter.')

    def account(self):
        raw=self.private.call('BalanceEx')
        balances={asset:dict(balance='0',available='0') for asset in ('EUR','XBT','ETH')}
        for code,entry in raw.items():
            if not isinstance(entry,dict):
                raise ExchangeError('Ogiltigt saldosvar från Kraken.')
            value=decimal(entry.get('balance'))
            hold=decimal(entry.get('hold_trade','0'))
            if decimal(entry.get('credit','0'))!=0 or decimal(entry.get('credit_used','0'))!=0:
                raise ExchangeError('Marginal/kreditkonton stöds inte.')
            if value<0 or hold<0 or hold>value:
                raise ExchangeError('Saldot kan inte stämmas av.')
            asset=ALIASES.get(code)
            if not asset:
                if value!=0:
                    raise ExchangeError('Pilotläget kräver ett konto med enbart EUR, BTC och ETH; övriga innehav kan inte riskvärderas.')
                continue
            if decimal(balances[asset]['balance'])!=0:
                raise ExchangeError('Flera saldokoder för samma tillgång måste stämmas av.')
            balances[asset]=dict(balance=str(value),available=str(value-hold))
        open_orders=self.private.call('OpenOrders',{'trades':'true'}).get('open')
        if not isinstance(open_orders,dict):
            raise ExchangeError('Öppna order kan inte stämmas av.')
        self.last_account=dict(balances=balances,openOrderCount=len(open_orders),currency='EUR')
        self.last_check=datetime.now(timezone.utc).isoformat()
        self.last_error=None
        return balances,open_orders

    def inspect(self):
        with self.lock:
            try:
                self.account()
            except (ExchangeError,ExchangeUnknown) as exc:
                self.last_account=None
                self.last_error=str(exc)
                raise
            for ticket in self.tickets():
                if ticket['state'] in ACTIVE or ticket.get('exchangeOrderId') and not ticket.get('fillHistoryComplete'):
                    try:
                        self.reconcile(ticket)
                    except (ExchangeError,ExchangeUnknown):
                        self.last_error='Saldo hämtat, men en orderhistorik kunde inte uppdateras. Kontrollera orderns avstämning.'
            return self.status()

    def collect_fills(self,ticket):
        ids=ticket.get('tradeIds',[])
        if not isinstance(ids,list) or any(not isinstance(value,str) for value in ids) or len(ids)!=len(set(ids)):
            raise ExchangeError('Börsens avsluts-ID kan inte verifieras.')
        fills=[]
        for offset in range(0,len(ids),20):
            response=self.private.call('QueryTrades',dict(txid=','.join(ids[offset:offset+20])))
            for ident in ids[offset:offset+20]:
                raw=response.get(ident)
                if not isinstance(raw,dict):raise ExchangeError('Ett avslut saknas hos Kraken.')
                if (raw.get('ordertxid')!=ticket['exchangeOrderId'] or raw.get('type')!=ticket['side']
                    or canonical(str(raw.get('pair','')).replace('/',''))!=ticket['pair']):
                    raise ExchangeError('Avslutet hör inte till den verifierade ordern.')
                volume=decimal(raw.get('vol'),True);price=decimal(raw.get('price'),True)
                cost=decimal(raw.get('cost'),True);fee=decimal(raw.get('fee'))
                stamp=decimal(raw.get('time'),True)
                if cost<0 or fee<0 or stamp>Decimal(str(time.time()+5)):
                    raise ExchangeError('Ogiltigt belopp eller framtida avslutstid.')
                when=datetime.fromtimestamp(float(stamp),timezone.utc).isoformat()
                fills.append(dict(tradeId=ident,ticketId=ticket['id'],orderId=ticket['exchangeOrderId'],
                    pair=ticket['pair'],side=ticket['side'],quantity=str(volume),priceEUR=str(price),
                    costEUR=str(cost),feeEUR=str(fee),executedAt=when,agentId=ticket.get('agentId'),
                    **({'sessionId':ticket['sessionId']} if ticket.get('sessionId') else {}),actor=ticket.get('agentId') or 'Manuell'))
        if sum((decimal(f['quantity']) for f in fills),Decimal(0))!=decimal(ticket.get('filledVolume','0')):
            raise ExchangeError('Avslutshistoriken är ofullständig; inga tidpunkter gissas.')
        with self.store.lock,self.store.db:
            for fill in fills:
                old=self.store.db.execute('SELECT payload FROM live_fills WHERE id=?',(fill['tradeId'],)).fetchone()
                if old and json.loads(old[0])!=fill:raise ExchangeError('Ett tidigare avslut har ändrats; manuell avstämning krävs.')
                self.store.db.execute('INSERT OR IGNORE INTO live_fills VALUES(?,?)',(fill['tradeId'],json.dumps(fill)))
        ticket['fillHistoryComplete']=ticket['state'] in TERMINAL
        ticket.pop('historyError',None)

    def preflight(self,parameters):
        try:
            if isinstance(self.public,Kraken):
                self.public.rules(force=True)
            self.public.fetch()
        except MarketError:
            raise ExchangeError('Publika börsdata kan inte verifieras. Ingen order har skickats.') from None
        data=self.public.metadata
        if not data.get('pairRules') or not data.get('orderBooks'):
            raise ExchangeError('Börsregler och aktuellt orderdjup krävs.')
        age=(datetime.now(timezone.utc)-datetime.fromisoformat(data['fetchedAt'])).total_seconds()
        if not 0<=age<=15:
            raise ExchangeError('Orderdjupet är för gammalt.')
        pair=parameters.get('pair')
        symbol=next((symbol for symbol,value in PAIRS.items() if value==pair),None)
        if not symbol or parameters.get('side') not in ('buy','sell'):
            raise ExchangeError('Endast BTC/EUR eller ETH/EUR, köp eller sälj, stöds.')
        rule=data['pairRules'][symbol]
        if rule['status']!='online':
            raise ExchangeError('Handelsparet är inte online.')
        if any(not isinstance(parameters.get(field),str) or not re.fullmatch(r'\d+(?:\.\d{1,18})?',parameters[field]) for field in ('volume','price')):
            raise ExchangeError('Använd vanliga decimalsträngar för antal och pris, utan exponent.')
        volume,price=decimal(parameters.get('volume'),True),decimal(parameters.get('price'),True)
        if volume%decimal(rule['quantityStep'],True)!=0 or price%decimal(rule['priceStep'],True)!=0:
            raise ExchangeError('Antal/pris följer inte Krakens decimalsteg.')
        notional=volume*price
        if volume<decimal(rule['minimumVolume']) or notional<decimal(rule['minimumCost']):
            raise ExchangeError('Ordern understiger Krakens minsta antal eller ordervärde.')
        if notional>self.maximum:
            raise ExchangeError('Ordern överskrider den lokala pilotgränsen '+str(self.maximum)+' EUR.')
        rate=decimal(data['eurSek'],True)
        bids={sym:decimal(book['bids'][0][0],True)/rate for sym,book in data['orderBooks'].items()}
        bid=bids[symbol]
        ask=decimal(data['orderBooks'][symbol]['asks'][0][0],True)/rate
        if ask/bid-1>Decimal('.01') or abs(price-(ask+bid)/2)/((ask+bid)/2)>Decimal('.02'):
            raise ExchangeError('Spread över 1 % eller limitpris mer än 2 % från mittkursen.')
        try:
            balances,open_orders=self.account()
        except ExchangeUnknown:
            raise ExchangeError('Kontot kan inte verifieras just nu. Ingen order har skickats.') from None
        # Private HTTP calls must not turn a once-fresh quote into an old preflight.
        if (datetime.now(timezone.utc)-datetime.fromisoformat(data['fetchedAt'])).total_seconds()>30:
            raise ExchangeError('Börskontrollen tog för lång tid. Förhandsgranska igen.')
        reserved={'BTC-SIM':Decimal(0),'ETH-SIM':Decimal(0)}
        for order in open_orders.values():
            desc=order.get('descr',{})
            target=next((sym for sym,p in PAIRS.items() if canonical(str(desc.get('pair','')).replace('/',''))==p),None)
            if target is None or desc.get('ordertype')!='limit':
                raise ExchangeError('Kontot har order som pilotens riskmodell inte kan värdera.')
            if desc.get('type') not in ('buy','sell') or desc.get('leverage','none') not in ('none','0','1','1:1'):
                raise ExchangeError('Okänd orderriktning eller marginalorder på kontot.')
            total,executed=decimal(order.get('vol'),True),decimal(order.get('vol_exec'))
            if not 0<=executed<=total:
                raise ExchangeError('Öppen order har ogiltig avslutsmängd.')
            if desc.get('type')=='buy':
                reserved[target]+=(total-executed)*decimal(desc.get('price'),True)
        equity=decimal(balances['EUR']['balance'])+decimal(balances['XBT']['balance'])*bids['BTC-SIM']+decimal(balances['ETH']['balance'])*bids['ETH-SIM']
        invested=decimal(balances['XBT']['balance'])*bids['BTC-SIM']+decimal(balances['ETH']['balance'])*bids['ETH-SIM']
        base=rule['base']
        if parameters['side']=='buy':
            # 1% fee reserve; no margin, no balance borrowed from the paper ledger.
            if notional*Decimal('1.01')>decimal(balances['EUR']['available']):
                raise ExchangeError('Otillräckligt tillgängligt EUR-saldo inklusive avgiftsreserv.')
            after_equity=equity-notional*Decimal('.01')
            instrument=decimal(balances[base]['balance'])*bid
            if invested+sum(reserved.values())+notional>after_equity*Decimal('.4'):
                raise ExchangeError('Samlad kryptoexponering skulle överstiga 40 %.')
            if instrument+reserved[symbol]+notional>after_equity*Decimal('.25'):
                raise ExchangeError('Instrumentexponering skulle överstiga 25 %.')
        elif volume>decimal(balances[base]['available']):
            raise ExchangeError('Otillräckligt tillgängligt innehav. Blankning tillåts inte.')
        return dict(notionalEUR=str(notional),feeReserveEUR=str(notional*Decimal('.01')),
                    equityEUR=str(equity),checkedAt=datetime.now(timezone.utc).isoformat(),pairRules=rule)

    def run(self,action,data,key,approval=None):
        if action in {'preview','validate','submit'} and self.sessions.active():
            raise ExchangeError('En agentsession pågår. Avsluta den innan manuella order förbereds.')
        if self.monitor_only and action in {'preview','validate','submit'}:
            raise ExchangeError('Bevakningsläge: läser bara kurser och konto. Ingen ny order kan förberedas eller skickas.')
        if not key or len(key)>100:
            raise ExchangeError('Idempotency-Key krävs.')
        if action not in {'preview','validate','submit','reconcile','cancel','check'}:
            raise ExchangeError('Okänd börsåtgärd.')
        expected={'pair','side','volume','price'} if action=='preview' else set() if action=='check' else {'ticket'}
        if set(data)!=expected:
            raise ExchangeError('Unexpected exchange request fields')
        request=json.dumps(dict(action=action,data=data),sort_keys=True,allow_nan=False)
        with self.lock:
            with self.store.lock:
                previous=self.store.db.execute('SELECT request,ticket FROM live_requests WHERE id=?',(key,)).fetchone()
            if previous:
                if previous[0]!=request:
                    raise ExchangeError('Kommandonyckeln har redan använts för en annan åtgärd.')
                return self.get(previous[1]) if previous[1] else self.status()
            if action=='check':
                result=self.inspect()
            elif action=='preview':
                if any(t['state'] in ACTIVE for t in self.tickets()):
                    raise ExchangeError('En tidigare liveorder måste stämmas av eller avslutas först.')
                params={name:data.get(name) for name in ('pair','side','volume','price')}
                # Require decimal strings, so browser rounding never changes signed values.
                if not isinstance(params['volume'],str) or not isinstance(params['price'],str):
                    raise ExchangeError('Antal och limitpris ska skickas som decimalsträngar.')
                risk=self.preflight(params)
                ident='LIVE-'+uuid.uuid4().hex
                result=dict(id=ident,**params,clientId='J'+uuid.uuid4().hex[:17],
                    createdAt=datetime.now(timezone.utc).isoformat(),state='PREVIEW',risk=risk,
                    stopGeneration=self.store.snapshot()['state'].get('stopGeneration',0))
                result['fingerprint']=fingerprint(result)
                self.save(result,'PREVIEW')
            else:
                result=self.get(data.get('ticket'))
                if result.get('sessionId') and action in ('validate','submit'):
                    raise ExchangeError('Sessionsorder hanteras bara av den godkända sessionen. Skapa en ny manuell biljett för manuell handel.')
                if action=='reconcile':
                    result=self.reconcile(result)
                elif action=='validate':
                    if result['state'] not in {'PREVIEW','VALIDATED','VALIDATION_FAILED'}:
                        raise ExchangeError('Ordern är redan skickad eller avslutad.')
                    self.fresh(result)
                    result['risk']=self.preflight(result)
                    try:
                        response=self.private.call('AddOrder',self.parameters(result,True))
                        if response.get('txid'):
                            result['state']='UNKNOWN'
                            self.save(result,'UNEXPECTED_VALIDATION_ORDER_ID')
                            raise ExchangeUnknown('Kraken returnerade order-ID under validering; kontrollera kontot innan fler åtgärder.')
                    except ExchangeUnknown as exc:
                        # A correctly implemented validate=true does not place an order,
                        # but an unexpected result remains visible and never enables submit.
                        result['state']='UNKNOWN' if result['state']=='UNKNOWN' or getattr(exc,'possible_order',False) else 'VALIDATION_FAILED'
                        self.save(result,result['state'])
                        raise
                    result.update(state='VALIDATED',validatedAt=datetime.now(timezone.utc).isoformat())
                    self.save(result,'VALIDATED')
                else:
                    result=self.mutate(result,action,approval,key)
            with self.store.lock,self.store.db:
                ticket=result.get('id') if isinstance(result,dict) else None
                self.store.db.execute('INSERT OR IGNORE INTO live_requests VALUES(?,?,?)',(key,request,ticket))
            return result

    def fresh(self,ticket):
        age=(datetime.now(timezone.utc)-datetime.fromisoformat(ticket['createdAt'])).total_seconds()
        if not 0<=age<=300:
            raise ExchangeError('Orderförhandsgranskningen har löpt ut. Skapa en ny biljett.')

    def parameters(self,ticket,validate):
        return dict(pair=ticket['pair'],type=ticket['side'],ordertype='limit',
                    volume=ticket['volume'],price=ticket['price'],cl_ord_id=ticket['clientId'],
                    timeinforce='GTD',expiretm='+60',oflags='fciq',validate='true' if validate else 'false')

    def mutate(self,ticket,action,approval,key,session_authorizer=None):
        signed_action='live-order' if action=='submit' else 'live-cancel'
        subject=ticket['fingerprint'] if action=='submit' else ticket['fingerprint']+':cancel'
        signed=session_authorizer(ticket,action) if session_authorizer else verify(self.store.operator_key,approval or '',signed_action,subject=subject)
        if action=='submit':
            if not self.enabled:
                raise ExchangeError('Liveorder är avstängda. Ägaren måste starta servern med JARVIS_KRAKEN_LIVE=1.')
            if ticket['state']!='VALIDATED':
                raise ExchangeError('Ordern måste först valideras av Kraken.')
            if any(t['id']!=ticket['id'] and t['state'] in ACTIVE for t in self.tickets()):
                raise ExchangeError('En annan liveorder har ännu inte stämts av.')
            self.fresh(ticket)
            if (datetime.now(timezone.utc)-datetime.fromisoformat(ticket['validatedAt'])).total_seconds()>120:
                raise ExchangeError('Kraken-valideringen är för gammal.')
            ticket['risk']=self.preflight(ticket)
            with self.store.lock:
                if not session_authorizer and ticket['side']=='buy' and (self.store.state['halt'] or self.store.state['globalPause']):
                    raise ExchangeError('CHEF har en aktiv riskpaus; ingen ny liveorder tillåts.')
            method,parameters='AddOrder',self.parameters(ticket,False)
        else:
            if ticket['state'] not in ACTIVE or not ticket.get('exchangeOrderId'):
                raise ExchangeError('Ett känt aktivt Kraken-order-ID krävs för annullering.')
            method,parameters='CancelOrder',dict(txid=ticket['exchangeOrderId'])
        if not session_authorizer:verify(self.store.operator_key,approval or '',signed_action,subject=subject)
        ticket.update(state='SENDING' if action=='submit' else 'CANCEL_REQUESTED',
                      actor=signed['actor'],approvalId=signed['nonce'])
        # Mark the request before network I/O, atomically with consumed approval.
        # If the process dies now, this ticket is UNKNOWN on restart, never resent.
        request=json.dumps(dict(action=action,data=dict(ticket=ticket['id'])),sort_keys=True,allow_nan=False)
        with self.store.lock,self.store.db:
            if session_authorizer:session_authorizer(ticket,action)
            if action=='submit':
                if self.store.state.get('stopGeneration',0)!=ticket['stopGeneration']:
                    raise ExchangeError('En paus/omstart har ogiltigförklarat biljetten. Skapa en ny förhandsgranskning.')
                if not session_authorizer and ticket['side']=='buy' and (self.store.state['halt'] or self.store.state['globalPause']):
                    raise ExchangeError('CHEF har aktiverat riskpaus under orderkontrollen.')
            try:
                self.store.db.execute('INSERT INTO live_approvals VALUES(?,?)',(signed['nonce'],ticket['id']))
                self.store.db.execute('INSERT INTO live_requests VALUES(?,?,?)',(key,request,ticket['id']))
            except Exception:
                raise ExchangeError('Godkännandet eller kommandot har redan förbrukats.') from None
            self.store.db.execute('INSERT OR REPLACE INTO live_tickets VALUES(?,?)',(ticket['id'],json.dumps(ticket)))
            self.store.db.execute('INSERT INTO live_events(at,ticket,event,payload) VALUES(?,?,?,?)',
                (datetime.now(timezone.utc).isoformat(),ticket['id'],ticket['state'],json.dumps(ticket)))
        try:
            response=self.private.call(method,parameters)
        except ExchangeError as exc:
            ticket.update(state='REJECTED' if action=='submit' else 'UNKNOWN',error=str(exc))
        except ExchangeUnknown as exc:
            ticket.update(state='UNKNOWN',error=str(exc))
        else:
            if action=='submit':
                ids=response.get('txid')
                if not isinstance(ids,list) or len(ids)!=1 or not isinstance(ids[0],str) or not ids[0]:
                    ticket.update(state='UNKNOWN',error='Kraken-svaret saknar ett entydigt order-ID.')
                else:
                    ticket.update(state='ACKNOWLEDGED',exchangeOrderId=ids[0])
            else:
                ticket['state']='CANCEL_REQUESTED'
        return self.save(ticket,ticket['state'])

    def reconcile(self,ticket):
        if ticket['state'] not in ACTIVE and not ticket.get('exchangeOrderId'):
            return ticket
        match=None
        if ticket.get('exchangeOrderId'):
            result=self.private.call('QueryOrders',dict(txid=ticket['exchangeOrderId'],trades='true'))
            match=result.get(ticket['exchangeOrderId'])
        else:
            opened=self.private.call('OpenOrders',dict(trades='true')).get('open',{})
            found=[(ident,value) for ident,value in opened.items() if value.get('cl_ord_id')==ticket['clientId']]
            # ClosedOrders is paginated. A miss is deliberately UNKNOWN, never
            # proof that an order was not placed and never permission to resend.
            if not found:
                for offset in range(0,500,50):
                    closed=self.private.call('ClosedOrders',dict(start=str(int(datetime.fromisoformat(ticket['createdAt']).timestamp())-60),ofs=str(offset),trades='true'))
                    rows=closed.get('closed',{})
                    found.extend((ident,value) for ident,value in rows.items() if value.get('cl_ord_id')==ticket['clientId'])
                    if found or offset+len(rows)>=int(closed.get('count',len(rows))):
                        break
            if len(found)==1:
                ticket['exchangeOrderId'],match=found[0]
        if not isinstance(match,dict):
            ticket.update(state='UNKNOWN',error='Ordern kunde inte entydigt återfinnas. Skicka inte om den; kontrollera Kraken.')
        else:
            description=match.get('descr',{})
            if (not isinstance(description,dict)
                or canonical(str(description.get('pair','')).replace('/',''))!=ticket['pair']
                or description.get('type')!=ticket['side']
                or description.get('ordertype')!='limit'
                or match.get('cl_ord_id')!=ticket['clientId']
                or decimal(match.get('vol','0'))!=decimal(ticket['volume'])
                or decimal(description.get('price','0'))!=decimal(ticket['price'])):
                ticket.update(state='UNKNOWN',error='Börsens orderuppgifter matchar inte den signerade biljetten. Kontrollera Kraken.')
                return self.save(ticket,'RECONCILIATION_MISMATCH')
            state=match.get('status')
            mapped={'pending':'OPEN','open':'OPEN','closed':'CLOSED','canceled':'CANCELLED','expired':'EXPIRED'}
            ticket['state']=mapped.get(state,'UNKNOWN')
            executed=decimal(match.get('vol_exec','0'))
            if not 0<=executed<=decimal(ticket['volume']):
                raise ExchangeError('Börsens avslutsmängd kan inte stämmas av.')
            ticket['filledVolume']=str(executed)
            ticket['costEUR']=str(decimal(match.get('cost','0')))
            ticket['feeEUR']=str(decimal(match.get('fee','0')))
            ticket['tradeIds']=match.get('trades',[])
            if ticket['state']=='OPEN' and executed>0:
                ticket['state']='PARTIAL'
            ticket.pop('error',None)
            try:
                self.collect_fills(ticket)
            except (ExchangeError,ExchangeUnknown,ValueError,OverflowError):
                ticket.update(fillHistoryComplete=False,historyError='Avslutsdetaljer kunde inte verifieras. Tidigare verifierade affärer bevaras; försök stämma av igen.')
        return self.save(ticket,'RECONCILED')
