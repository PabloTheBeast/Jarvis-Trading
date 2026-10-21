"""Pure rule-based signals: no order placement or account mutation."""

def signal(state, a, symbol):
    p = state['prices'][symbol]
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
        if (symbol == 'ETF-BOND' and state['tick'] % 8 == 0 or
                symbol == 'ETF-WORLD' and curr > avg(6) and state['tick'] % 5 == 0):
            return 'BUY'
        return 'SELL' if curr < avg(6)*.975 else None
