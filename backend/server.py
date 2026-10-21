"""Single-process, loopback-only service with transactional SQLite persistence."""
import argparse
import uuid
import os
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
from .migrations import migrate, VERSION
from .calendar import clock
from .approval import operator_key, verify
from .exporting import trades, capital
from .logging_config import configure
from .live import LiveService
from .kraken_private import ExchangeError, ExchangeUnknown


ROOT = Path(__file__).resolve().parents[1]


class Store:
    def __init__(self, path):
        path = Path(path)
        self.path = path
        self.paper_mode=False
        self.fault_hook = None
        path.parent.mkdir(parents=True, exist_ok=True)
        self.guard = open(str(path)+'.lock', 'a')
        try:
            fcntl.flock(self.guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.guard.close()
            raise RuntimeError('Another JARVIS process owns this database') from None
        try:
            self._initialize(path)
        except BaseException:
            if hasattr(self, 'db'):
                self.db.close()
            self.guard.close()
            raise

    def _initialize(self, path):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        os.chmod(path, 0o600)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS journal (seq INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS commands (id TEXT PRIMARY KEY, request TEXT NOT NULL);
        ''')
        row = self.db.execute('SELECT payload FROM state WHERE id=1').fetchone()
        self.state = json.loads(row[0]) if row else new_state()
        if row and self.state.get('schemaVersion', 1) < VERSION:
            target = sqlite3.connect(str(path)+'.pre-v2-'+uuid.uuid4().hex[:10]+'.sqlite3')
            try:
                target_path = target.execute('PRAGMA database_list').fetchone()[2]
                os.chmod(target_path, 0o600)
                self.db.backup(target)
                if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('Pre-migration backup failed')
            finally:
                target.close()
        migrate(Engine(self.state))
        Engine(self.state).audit()
        self.operator_key = operator_key(path)
        self.db.executescript('''
            CREATE TRIGGER IF NOT EXISTS journal_no_update BEFORE UPDATE ON journal
            BEGIN SELECT RAISE(ABORT, 'Journal is append-only'); END;
            CREATE TRIGGER IF NOT EXISTS journal_no_delete BEFORE DELETE ON journal
            BEGIN SELECT RAISE(ABORT, 'Journal is append-only'); END;
        ''')
        # Crash recovery never resumes trading automatically. Risk pauses remain intact.
        self.state['running'] = False
        self.state['stopGeneration'] = self.state.get('stopGeneration', 0)+1
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
            candidate['revision']=self.state.get('revision',0)+1
            engine.audit()
            payload = json.dumps(candidate, allow_nan=False, ensure_ascii=False)
            journal = {key: candidate[key] for key in ('tick', 'cash', 'vault', 'realizedTotal', 'ownerNetDeposits', 'running')}
            for field in ('orderEvents', 'orderHistory', 'ownerMovements', 'vaultMovements', 'dayReports', 'capitalHistory', 'approvals'):
                count = len(candidate[field])-len(self.state[field])
                journal[field] = candidate[field][:count] if count > 0 else []
            with self.db:
                self.db.execute('INSERT OR REPLACE INTO state VALUES (1, ?)', (payload,))
                if self.fault_hook:
                    self.fault_hook('after_state_write')
                self.db.execute('INSERT INTO journal(at, kind, payload) VALUES (?, ?, ?)',
                                (datetime.now(timezone.utc).isoformat(), kind, json.dumps(journal, allow_nan=False, ensure_ascii=False)))
                if command_id:
                    self.db.execute('INSERT INTO commands VALUES (?, ?)', (command_id, request))
            if self.fault_hook:
                self.fault_hook('after_commit')
            self.state = candidate
            logging.info('Committed command', extra={'command': kind, 'requestId': command_id,
                                                    'tick': candidate['tick'], 'status': 'committed'})
            return self.snapshot()

    def snapshot(self):
        with self.lock:
            public = copy.deepcopy(self.state)
            public.pop('book', None)
            public.pop('intentOrders', None)
            public['canOpenDay'] = not public['dayClosed'] or clock(datetime.now(timezone.utc))[0] > public['reportDate']
            return dict(state=public, agents=Engine(self.state).agents(), assets=ASSETS, paperOnly=True,paperMode=self.paper_mode,scope='paper-portfolio',serverTime=datetime.now(timezone.utc).isoformat())

    def close(self):
        self.db.close()
        self.guard.close()


class Runtime:
    def __init__(self, store, market=None,paper_enabled=None):
        self.store = store
        self.market = market or Kraken()
        self.quit = threading.Event()
        self.fetch_lock = threading.Lock()
        self.last_fetch = 0
        self.last_prices = None
        self.last_metadata = {}
        self.thread = None
        self.paper_enabled=os.environ.get('JARVIS_PAPER_MODE')=='1' if paper_enabled is None else paper_enabled
        store.paper_mode=self.paper_enabled
        self.monitor_only=os.environ.get('JARVIS_MONITOR_ONLY')=='1'
        self.exchange = LiveService(store,enabled=False if self.monitor_only else None)
        self.exchange.monitor_only=self.monitor_only
        self.exchange.monitoring=not self.paper_enabled or self.monitor_only
        self.exchange.sessions.enabled=self.exchange.sessions.enabled and not self.paper_enabled
        self.monitor_thread=None

    def monitor_market(self):
        prices,metadata=self.prices()
        if not self.quit.is_set():
            def observe(engine):
                engine.real_monitoring=not self.paper_enabled
                engine.observe(prices,metadata)
            self.store.change('market-observation',observe)

    def monitor_loop(self):
        market_due=account_due=0
        while not self.quit.is_set():
            now=time.monotonic()
            if now>=market_due:
                try:
                    self.monitor_market()
                except Exception:
                    # No credential values or raw exchange responses enter logs.
                    logging.warning('Read-only market refresh failed; existing quote timestamps retained')
                market_due=time.monotonic()+15
                self.exchange.sessions.tick()
            if not self.quit.is_set() and now>=account_due:
                try:
                    self.exchange.inspect()
                except (ExchangeError,ExchangeUnknown):
                    logging.warning('Read-only Kraken account refresh failed; inspect account status')
                except Exception:
                    logging.warning('Read-only account refresh failed unexpectedly')
                account_due=time.monotonic()+60
            self.quit.wait(.5)

    def prices(self):
        with self.fetch_lock:
            if self.last_prices is not None and time.monotonic()-self.last_fetch < 5:
                return copy.deepcopy(self.last_prices), copy.deepcopy(self.last_metadata)
            prices = self.market.fetch()
            self.last_prices, self.last_fetch = prices, time.monotonic()
            self.last_metadata = dict(getattr(self.market, "metadata", {}))
            return copy.deepcopy(prices), copy.deepcopy(self.last_metadata)

    def step(self, only_running=False, elapsed_ms=0):
        generation = self.store.snapshot()['state'].get('stopGeneration', 0)
        try:
            prices, metadata = self.prices()
        except MarketError:
            self.store.change('market-error', lambda e: e.s['marketData'].update(
                status='error', error='Kraken/ECB market data unavailable; no tick or fill executed'))
            raise
        def apply(engine):
            # Stop commands are allowed while HTTP data is being fetched.
            if engine.s.get('stopGeneration', 0) != generation or only_running and not engine.s['running']:
                return
            engine.step(prices, metadata=metadata)
            engine.s["activeMs"] += elapsed_ms
        return self.store.change('tick', apply)

    def command(self, name, data, key, approval=None):
        if not self.paper_enabled and name not in {'refresh-market','read-notices','save','stop'}:
            raise ValueError('Simulerad handel är avstängd. Bevakning och Kraken-konto är tillgängliga.')
        if (self.monitor_only or not self.paper_enabled) and name in ('start','step'):
            raise ValueError('Bevakningsläge: handel är avstängd. Kurser och kontosaldo uppdateras automatiskt.')
        request = json.dumps(dict(name=name, data=data), sort_keys=True, allow_nan=False)
        with self.store.lock:
            prior = self.store.db.execute('SELECT request FROM commands WHERE id=?', (key,)).fetchone()
            if prior:
                if prior[0] != request:
                    raise ValueError('Idempotency key was used for a different request')
                return self.store.snapshot()
        generation = self.store.snapshot()['state'].get('stopGeneration', 0)
        if name in ('step','refresh-market'):
            # Replayed commands must not require network access.
            with self.store.lock:
                row = self.store.db.execute('SELECT request FROM commands WHERE id=?', (key,)).fetchone()
                if row:
                    if row[0] != request:
                        raise ValueError('Idempotency key was used for a different request')
                    return self.store.snapshot()
            try:
                prices, metadata = self.prices()
            except MarketError:
                self.store.change('market-error', lambda e: e.s['marketData'].update(status='error', error='Kraken/ECB market data unavailable'))
                raise
            def apply(engine):
                if engine.s.get('stopGeneration', 0) != generation:
                    raise ValueError('Step cancelled by emergency stop')
                if name=='refresh-market':
                    engine.observe(prices,metadata)
                else:
                    engine.step(prices,metadata=metadata)
            return self.store.change(name,apply,key,request)
        signed = verify(self.store.operator_key, approval or '', name, data.get('amount')) if name in ('release', 'approve') else None
        def apply(engine):
            if signed:
                verify(self.store.operator_key, approval, name, data.get('amount'))
                if any(a['id'] == signed['nonce'] for a in engine.s.get('approvals', [])):
                    raise ValueError('Owner approval already consumed')
                engine.operator, engine.approval_id = signed['actor'], signed['nonce']
                engine.s.setdefault('approvals', []).insert(0, dict(id=signed['nonce'], actor=signed['actor'],
                    action=name, amountNanoSek=signed.get('amountNanoSek'), at=engine.now.isoformat(), commandId=key))
            engine.command(name, data)
        return self.store.change(name, apply, key, request)

    def loop(self):
        deadline = time.monotonic()
        last_active = deadline
        while not self.quit.wait(.1):
            with self.store.lock:
                snap={key:self.store.state[key] for key in ('running','autoPace')}
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
                self.store.change('runtime-error', lambda e: e.s.update(running=False,runtimeError='Motorn pausades efter ett internt fel. Läs systemloggen före återstart.'))
            last_active = started
            deadline = time.monotonic()+snap['autoPace']/1000

    def start(self):
        if self.monitor_only or not self.paper_enabled:
            self.monitor_thread=threading.Thread(target=self.monitor_loop,daemon=True)
            self.monitor_thread.start()
            return
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.quit.set()
        self.exchange.sessions.shutdown()
        if self.monitor_thread:
            self.monitor_thread.join(45)
            if self.monitor_thread.is_alive():
                raise RuntimeError('Monitor did not stop; database remains open until process exit')
        if self.thread:
            self.thread.join(45)
            if self.thread.is_alive():
                raise RuntimeError("Market worker did not stop; database remains open until process exit")


def make_server(runtime, port=8000):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

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

        def log_message(self, fmt, *args):
            logging.info('HTTP response', extra={'status': str(args[1]) if len(args) > 1 else 'unknown'})

        def do_GET(self):
            if not self.host_allowed():
                self.send(403, dict(error='Invalid Host'))
                return
            path = urlsplit(self.path).path
            if path == '/api/state':
                self.send(200, runtime.store.snapshot())
            elif path == '/api/exchange':
                self.send(200,runtime.exchange.status())
            elif path == '/api/trading-session':
                self.send(200,runtime.exchange.sessions.status())
            elif path == '/api/health':
                snap = runtime.store.snapshot()
                self.send(200, dict(status='ok', paperOnly=True, scope='paper-engine',liveEnabled=runtime.exchange.enabled, tick=snap['state']['tick'], marketData=snap['state']['marketData']))
            elif path == '/api/export/trades.csv':
                self.send(200, trades(runtime.store.snapshot()['state']), 'text/csv; charset=utf-8')
            elif path == '/api/export/capital.csv':
                self.send(200, capital(runtime.store.snapshot()['state']), 'text/csv; charset=utf-8')
            elif path == '/api/export/journal.jsonl':
                with runtime.store.lock:
                    entries = runtime.store.db.execute('SELECT seq,at,kind,payload FROM journal ORDER BY seq').fetchall()
                data = ''.join(json.dumps(dict(seq=seq, at=at, kind=kind, data=json.loads(payload)), ensure_ascii=False)+'\n' for seq, at, kind, payload in entries)
                self.send(200, data.encode(), 'application/x-ndjson; charset=utf-8')
            elif path == '/':
                self.send(200, (ROOT/'index (2).html').read_bytes(), 'text/html; charset=utf-8')
            elif path == '/dashboard.css':
                self.send(200,(ROOT/'dashboard.css').read_bytes(),'text/css; charset=utf-8')
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
                if path.startswith('/api/trading-session/'):
                    self.send(200,runtime.exchange.sessions.run(path.rsplit('/',1)[-1],data,key,self.headers.get('X-Jarvis-Approval')))
                    return
                if path.startswith('/api/exchange/'):
                    self.send(200,runtime.exchange.run(path.rsplit('/',1)[-1],data,key,self.headers.get('X-Jarvis-Approval')))
                    return
                if not path.startswith('/api/commands/'):
                    self.send(404, dict(error='Not found'))
                    return
                self.send(200, runtime.command(path.rsplit('/', 1)[-1], data, key, self.headers.get('X-Jarvis-Approval')))
            except ExchangeUnknown as exc:
                self.send(503,dict(error=str(exc),requiresReconciliation=True))
            except ExchangeError as exc:
                self.send(400,dict(error=str(exc)))
            except (ValueError, TypeError) as exc:
                self.send(400, dict(error=str(exc)))
            except MarketError:
                self.send(503, dict(error='Kraken/ECB unavailable; no tick executed'))
            except Exception:
                logging.exception('Request failed')
                self.send(500, dict(error='Internt fel. Uppdatera status och stäm av eventuell livebiljett innan fler försök.'))

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def main():
    parser = argparse.ArgumentParser(description='JARVIS v0.6 paper engine and owner-approved Kraken adapter')
    parser.add_argument('--db', default=str(ROOT/'.jarvis/paper.sqlite3'))
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    configure(Path(args.db).parent/'system.jsonl')
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
