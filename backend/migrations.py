"""Non-destructive schema upgrades. Store backs up the database before calling."""
from datetime import date, timedelta

from .calendar import clock
from .accounting import Ledger

VERSION = 2


def migrate(engine):
    s = engine.s
    version = s.get('schemaVersion', 1)
    if version > VERSION:
        raise ValueError('Database was created by a newer JARVIS version')
    if version == VERSION:
        return
    Ledger(engine)
    s['closedDates'] = sorted({r['date'] for r in s['dayReports']})
    # Historical duplicate reports cannot count toward any new expansion.
    s['expansionDates'] = []
    s['lastExpansionReview'] = 0
    s['weeklyLastDate'] = s.get('reportDate') or clock(engine.now)[0]
    s['nextSweepDate'] = (date.fromisoformat(s['weeklyLastDate'])+timedelta(days=7)).isoformat()
    s['intentOrders'] = {}
    for order in reversed(s['orders']):
        intent = order.get('intentId') or '|'.join(map(str, (order['tick'], order['agentId'], order['instrument'], order['side'], order['reason'])))
        order['intentId'] = intent
        s['intentOrders'].setdefault(intent, order['orderId'])
    for number, fill in enumerate(reversed(s['orderHistory']), 1):
        fill.setdefault('fillId', 'LEGACY-F'+str(number))
    for field in ('ownerMovements', 'vaultMovements'):
        for entry in s[field]:
            entry['legacy'] = True
    s.setdefault('capitalHistory', [])
    Ledger(engine).movement('MIGRATION_OPENING', 0, 0, 'Opening exact balances after legacy migration')
    s.setdefault('approvals', [])
    s['schemaVersion'] = VERSION
    s['running'] = False
    engine.log('MIGRATION', 'Schema 2: exact nano-SEK ledger; historical day reports retained but excluded from new expansion reviews.')
