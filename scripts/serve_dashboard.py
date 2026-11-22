"""Run the dashboard: python scripts/serve_dashboard.py [--provider public]."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import threading
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from live.market import make_provider
from live.service import LiveService, make_server


def main():
    parser = argparse.ArgumentParser(description='Live strategy dashboard (local, no order submission)')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--provider', choices=('auto', 'public', 'efinance', 'longbridge'), default='auto')
    parser.add_argument('--quote-interval', type=int, default=30)
    parser.add_argument('--history-interval', type=int, default=900)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    try:
        provider = make_provider(args.provider)
    except Exception as error:
        parser.exit(1, f'行情源初始化失败 ({type(error).__name__})，请检查 SDK、环境变量及网络。\n')
    service = LiveService(provider, quote_interval=args.quote_interval, history_interval=args.history_interval)
    try:
        server = make_server(service, port=args.port)
    except OSError:
        parser.exit(1, f'端口 {args.port} 不可用，请选择 --port 其他端口。\n')
    service.start()
    url = f'http://127.0.0.1:{server.server_port}'
    print(f'QUANTLAB live dashboard: {url} | provider: {provider.name}', flush=True)
    if not args.no_browser:
        threading.Timer(.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=.5)
    except KeyboardInterrupt:
        pass
    finally:
        service.stop()
        server.server_close()


if __name__ == '__main__':
    main()
