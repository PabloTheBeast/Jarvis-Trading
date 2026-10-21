"""Local owner operations: python -m backend.ops --help. Never sends money."""
import argparse
import getpass
import base64
import sqlite3
import os
import json
import sys
import uuid
from datetime import datetime, timezone
from urllib.request import urlopen
from pathlib import Path

from .approval import issue, operator_key


def backup(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination or destination.exists():
        raise ValueError('Backup destination must be a new file')
    source_db = sqlite3.connect(source.as_uri()+'?mode=ro', uri=True)
    try:
        fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        target = sqlite3.connect(destination)
        try:
            source_db.backup(target)
            if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Backup integrity check failed')
        finally:
            target.close()
    finally:
        source_db.close()


def main():
    parser = argparse.ArgumentParser(description='JARVIS local owner operations')
    parser.add_argument('--db', default=str(Path(__file__).resolve().parents[1]/'.jarvis/paper.sqlite3'))
    commands = parser.add_subparsers(dest='operation', required=True)
    owner = commands.add_parser('approve', help='Create a single-use owner approval, valid for 120 seconds')
    owner.add_argument('action', choices=['release', 'resume', 'approve', 'live-order', 'live-cancel','session-start','agent-review'])
    owner.add_argument('--amount', type=str)
    owner.add_argument('--ticket',type=str)
    owner.add_argument('--session',type=str)
    owner.add_argument('--agent',choices=['gamma','zeta','iota'])
    credentials=commands.add_parser('configure-kraken',help='Write local owner-only Kraken credentials; inputs are hidden')
    credentials.add_argument('--replace',action='store_true')
    research=commands.add_parser('research-test',help='Evaluate built-in rules on imported JSON OHLC; never sends orders')
    research.add_argument('file',type=Path)
    research.add_argument('--strategy',choices=['Trend','Momentum','Mean reversion'],required=True)
    research.add_argument('--idea',required=True)
    health = commands.add_parser('check', help='Check local service health; require fresh data when running')
    health.add_argument('--port',type=int,default=8000)
    snapshot = commands.add_parser('backup', help='Create a consistent SQLite backup, including WAL')
    snapshot.add_argument('destination')
    args = parser.parse_args()
    if args.operation == 'approve':
        if not Path(args.db).is_file():
            parser.error('Start the server once before creating owner approvals')
        subject=None
        if args.action=='agent-review':
            import hashlib
            if not args.agent:parser.error('--agent is required')
            with sqlite3.connect(Path(args.db).resolve().as_uri()+'?mode=ro',uri=True) as db:
                rows={k:json.loads(v) for k,v in db.execute('SELECT key,payload FROM supervision WHERE key IN (?,?)',('agent:'+args.agent,'shadow:'+args.agent))}
            agent=rows.get('agent:'+args.agent)
            shadow=rows.get('shadow:'+args.agent)
            if not agent or agent['status']!='REVIEW_READY' or not shadow:parser.error('Agent must be REVIEW_READY')
            subject=hashlib.sha256(json.dumps(dict(agent=agent,shadow=shadow),sort_keys=True).encode()).hexdigest()
            print('Godkänn begränsad återstart för '+args.agent+'. Skuggavslut: '+str(len(shadow['trades']))+'; netto '+shadow['netEUR']+' EUR; max nedgång '+shadow['maxDrawdownEUR']+' EUR. Ny sessionsplan krävs.',file=sys.stderr)
        if args.action=='session-start':
            from .sessions import digest
            if not args.session:parser.error('--session is required')
            with sqlite3.connect(Path(args.db).resolve().as_uri()+'?mode=ro',uri=True) as db:
                row=db.execute('SELECT payload FROM trading_sessions WHERE id=?',(args.session,)).fetchone()
            if not row:parser.error('Session not found')
            session=json.loads(row[0]);subject=digest(session)
            if session.get('fingerprint')!=subject or session['status']!='PLANNED':parser.error('A new unchanged plan is required')
            print('LOKAL EFTERBEVAKNING: '+str(session.get('protectionMinutes',0))+' minuter efter planerad sluttid; endast försäljning av agentinnehav, servern måste köra. Slut '+str(session.get('protectionExpiresAt','ingen'))+'.',file=sys.stderr)
            print('AUTOMATISK KRAKEN-HANDEL: kapitaltak '+session['capitalEUR']+' EUR, nya köp högst '+session['newCashEUR']+' EUR, budgetar '+json.dumps(session['budgets'])+'; förlustgräns '+session['maxLossEUR']+' EUR; avslutspolicy '+session['endPolicy']+'; slut senast '+session['expiresAt']+'; max 15 min säljavslut därefter. Inga uttag. Godkänn bara om planen stämmer.',file=sys.stderr)
        if args.action in ('live-order','live-cancel'):
            from .live import fingerprint
            if not args.ticket:
                parser.error('--ticket is required')
            with sqlite3.connect(Path(args.db).resolve().as_uri()+'?mode=ro',uri=True) as db:
                row=db.execute('SELECT payload FROM live_tickets WHERE id=?',(args.ticket,)).fetchone()
            if not row:
                parser.error('Ticket not found')
            ticket=json.loads(row[0])
            subject=fingerprint(ticket)+(':cancel' if args.action=='live-cancel' else '')
            print('LIVE '+ticket['side'].upper()+' '+ticket['volume']+' '+ticket['pair']+' limit '+ticket['price']+' EUR; ticket '+ticket['id'],file=sys.stderr)
        print(issue(operator_key(args.db), 'approve' if args.action=='resume' else args.action, args.amount, subject=subject))
    elif args.operation == 'research-test':
        from .research import evaluate
        if args.file.stat().st_size>32*1024*1024:parser.error('Import exceeds 32 MiB')
        report=evaluate(json.loads(args.file.read_text()),args.strategy)
        with sqlite3.connect(args.db) as db:
            row=db.execute('SELECT payload FROM supervision WHERE key=?',('research:'+args.idea,)).fetchone()
            if not row:parser.error('Research idea not found; submit it in the dashboard first')
            idea=json.loads(row[0]);idea.update(status='BACKTEST_REVIEW',evaluation=report)
            payload=json.dumps(idea);stamp=datetime.now(timezone.utc).isoformat()
            db.execute('UPDATE supervision SET payload=? WHERE key=?',(payload,'research:'+args.idea))
            db.execute('INSERT INTO supervision_events(at,kind,payload) VALUES(?,?,?)',(stamp,'RESEARCH_EVALUATED',payload))
        print(json.dumps(report,ensure_ascii=False,indent=2))
    elif args.operation == 'configure-kraken':
        path=Path(args.db).parent/'kraken.credentials.json'
        path.parent.mkdir(parents=True,exist_ok=True)
        if path.exists() and not args.replace:
            parser.error('Credential file exists; use --replace deliberately to replace it')
        key=getpass.getpass('Kraken API key (hidden): ').strip()
        secret=getpass.getpass('Kraken API secret (hidden): ').strip()
        try:
            decoded=base64.b64decode(secret,validate=True)
            if not key or len(decoded)<32:
                raise ValueError()
        except Exception:
            parser.error('Invalid local credentials')
        temporary=path.with_name(path.name+'.tmp-'+uuid.uuid4().hex)
        fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as output:
            json.dump(dict(apiKey=key,apiSecret=secret),output)
        os.replace(temporary,path)
        print('Local credentials saved with mode 0600. No order has been sent. Restart the server to test connection.')
    elif args.operation == 'check':
        with urlopen('http://127.0.0.1:'+str(args.port)+'/api/state',timeout=5) as response:
            snapshot=json.load(response)
        state=snapshot['state']
        if not snapshot['paperOnly']:
            raise SystemExit('Unexpected trading mode')
        if state['running']:
            data=state['marketData']
            if data['status']!='ok' or not data.get('at'):
                raise SystemExit('Market data unavailable while running')
            age=(datetime.now(timezone.utc)-datetime.fromisoformat(data['at'])).total_seconds()
            if not 0 <= age <= 90:
                raise SystemExit('Market data is stale')
        print('OK: paper engine '+('running' if state['running'] else 'paused')+'; check /api/exchange separately for live mode')
    else:
        backup(args.db, args.destination)
        print('Backup verified:', args.destination)


if __name__ == '__main__':
    main()
