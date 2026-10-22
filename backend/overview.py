"""Read-only live dashboard projection. No network calls or execution authority."""
import copy
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from .live import ACTIVE
from .trade_history import history

D = Decimal
PAIRS = {'BTC-SIM': ('BTC', 'XBT', 'XBTEUR'), 'ETH-SIM': ('ETH', 'ETH', 'ETHEUR')}


def age(stamp, now):
    try:
        value = (now-datetime.fromisoformat(stamp)).total_seconds()
        return round(value, 1) if value >= 0 else None
    except (TypeError, ValueError):
        return None


def snapshot(runtime, now=None):
    now = now or datetime.now(timezone.utc)
    e = runtime.exchange
    # Reference snapshots are replaced atomically by the workers. Never wait for
    # the exchange network lock to paint the dashboard or access stop controls.
    account = copy.deepcopy(e.last_account)
    checked = (account or {}).get('checkedAt', e.last_check)
    error = e.last_error
    metadata = copy.deepcopy(runtime.last_metadata)
    state = runtime.store.snapshot()['state']
    fills = e.sessions.fills()
    book = history(fills)
    tickets = e.tickets()
    trading = e.sessions.status()
    supervision = e.governance.status()
    account_age = age(checked, now)
    market_age = age(metadata.get('fetchedAt'), now)
    account_fresh = bool(account is not None and account_age is not None and account_age <= 90)
    market_fresh = bool(market_age is not None and market_age <= 30)
    balances = (account or {}).get('balances', {})
    prices = []
    holdings = []
    market_value = D(0)
    complete = bool(account is not None)
    for symbol, (name, asset, pair) in PAIRS.items():
        bid = ask = None
        try:
            rate = D(str(metadata['eurSek']))
            raw = metadata['orderBooks'][symbol]
            bid = D(str(raw['bids'][0][0]))/rate
            ask = D(str(raw['asks'][0][0]))/rate
            if not rate.is_finite() or not bid.is_finite() or not ask.is_finite() or not 0 < bid <= ask:
                raise ValueError('Invalid quote')
        except (KeyError, ValueError, TypeError, ArithmeticError):
            bid = ask = None
        analysis = state.get('marketAnalysis', {}).get(symbol, {})
        stream = e.stream.status()
        streamed = stream.get('quotes', {}).get(name+'/EUR') if stream['healthy'] else None
        signal=copy.deepcopy(state.get('chefSignals', {}).get(symbol))
        signal_age=age((signal or {}).get('closedAt'),now)
        signal_fresh=bool(signal and signal_age is not None and signal_age<300 and market_fresh)
        prices.append(dict(pair=name+'/EUR', bidEUR=str(bid) if bid else None, askEUR=str(ask) if ask else None,
            spreadPercent=str((ask/bid-1)*100) if bid else None, checkedAt=metadata.get('fetchedAt'),
            fresh=market_fresh and bid is not None, trend=analysis.get('trend'), analysisStatus=analysis.get('status', 'missing'),
            signal=signal, signalFresh=signal_fresh, stream=streamed))
        if not account:
            continue
        qty = D(balances[asset]['balance'])
        available = D(balances[asset]['available'])
        lots = [l for l in book['open'] if l['pair'] == pair]
        known_qty = sum((D(l['quantity']) for l in lots), D(0))
        known_cost = sum((D(l['costIncludingFeeEUR']) for l in lots), D(0))
        value = qty*bid if bid else D(0) if qty == 0 else None
        complete = complete and value is not None
        market_value += value or D(0)
        known = abs(known_qty-qty) <= D('.00000001')
        holdings.append(dict(asset=name, pair=name+'/EUR', quantity=str(qty), available=str(available),
            valueEUR=str(value) if value is not None else None,
            costEUR=str(known_cost) if known else None,
            unrealizedEUR=str(value-known_cost) if known and value is not None else None,
            basisKnown=known, agents=sorted({l['agentId'] or 'Manuell' for l in lots})))
    reserve = D(supervision['savings']['reservedEUR'])
    cash = D(balances['EUR']['balance']) if account else None
    available_cash = D(balances['EUR']['available']) if account else None
    total = cash+market_value if complete and cash is not None else None
    today = now.astimezone(ZoneInfo('Europe/Stockholm')).date()
    daily = [r for r in book['closed'] if datetime.fromisoformat(r['soldAt']).astimezone(ZoneInfo('Europe/Stockholm')).date() == today]
    warnings = []
    if not account:
        warnings.append(error or 'Kraken-kontot har inte kunnat läsas. Belopp är okända, inte noll.')
    elif not account_fresh:
        warnings.append('Kontouppgifterna är äldre än 90 sekunder eller har ogiltig tidsstämpel.')
    if not market_fresh or any(not p['fresh'] for p in prices):
        warnings.append('Verifierade REST-kurser saknas eller är äldre än 30 sekunder.')
    if error and account:
        warnings.append(error)
    if any(t['state'] in ('UNKNOWN', 'VALIDATION_UNKNOWN', 'SENDING') or t.get('historyError') for t in tickets):
        warnings.append('En order eller avslutshistorik kräver avstämning. Kontrollera Kraken innan nya beslut.')
    if trading['session'] and trading['session']['status'] == 'STOPPED_HOLDINGS':
        warnings.append('Innehav finns kvar efter avslutad session utan automatisk orderhantering.')
    if book['unmatchedSales']:
        warnings.append('Resultatet är ofullständigt: försäljningar saknar verifierad inköpshistorik.')
    # Agent metrics are tied to the same overview market snapshot, not an older
    # independent preflight connector's valuation.
    open_ids={o['id'] for o in (account or {}).get('openOrders',[])}
    owners={t.get('exchangeOrderId'):t.get('agentId') for t in tickets if t.get('exchangeOrderId')}
    agents = []
    for agent in supervision['agents']:
        aid = agent['agentId']
        reports = [dict(r,fresh=bool(r.get('status')=='reading' and age(r.get('closedAt'),now) is not None and age(r.get('closedAt'),now)<300)) for r in state.get('agentReports', {}).values() if r['agentId'] == aid]
        session = trading['session']
        agents.append(dict(id=aid, status=agent['status'], reason=agent['reason'], metrics=agent['metrics'],
            budgetEUR=session['budgets'].get(aid) if session else None,
            budgetActive=bool(session and session['status'] == 'RUNNING'), reports=reports))
    return dict(serverTime=now.isoformat(), source='Kraken', currency='EUR', paper=False,
        freshness=dict(account=dict(checkedAt=checked, ageSeconds=account_age, fresh=account_fresh, error=error),
                       market=dict(checkedAt=metadata.get('fetchedAt'), ageSeconds=market_age, fresh=market_fresh and all(p['fresh'] for p in prices))),
        refresh=dict(browserSeconds=2, marketSeconds=15, accountSeconds=60),
        mode='MONITOR_ONLY' if e.monitor_only else 'LIVE_PERMITTED' if e.enabled else 'LIVE_DISABLED',
        totals=dict(accountValueEUR=str(total) if total is not None else None, cashEUR=str(cash) if cash is not None else None,
            availableEUR=str(available_cash) if available_cash is not None else None, reservedEUR=str(reserve),
            spendableEUR=str(max(D(0), available_cash-reserve)) if available_cash is not None else None,
            investedEUR=str(market_value) if complete else None,
            exposurePercent=str(market_value/total*100) if total is not None and total > 0 else '0' if total == 0 else None,
            realizedNetEUR=str(sum((D(r['netEUR']) for r in book['closed']), D(0))),
            todayRealizedNetEUR=str(sum((D(r['netEUR']) for r in daily), D(0))),
            resultComplete=not bool(book['unmatchedSales']) and not any(t.get('historyError') for t in tickets), fills=len(fills)),
        prices=prices, holdings=holdings, agents=agents, trading=trading,
        openOrders=[dict(o,agentId=owners.get(o['id'])) for o in (account or {}).get('openOrders', [])], openOrderCount=(account or {}).get('openOrderCount'),
        pendingTickets=[{k:t.get(k) for k in ('id','pair','side','volume','price','state','agentId','updatedAt','exchangeOrderId','historyError')} for t in tickets if t.get('historyError') or t['state'] in ACTIVE and (t.get('exchangeOrderId') not in open_ids or t['state'] in ('UNKNOWN','VALIDATION_UNKNOWN','SENDING'))],
        recentFills=sorted(fills,key=lambda f:(f['executedAt'],f['tradeId']),reverse=True)[:12],
        notices=[n for n in state.get('notifications',[]) if n.get('kind')=='CRYPTO_SIGNAL'][:5],warnings=warnings,
        stream=e.stream.status(), privateStream=e.execution_stream.status())
