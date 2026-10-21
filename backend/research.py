"""Deterministic strategy evaluation on imported five-minute OHLC.

No generated Python, eval, dynamic imports or live account access. Orders use the
next bar's open, never the already-known close that generated the signal.
"""
import hashlib
import json
from datetime import datetime,timezone
from decimal import Decimal,ROUND_DOWN

from .analysis import analyse

STRATEGIES=('Trend','Momentum','Mean reversion')


def evaluate(rows,strategy,capital='100',fee='.004',slippage='.001',quantity_step='.00000001',minimum_cost='5'):
    if strategy not in STRATEGIES:raise ValueError('Unknown built-in strategy')
    if len(rows)<300:raise ValueError('At least 300 five-minute bars required')
    if len(rows)>100000:raise ValueError('Import limited to 100000 bars')
    # Validate the complete import, then require continuity and closed history.
    final=datetime.fromtimestamp(int(rows[-1][0])+300,timezone.utc)
    if final>datetime.now(timezone.utc):raise ValueError('History contains unfinished or future bars')
    analyse(rows,final)
    if any(int(b[0])-int(a[0])!=300 for a,b in zip(rows,rows[1:])):raise ValueError('History must be contiguous')
    initial=Decimal(capital);fee=Decimal(fee);slip=Decimal(slippage);step=Decimal(quantity_step);minimum=Decimal(minimum_cost)
    if not initial.is_finite() or initial<=0 or not 0<=fee<Decimal('.1') or not 0<=slip<Decimal('.1') or step<=0 or minimum<0:raise ValueError('Invalid execution assumptions')
    split=int(len(rows)*.7)
    def run(start,end):
        cash=initial;qty=Decimal(0);basis=Decimal(0);peak=initial;dd=Decimal(0);trades=[]
        for i in range(start,end):
            closed=datetime.fromtimestamp(int(rows[i][0]),timezone.utc)
            analysis=analyse(rows[max(0,i-120):i],closed)
            side=analysis.get('signals',{}).get(strategy)
            opening=Decimal(str(rows[i][1]));mark=Decimal(str(rows[i][4]))
            if side=='BUY' and not qty:
                price=opening*(1+slip);size=(cash/(price*(1+fee))/step).to_integral_value(rounding=ROUND_DOWN)*step
                if size*price>=minimum:
                    basis=size*price*(1+fee);qty=size;cash-=basis
            elif side=='SELL' and qty and qty*opening>=minimum:
                revenue=qty*opening*(1-slip)*(1-fee);cash+=revenue
                trades.append(dict(at=int(rows[i][0]),netEUR=str(revenue-basis)))
                qty=Decimal(0);basis=Decimal(0)
            equity=cash+qty*mark*(1-slip)*(1-fee);peak=max(peak,equity);dd=max(dd,peak-equity)
        return dict(netEUR=str(cash+qty*Decimal(str(rows[end-1][4]))*(1-slip)*(1-fee)-initial),
            closedTrades=len(trades),maxDrawdownEUR=str(dd),trades=trades,openQuantity=str(qty))
    training=run(60,split);holdout=run(split,len(rows))
    return dict(strategy=strategy,version='rules-v1',dataSHA256=hashlib.sha256(json.dumps(rows,separators=(',',':')).encode()).hexdigest(),
        assumptions=dict(capitalEUR=str(initial),feePerSide=str(fee),slippagePerSide=str(slip),quantityStep=str(step),minimumCostEUR=str(minimum)),
        training=training,holdout=holdout,liveEligible=False,
        nextStep='Separat skuggprov och ägargodkännande krävs. Ett historiskt test bevisar inte framtida avkastning.')
