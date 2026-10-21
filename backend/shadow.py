"""Conservative, separate shadow limit orders; never imports a live broker.

Observed depth is not a promise of a real fill. Only 10% of visible size is made
available; slippage is charged adversely, orders expire after 60 seconds, and
partial fills remain owned. No fills on stale, crossed or incomplete data.
"""
from datetime import datetime,timedelta
from decimal import Decimal,ROUND_DOWN

D=Decimal
FEE=D('.004')
SLIP=D('.001')


def update(book,pair,levels,rule,side,bar,exit_plan,at,capital,risk_capital="100"):
    positions=book['positions'];pending=book.setdefault('pending',{})
    events=book.setdefault('orders',[])
    def event(kind,**values):
        events.append(dict(kind=kind,at=at.isoformat(),pair=pair,**values));del events[:-200]
    bid=D(str(levels['bids'][0][0]));ask=D(str(levels['asks'][0][0]))
    step=D(str(rule['quantityStep']));minimum=D(str(rule['minimumVolume']));costmin=D(str(rule['minimumCost']))
    if not 0<bid<=ask or ask/bid>D('1.01'):return
    order=pending.get(pair)
    if order:
        if at>=datetime.fromisoformat(order['expiresAt']):
            if order['side']=='sell' and D(order.get('filledQuantity','0'))>0:
                book['trades'].append(dict(pair=pair,boughtAt=order['boughtAt'],soldAt=at.isoformat(),netEUR=order['netEUR'],reason=order['reason']+' · delavslut, resten utgick'))
            event('EXPIRED',remaining=order['remaining']);del pending[pair];order=None
        elif side=='SELL' and order['side']=='buy':
            event('CANCELLED_SIGNAL_CHANGE');del pending[pair];order=None
    if order:
        remaining=D(order['remaining']);price=D(order['limit']);filled=D(0);value=D(0)
        for raw_price,raw_size,*_ in levels['asks' if order['side']=='buy' else 'bids']:
            observed=D(str(raw_price));size=D(str(raw_size))*D('.1')
            if (order['side']=='buy' and observed>price) or (order['side']=='sell' and observed<price):break
            take=(min(remaining,size)/step).to_integral_value(rounding=ROUND_DOWN)*step
            if take<=0:continue
            remaining-=take;filled+=take;value+=take*observed*(1+SLIP if order['side']=='buy' else 1-SLIP)
            if remaining<=0:break
        if filled:
            if order['side']=='buy':
                p=positions.setdefault(pair,dict(quantity='0',costEUR='0',entry=order['limit'],at=at.isoformat(),lossFraction=order['lossFraction']))
                p['quantity']=str(D(p['quantity'])+filled);p['costEUR']=str(D(p['costEUR'])+value*(1+FEE))
            else:
                p=positions[pair];qty=D(p['quantity']);basis=D(p['costEUR'])*filled/qty
                order['netEUR']=str(D(order.get('netEUR','0'))+value*(1-FEE)-basis)
                p['quantity']=str(qty-filled);p['costEUR']=str(D(p['costEUR'])-basis)
                if D(p['quantity'])<=0:del positions[pair]
            order['filledQuantity']=str(D(order.get('filledQuantity','0'))+filled)
            order['remaining']=str(remaining);event('PARTIAL' if remaining else 'FILLED',quantity=str(filled),side=order['side'])
        if remaining<=0:
            if order['side']=='sell':book['trades'].append(dict(pair=pair,boughtAt=order['boughtAt'],soldAt=at.isoformat(),netEUR=order['netEUR'],reason=order['reason']))
            del pending[pair]
        # Expired partial sell results are accounted immediately below at expiry.
        return
    position=positions.get(pair)
    risk_exit=position and bid<D(position['entry'])*(1-D(position['lossFraction']))
    if book['lastBars'].get(pair)==bar and not risk_exit:return
    book['lastBars'][pair]=bar
    action='sell' if position and (side=='SELL' or risk_exit) else 'buy' if not position and side=='BUY' else None
    if not action:return
    limit=ask if action=='buy' else bid
    if action=='buy':
        realized=sum((D(t['netEUR']) for t in book['trades']),D(0))+sum((D(p.get('netEUR','0')) for p in pending.values() if p['side']=='sell'),D(0))
        committed=sum((D(p['costEUR']) for p in positions.values()),D(0))
        reserved=sum((D(p['remaining'])*D(p['limit'])*(1+FEE)*(1+SLIP) for p in pending.values() if p['side']=='buy'),D(0))
        free=max(D(0),D(capital)+realized-committed-reserved)
        # Same 0.5% capital risk policy as live, with explicit cost assumptions.
        allowance=min(D('25'),free,D(risk_capital)*D('.005')/(D(exit_plan['lossFraction'])+FEE*2+SLIP*2))
        qty=(allowance/(limit*(1+FEE)*(1+SLIP))/step).to_integral_value(rounding=ROUND_DOWN)*step
    else:qty=D(position['quantity'])
    if qty<minimum or qty*limit<costmin:
        event('BELOW_MINIMUM');return
    order=dict(side=action,remaining=str(qty),limit=str(limit),createdAt=at.isoformat(),expiresAt=(at+timedelta(seconds=60)).isoformat(),
               lossFraction=exit_plan['lossFraction'] if action=='buy' else position['lossFraction'],reason='Riskgräns' if risk_exit else 'Strategisignal',netEUR='0')
    if action=='sell':order['boughtAt']=position['at']
    pending[pair]=order;event('OPEN',side=action,quantity=str(qty),limit=str(limit))
