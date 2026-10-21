"""Local owner operations: python -m backend.ops --help. Never sends money."""
import argparse
import sqlite3
import os
import json
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
    owner.add_argument('action', choices=['release', 'resume', 'approve'])
    owner.add_argument('--amount', type=str)
    health = commands.add_parser('check', help='Check local service health; require fresh data when running')
    health.add_argument('--port',type=int,default=8000)
    snapshot = commands.add_parser('backup', help='Create a consistent SQLite backup, including WAL')
    snapshot.add_argument('destination')
    args = parser.parse_args()
    if args.operation == 'approve':
        if not Path(args.db).is_file():
            parser.error('Start the server once before creating owner approvals')
        print(issue(operator_key(args.db), 'approve' if args.action=='resume' else args.action, args.amount))
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
        print('OK: paper-only service; '+('running' if state['running'] else 'paused'))
    else:
        backup(args.db, args.destination)
        print('Backup verified:', args.destination)


if __name__ == '__main__':
    main()
