"""Single-process, loopback-only service with transactional SQLite persistence."""
import argparse
import copy
import fcntl
import json
import logging
import signal
import sqlite3
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .engine import ASSETS, Engine, new_state
from .market import Kraken, MarketError

ROOT = Path(__file__).resolve().parents[1]


class Store:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.guard = open(str(path)+'.lock', 'a')
        try:
            fcntl.flock(self.guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.guard.close()
            raise RuntimeError('Another JARVIS process owns this database') from None
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS journal (seq INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS commands (id TEXT PRIMARY KEY, request TEXT NOT NULL);
        ''')
        row = self.db.execute('SELECT payload FROM state WHERE id=1').fetchone()
        self.state = json.loads(row[0]) if row else new_state()
        Engine(self.state).audit()
        # Persisted stop/start intent is retained. A crashed server resumes only
        # if it was explicitly started before; market data must be fetched afresh.
        self.state['marketData']['status'] = 'waiting'
        self.state['marketData']['error'] = None
        self.change('startup', lambda e: None)

    def change(self, kind, action, command_id=None, request=None):
        with self.lock:
            if command_id:
                row = self.db.execute('SELECT request FROM commands WHERE id=?', (command_id,)).fetchone()
                if row:
                    if row[0] != request:
                        raise ValueError('Idempotency key was used for a different request')
                    return self.snapshot()
            candidate = copy.deepcopy(self.state)
            engine = Engine(candidate)
            action(engine)
            engine.audit()
            payload = json.dumps(candidate, allow_nan=False, ensure_ascii=False)
            journal = {key: candidate[key] for key in ('tick', 'cash', 'vault', 'realizedTotal', 'ownerNetDeposits', 'running')}
            for field in ('orderEvents', 'orderHistory', 'ownerMovements', 'vaultMovements', 'dayReports'):
                count = len(candidate[field])-len(self.state[field])
                journal[field] = candidate[field][:count] if count > 0 else []
            with self.db:
                self.db.execute('INSERT OR REPLACE INTO state VALUES (1, ?)', (payload,))
                self.db.execute('INSERT INTO journal(at, kind, payload) VALUES (?, ?, ?)',
                                (datetime.now(timezone.utc).isoformat(), kind, json.dumps(journal, allow_nan=False, ensure_ascii=False)))
                if command_id:
                    self.db.execute('INSERT INTO commands VALUES (?, ?)', (command_id, request))
            self.state = candidate
            return self.snapshot()

    def snapshot(self):
        with self.lock:
            return dict(state=copy.deepcopy(self.state), agents=Engine(self.state).agents(), assets=ASSETS, paperOnly=True)

    def close(self):
        self.db.close()
        self.guard.close()


class Runtime:
    def __init__(self, store, market=None):
        self.store = store
        self.market = market or Kraken()
        self.quit = threading.Event()
        self.fetch_lock = threading.Lock()
        self.last_fetch = 0
        self.last_prices = None
        self.last_metadata = {}
        self.thread = None

    def prices(self):
        with self.fetch_lock:
            if self.last_prices is not None and time.monotonic()-self.last_fetch < 5:
                return self.last_prices
            prices = self.market.fetch()
            self.last_prices, self.last_fetch = prices, time.monotonic()
            self.last_metadata = dict(getattr(self.market, "metadata", {}))
            return prices

    def step(self, only_running=False, elapsed_ms=0):
        try:
            prices = self.prices()
        except MarketError:
            self.store.change('market-error', lambda e: e.s['marketData'].update(
                status='error', error='Kraken/ECB market data unavailable; no tick or fill executed'))
            raise
        def apply(engine):
            # Stop commands are allowed while HTTP data is being fetched.
            if only_running and not engine.s['running']:
                return
            engine.step(prices)
            engine.s["marketData"].update(self.last_metadata)
            engine.s["activeMs"] += elapsed_ms
        return self.store.change('tick', apply)

    def command(self, name, data, key):
        request = json.dumps(dict(name=name, data=data), sort_keys=True, allow_nan=False)
        if name == 'step':
            # Replayed commands must not require network access.
            with self.store.lock:
                row = self.store.db.execute('SELECT request FROM commands WHERE id=?', (key,)).fetchone()
                if row:
                    if row[0] != request:
                        raise ValueError('Idempotency key was used for a different request')
                    return self.store.snapshot()
            try:
                prices = self.prices()
            except MarketError:
                self.store.change('market-error', lambda e: e.s['marketData'].update(status='error', error='Kraken/ECB market data unavailable'))
                raise
            def apply(engine):
                engine.step(prices)
                engine.s['marketData'].update(self.last_metadata)
            return self.store.change('step', apply, key, request)
        return self.store.change(name, lambda e: e.command(name, data), key, request)

    def loop(self):
        deadline = time.monotonic()
        last_active = deadline
        while not self.quit.wait(.1):
            snap = self.store.snapshot()['state']
            if not snap['running']:
                deadline = time.monotonic()
                last_active = deadline
                continue
            if time.monotonic() < deadline:
                continue
            started = time.monotonic()
            try:
                self.step(only_running=True, elapsed_ms=(started-last_active)*1000)
            except MarketError:
                logging.warning('Kraken/ECB unavailable; tick skipped; retrying after interval')
            except Exception:
                logging.exception('Engine stopped after unexpected failure')
                self.store.change('runtime-error', lambda e: e.s.update(running=False))
            last_active = started
            deadline = time.monotonic()+snap['autoPace']/1000

    def start(self):
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.quit.set()
        if self.thread:
            self.thread.join(15)


def make_server(runtime, port=8000):
    class Handler(BaseHTTPRequestHandler):
        def send(self, status, body, content_type='application/json'):
            data = body if isinstance(body, bytes) else json.dumps(body, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

        def host_allowed(self):
            return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}')

        def do_GET(self):
            if not self.host_allowed():
                self.send(403, dict(error='Invalid Host'))
                return
            path = urlsplit(self.path).path
            if path == '/api/state':
                self.send(200, runtime.store.snapshot())
            elif path == '/api/health':
                snap = runtime.store.snapshot()
                self.send(200, dict(status='ok', paperOnly=True, tick=snap['state']['tick'], marketData=snap['state']['marketData']))
            elif path == '/':
                self.send(200, (ROOT/'index (2).html').read_bytes(), 'text/html; charset=utf-8')
            elif path == '/dashboard.js':
                self.send(200, (ROOT/'dashboard.js').read_bytes(), 'text/javascript; charset=utf-8')
            else:
                self.send(404, dict(error='Not found'))

        def do_POST(self):
            # Custom header + same-origin validation prevents cross-site form/fetch
            # mutations. No CORS is enabled. Service must remain loopback-only.
            origin = self.headers.get('Origin')
            if (not self.host_allowed() or self.headers.get('X-Jarvis-Client') != 'dashboard' or
                    origin is not None and origin != 'http://'+self.headers.get('Host', '')):
                self.send(403, dict(error='Same-origin client required'))
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 4096:
                    raise ValueError('Invalid request length')
                if self.headers.get_content_type() != 'application/json':
                    raise ValueError('JSON required')
                data = json.loads(self.rfile.read(length), parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
                if not isinstance(data, dict):
                    raise ValueError('JSON object required')
                key = self.headers.get('Idempotency-Key', '')
                if not 1 <= len(key) <= 100:
                    raise ValueError('Idempotency-Key required')
                path = urlsplit(self.path).path
                if not path.startswith('/api/commands/'):
                    self.send(404, dict(error='Not found'))
                    return
                self.send(200, runtime.command(path.rsplit('/', 1)[-1], data, key))
            except (ValueError, TypeError) as exc:
                self.send(400, dict(error=str(exc)))
            except MarketError:
                self.send(503, dict(error='Kraken/ECB unavailable; no tick executed'))
            except Exception:
                logging.exception('Request failed')
                self.send(500, dict(error='Internal error; transaction rolled back'))

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def main():
    parser = argparse.ArgumentParser(description='JARVIS v0.5 paper-only backend')
    parser.add_argument('--db', default=str(ROOT/'.jarvis/paper.sqlite3'))
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    store = Store(args.db)
    runtime = Runtime(store)
    try:
        server = make_server(runtime, args.port)
    except BaseException:
        store.close()
        raise
    # SIGTERM exits serve_forever through KeyboardInterrupt, then closes SQLite.
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    runtime.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        runtime.stop()
        store.close()


if __name__ == '__main__':
    main()
