"""Single-flight polling, atomic last-good cache, and a local read-only dashboard."""
from __future__ import annotations

from copy import deepcopy
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit
import pandas as pd

from live.engine import recompute, monitor
from live.market import MarketClock, RISK_SYMBOLS, SYMBOLS, utc_now

ROOT = Path(__file__).resolve().parents[1]


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


def load_research(root=ROOT):
    return json.loads((root/'research_outputs'/'V22_dashboard_snapshot.json').read_text(encoding='utf-8'))


def render_dashboard(root=ROOT):
    html = (root/'web'/'template.html').read_text(encoding='utf-8')
    payload = json_bytes(load_research(root)).decode('utf-8').replace('</', '<\\/')
    return html.replace('/* V21_STYLES */', (root/'web'/'v21.css').read_text(encoding='utf-8')).replace(
        '/* V21_CODE */', (root/'web'/'v21.js').read_text(encoding='utf-8')).replace('V21_DATA_JSON', payload).encode('utf-8')


class LiveService:
    def __init__(self, provider, *, root=ROOT, quote_interval=30, history_interval=900, clock=None):
        self.provider, self.root = provider, root
        self.quote_interval, self.history_interval = max(15, quote_interval), max(300, history_interval)
        self.clock = clock or MarketClock()
        self.lock, self.flight = threading.Lock(), threading.Lock()
        self.wake, self.stopped = threading.Event(), threading.Event()
        self.worker = None
        self.research = load_research(root)
        self.cache = root/'market_data'/'live_cache'/'last_good.json'
        self.daily, self.tape = {}, {}
        self.last_history = 0
        self.force_history = False
        self.live = {'quotes': {}, 'models': {}, 'signals': {}, 'intraday': {}, 'strategy_status': {},
                     'quality': [], 'frozen_signals': [], 'liquidity': []}
        self.status = {'state': 'starting', 'provider': provider.name, 'refreshing': False,
                       'last_attempt': None, 'last_success': None, 'errors': {}, 'cache_restored': False,
                       'quote_interval_seconds': self.quote_interval, 'history_interval_seconds': self.history_interval}
        try:
            cached = json.loads(self.cache.read_text(encoding='utf-8'))
            if cached.get('schema') == 1 and isinstance(cached.get('live'), dict):
                self.live.update(cached['live'])
                self.status.update(state='cached', cache_restored=True, last_success=cached.get('saved_at'))
        except (OSError, ValueError, TypeError):
            pass

    def payload(self):
        now = utc_now()
        with self.lock:
            live, status = deepcopy(self.live), deepcopy(self.status)
        market_status = {m: self.clock.status(m, now) for m in ('CN', 'US')}
        for quote in live['quotes'].values():
            stamp = pd.Timestamp(quote['timestamp'])
            quote['age_seconds'] = max(0, int((now-stamp).total_seconds()))
            quote['stale'] = quote['age_seconds'] > 180
            quote['refresh_failed'] = any(k in status['errors'] for k in ('quotes', 'quote:'+quote.get('symbol', '')))
            quote['display_state'] = 'stale' if quote['stale'] else 'recent'
            if market_status[quote['market']]['state'] == 'closed':
                quote['display_state'] = 'last_close'
        # A cache read or an API poll never refreshes the source's data timestamp.
        for market, value in live.get('strategy_status', {}).items():
            if status['cache_restored'] or (status['errors'] and status['state'] in ('degraded', 'offline')):
                value['connection_degraded'] = True
        for symbol, observation in live.get('intraday', {}).items():
            age = (now-pd.Timestamp(observation['last_bar_end'])).total_seconds()
            if age > 2400:
                observation['data_state'] = 'last_session'
            observation['refresh_failed'] = any(k in status['errors'] for k in ('bars', 'risk:'+symbol, 'bars:'+symbol))
            if observation['refresh_failed']:
                observation['data_state'] = 'last_session'
        return {'schema': 1, 'server_time': now.isoformat(), 'status': status, 'market_status': market_status,
                'research': self.research, 'live': live}

    def refresh_once(self, *, history=False):
        if not self.flight.acquire(blocking=False):
            return False
        errors, any_success = {}, False
        with self.lock:
            self.status.update(refreshing=True, last_attempt=utc_now().isoformat())
        try:
            try:
                quotes, failures = self.provider.quotes(utc_now())
                errors.update({'quote:'+k: v for k, v in failures.items()})
                with self.lock:
                    self.live['quotes'].update(quotes)
                any_success |= bool(quotes)
            except Exception as error:
                errors['quotes'] = type(error).__name__ + '：行情请求失败，请检查网络及行情权限'
            if history or time.monotonic()-self.last_history >= self.history_interval:
                # Record attempt time too: an unavailable provider must not be hammered.
                self.last_history = time.monotonic()
                try:
                    daily, failures = self.provider.daily()
                    errors.update({'daily:'+k: v for k, v in failures.items()})
                    # Work only with this coherent refresh, never fill missing assets
                    # using previous downloads with a different adjustment basis.
                    self.daily = daily
                    self.tape = {}
                    try:
                        self.tape = self.provider.tape()
                    except Exception as error:
                        errors['tape'] = type(error).__name__ + '：指数成交额不可用，A股新信号暂停'
                    computed = recompute(self.daily, self.tape, self.clock, utc_now(), self.provider.name)
                    with self.lock:
                        # Keep last successful model per market but label failed refresh.
                        self.live['models'].update(computed['models'])
                        self.live['signals'].update(computed['signals'])
                        self.live['strategy_status'] = computed['strategy_status']
                        for key in ('quality', 'frozen_signals', 'liquidity', 'computed_at', 'external_backtests'):
                            self.live[key] = computed[key]
                    any_success |= bool(computed['models'])
                except Exception as error:
                    errors['history'] = type(error).__name__ + '：策略数据更新失败，保留上次计算'
            try:
                bars, failures = self.provider.bars()
                errors.update({'bars:'+k: v for k, v in failures.items()})
                for symbol in RISK_SYMBOLS:
                    try:
                        observation = monitor(symbol, bars[symbol], self.daily[symbol]['raw'], self.clock, utc_now())
                        with self.lock:
                            self.live['intraday'][symbol] = observation
                        any_success = True
                    except (KeyError, ValueError, IndexError) as error:
                        errors['risk:'+symbol] = str(error)
            except Exception as error:
                errors['bars'] = type(error).__name__ + '：30分钟行情更新失败'
            with self.lock:
                self.status.update(state=('degraded' if errors else 'connected') if any_success else 'offline',
                                   errors=errors, cache_restored=False)
                if any_success:
                    self.status['last_success'] = utc_now().isoformat()
                    # Validate JSON before replacing the last-good cache.
                    content = json_bytes({'schema': 1, 'saved_at': self.status['last_success'], 'live': self.live})
                    self.cache.parent.mkdir(parents=True, exist_ok=True)
                    temp = self.cache.with_suffix('.tmp')
                    temp.write_bytes(content)
                    os.replace(temp, self.cache)
            return True
        except Exception as error:
            with self.lock:
                self.status.update(state='degraded', errors={**errors, 'refresh': type(error).__name__ + '：更新未完成'})
            return False
        finally:
            with self.lock:
                self.status['refreshing'] = False
            self.flight.release()

    def start(self):
        def loop():
            while not self.stopped.is_set():
                with self.lock:
                    force, self.force_history = self.force_history, False
                self.refresh_once(history=force)
                self.wake.wait(self.quote_interval)
                self.wake.clear()
        self.worker = threading.Thread(target=loop, name='market-refresh', daemon=True)
        self.worker.start()

    def request_refresh(self):
        # Requests coalesce; no additional threads or concurrent provider calls.
        with self.lock:
            if self.status['refreshing']:
                return False
            self.force_history = time.monotonic()-self.last_history >= 30
        self.wake.set()
        return True

    def stop(self):
        self.stopped.set()
        self.wake.set()


def make_server(service, host='127.0.0.1', port=8765):
    if host not in ('127.0.0.1', 'localhost'):
        raise ValueError('dashboard service binds to localhost only')

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def send_content(self, code, body, kind='application/json; charset=utf-8'):
            if 'gzip' in self.headers.get('Accept-Encoding', '') and len(body) > 1024:
                body = gzip.compress(body)
                compressed = True
            else:
                compressed = False
            self.send_response(code)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'same-origin')
            self.send_header('Vary', 'Accept-Encoding')
            if compressed:
                self.send_header('Content-Encoding', 'gzip')
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def local_request(self):
            allowed = {f'{h}:{self.server.server_port}' for h in ('localhost', '127.0.0.1')}
            return self.headers.get('Host', '') in allowed

        def do_GET(self):
            if not self.local_request():
                return self.send_content(403, b'{"error":"invalid host"}')
            path = urlsplit(self.path).path
            if path in ('/', '/index.html'):
                return self.send_content(200, render_dashboard(service.root), 'text/html; charset=utf-8')
            if path in ('/api/dashboard', '/api/status'):
                payload = service.payload()
                if path == '/api/status':
                    payload = {k: payload[k] for k in ('server_time', 'status', 'market_status')}
                return self.send_content(200, json_bytes(payload))
            self.send_content(404, b'{"error":"not found"}')

        def do_POST(self):
            if not self.local_request():
                return self.send_content(403, b'{"error":"invalid host"}')
            origin = self.headers.get('Origin')
            if (origin and origin != 'http://'+self.headers.get('Host', '')) or self.headers.get('Sec-Fetch-Site') == 'cross-site':
                return self.send_content(403, b'{"error":"same origin required"}')
            if urlsplit(self.path).path != '/api/refresh':
                return self.send_content(404, b'{"error":"not found"}')
            if self.headers.get('Content-Length', '0') != '0' or self.headers.get('Transfer-Encoding'):
                return self.send_content(400, b'{"error":"request body not accepted"}')
            accepted = service.request_refresh()
            self.send_content(202, json_bytes({'accepted': accepted, 'message': '刷新已排队' if accepted else '刷新正在进行'}))

        def log_message(self, fmt, *args):
            # Only paths/status; never credentials or upstream responses.
            print(fmt % args, flush=True)

    return ThreadingHTTPServer((host, port), Handler)
