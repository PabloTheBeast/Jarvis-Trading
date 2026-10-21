"""Read-only agent reports and CHEF alerts; never creates orders or consumes signals."""
from datetime import datetime, timedelta

CONDITIONS={
    'Trend':'Köpläge: pris över SMA20, SMA20 över SMA50 och RSI under 70. Säljläge: pris under SMA20.',
    'Momentum':'Köpläge: positivt 25-minutersmomentum över volatilitetströskeln och RSI under 75. Säljläge: momentum under −0,3 %.',
    'Mean reversion':'Köpläge: RSI under 30 och pris under SMA20. Säljläge: RSI över 55 och pris över SMA20.',
}


def review(engine):
    s,now=engine.s,engine.now
    reports=s.setdefault('agentReports',{})
    summaries=s.setdefault('chefSignals',{})
    reviewed=s.setdefault('advisoryBars',{})
    previous=s.setdefault('advisoryActions',{})
    sent=s.setdefault('advisoryAlertIds',[])
    agents=engine.agents()
    current={a['id']+'|'+symbol for a in agents for symbol in a['ids'] if symbol in ('BTC-SIM','ETH-SIM')}
    for key in list(reports):
        if key not in current:del reports[key]
    for symbol in ('BTC-SIM','ETH-SIM'):
        analysis=s.get('marketAnalysis',{}).get(symbol,{})
        closed=analysis.get('closedAt')
        try:
            age=(now-datetime.fromisoformat(closed)).total_seconds()
        except (ValueError,TypeError):
            age=float('inf')
        ready=analysis.get('status')=='ready' and 0<=age<300
        votes={'BUY':set(),'SELL':set()}
        for agent in agents:
            if symbol not in agent['ids']:continue
            strategy=agent['strategy']
            side=(analysis.get('signals',{}).get(strategy) or 'WAIT') if ready else 'WAIT'
            eligible=not s['agentHalt'].get(agent['id']) and s['agentStatus'].get(agent['id'])!='KARANTÄN'
            if eligible and side in votes:votes[side].add(strategy)
            reports[agent['id']+'|'+symbol]=dict(agent=agent['name'],agentId=agent['id'],instrument=symbol,
                strategy=strategy,side=side,eligible=eligible,status='reading' if ready else 'waiting',
                checkedAt=now.isoformat(),closedAt=closed,barId=analysis.get('barId'),
                reason=analysis.get('reasons',{}).get(strategy,'Inga verifierade signaler.') if ready else 'Inväntar en färsk avslutad femminutersstapel.',
                conditions=CONDITIONS.get(strategy,''))
        side='WAIT'
        reason='Strategierna är oense eller färre än två olika strategier ger samma signal.'
        if not ready:reason='Ingen aktuell, verifierad stapel. Ingen köp- eller säljsignal.'
        elif len(votes['BUY'])>=2 and not votes['SELL']:side='BUY'
        elif len(votes['SELL'])>=2 and not votes['BUY']:side='SELL'
        if side=='BUY' and (s['halt'] or s['globalPause']):
            side='BLOCKED';reason='CHEF:s riskpaus blockerar köpsignalen.'
        elif side in ('BUY','SELL'):
            reason='Minst två olika strategier är överens, utan motsatt signal från övriga granskare.'
        name='BTC/EUR' if symbol=='BTC-SIM' else 'ETH/EUR'
        summaries[symbol]=dict(instrument=symbol,name=name,side=side,reason=reason,checkedAt=now.isoformat(),
            closedAt=closed,validUntil=(datetime.fromisoformat(closed)+timedelta(seconds=300)).isoformat() if ready else None,
            referenceEUR=analysis.get('close') if ready else None,
            buyStrategies=sorted(votes['BUY']),sellStrategies=sorted(votes['SELL']),
            scope='analysis-only',orderChecked=False)
        if not ready:
            previous[symbol]='WAIT'
            continue
        stamp=analysis.get('barId')
        # Separate from execution signalDecisions: observing must never consume a trade intent.
        key=str(stamp)+'|'+side
        if reviewed.get(symbol)==key:continue
        reviewed[symbol]=key
        changed=previous.get(symbol)!=side
        previous[symbol]=side
        event_id=f'crypto:{symbol}:{stamp}:{side}'
        if changed and side in ('BUY','SELL') and event_id not in sent:
            label='möjligt köpläge' if side=='BUY' else 'möjligt säljläge om du äger tillgången'
            supporters=', '.join(sorted(votes[side]))
            engine.announce(f'{name}: {label}. {supporters} är överens på avslutad 5-minutersstapel. Bedömningen skickar ingen order; verkligt saldo, innehav och orderrisk är inte kontrollerade.')
            s['notifications'][0].update(id=event_id,kind='CRYPTO_SIGNAL',
                instrument=symbol,side=side,closedAt=closed,validUntil=summaries[symbol]['validUntil'])
            sent.append(event_id)
            del sent[:-200]
            del s['notifications'][500:]
