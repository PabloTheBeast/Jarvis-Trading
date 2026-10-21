"""FIFO display of verified exchange fills; not a tax or cash ledger."""
from decimal import Decimal,localcontext


def history(fills):
    lots={};closed=[];unmatched=[]
    with localcontext() as context:
        context.prec=40
        for fill in sorted(fills,key=lambda row:(row['executedAt'],row['tradeId'])):
            key=(fill['pair'],fill.get('agentId'))
            quantity=Decimal(fill['quantity'])
            cost=Decimal(fill['costEUR']);fee=Decimal(fill['feeEUR'])
            if fill['side']=='buy':
                lots.setdefault(key,[]).append(dict(fill=fill,quantity=quantity,basis=cost+fee))
                continue
            remaining=quantity;proceeds=cost-fee
            while remaining and lots.get(key):
                lot=lots[key][0];taken=min(remaining,lot['quantity'])
                basis=lot['basis']*taken/lot['quantity']
                revenue=proceeds*taken/remaining
                closed.append(dict(pair=fill['pair'],agentId=fill.get('agentId'),quantity=str(taken),
                    boughtAt=lot['fill']['executedAt'],soldAt=fill['executedAt'],
                    buyTradeId=lot['fill']['tradeId'],sellTradeId=fill['tradeId'],
                    costIncludingFeeEUR=str(basis),proceedsAfterFeeEUR=str(revenue),netEUR=str(revenue-basis)))
                remaining-=taken;proceeds-=revenue;lot['quantity']-=taken;lot['basis']-=basis
                if not lot['quantity']:lots[key].pop(0)
            if remaining:
                unmatched.append(dict(pair=fill['pair'],quantity=str(remaining),soldAt=fill['executedAt'],
                    sellTradeId=fill['tradeId'],netEUR=None,reason='Köp saknas i JARVIS-historiken; inköpsdatum och resultat är okända.'))
        opened=[dict(pair=lot['fill']['pair'],agentId=lot['fill'].get('agentId'),boughtAt=lot['fill']['executedAt'],
            buyTradeId=lot['fill']['tradeId'],quantity=str(lot['quantity']),costIncludingFeeEUR=str(lot['basis']))
            for group in lots.values() for lot in group]
    return dict(executions=sorted(fills,key=lambda f:(f['executedAt'],f['tradeId']),reverse=True),
                closed=closed[::-1],open=opened,unmatchedSales=unmatched[::-1],scope='verified-jarvis-orders-only')
