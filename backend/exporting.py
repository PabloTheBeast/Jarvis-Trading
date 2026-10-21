"""Stable UTF-8 CSV exports with exact monetary strings and spreadsheet protection."""
import csv
import re
import io
from decimal import Decimal

from .accounting import SCALE


def safe(value):
    if isinstance(value, str) and value.startswith(('=', '+', '-', '@', '\t', '\r')) and not re.fullmatch(r'-?\d+(?:\.\d+)?', value):
        return "'"+value
    return value


def csv_bytes(rows, fields):
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction='ignore')
    writer.writeheader()
    for row in rows:
        writer.writerow({k: safe(v) for k, v in row.items()})
    return ('\ufeff'+output.getvalue()).encode('utf-8')


def trades(state):
    return csv_bytes(reversed(state['orderHistory']),
                     ['fillId', 'orderId', 'tick', 'day', 'agent', 'id', 'side', 'qty', 'price', 'amount', 'fee', 'pnl', 'reason'])


def capital(state):
    rows = []
    for entry in reversed(state.get('capitalHistory', [])):
        row = dict(entry)
        for source, target in [('cashDeltaNanoSek', 'cashDeltaSEK'), ('vaultDeltaNanoSek', 'vaultDeltaSEK'),
                               ('cashNanoSek', 'cashSEK'), ('vaultNanoSek', 'vaultSEK'),
                               ('ownerNetNanoSek', 'ownerNetSEK'), ('realizedNanoSek', 'realizedSEK')]:
            row[target] = format(Decimal(entry[source])/SCALE, 'f')
        rows.append(row)
    # Old rows are retained rather than invented or silently discarded at migration.
    for field in ['ownerMovements', 'vaultMovements']:
        for n, entry in enumerate(reversed(state[field]), 1):
            if entry.get('legacy'):
                rows.append(dict(id='LEGACY-'+field+'-'+str(n), type=entry['type'],
                                 at=entry.get('at', entry.get('date', '')), day=entry['day'],
                                 tick=entry['tick'], legacyAmountSEK=entry['amount'], reason='Imported historical row; timestamp/balances may be unavailable'))
    return csv_bytes(rows, ['id', 'at', 'type', 'cashDeltaSEK', 'vaultDeltaSEK', 'cashSEK',
                            'vaultSEK', 'ownerNetSEK', 'realizedSEK', 'actor', 'approvalId', 'reason',
                            'tick', 'day', 'orderId', 'fillId', 'feeNanoSek', 'legacyAmountSEK'])
