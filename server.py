"""Local, dependency-free demo server. Run: python3 server.py"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import RLock
import csv
import io
import json
import mimetypes
import argparse
from engine import Detector

ROOT = Path(__file__).parent
detector = Detector()
lock = RLock()


class Handler(BaseHTTPRequestHandler):
    def reply(self, body, status=200, content_type='application/json'):
        if not isinstance(body, bytes):
            body = json.dumps(body, allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        route = self.path.split('?')[0]
        with lock:
            if route == '/api/state':
                return self.reply(detector.state())
            if route == '/api/export':
                out = io.StringIO()
                fields = ['id', 'time', 'type', 'account', 'device', 'ip', 'card', 'amount', 'score', 'action', 'label', 'review_threshold', 'block_threshold']
                writer = csv.DictWriter(out, fields, extrasaction='ignore')
                writer.writeheader()
                for e in detector.events:
                    # Prevent spreadsheet formula execution in user-supplied IDs.
                    writer.writerow({k: "'"+v if isinstance(v, str) and v.startswith(('=', '+', '-', '@', '\t', '\r')) else v for k,v in e.items()})
                return self.reply(out.getvalue().encode(), content_type='text/csv; charset=utf-8')
        allowed = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}
        if route not in allowed:
            return self.reply({'error': 'Not found'}, 404)
        path = ROOT/'static'/allowed[route]
        self.reply(path.read_bytes(), content_type=mimetypes.guess_type(path.name)[0] or 'text/plain')

    def do_POST(self):
        # All mutations must originate from this local UI or an explicit JSON client.
        origin = self.headers.get('Origin')
        if origin and origin != f"http://{self.headers.get('Host')}":
            return self.reply({'error': 'Cross-origin requests are not accepted.'}, 403)
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            return self.reply({'error': 'JSON required'}, 415)
        try:
            length = int(self.headers.get('Content-Length', 0))
            if not 0 <= length <= 16384:
                raise ValueError('Request is too large.')
            body = json.loads(self.rfile.read(length) or b'{}')
            if not isinstance(body, dict):
                raise ValueError('Expected a JSON object.')
            with lock:
                if self.path == '/api/event':
                    detector.process(body)
                elif self.path == '/api/scenario':
                    return self.reply({'events': detector.scenario(body.get('name'))})
                elif self.path == '/api/reset':
                    detector.reset()
                elif self.path == '/api/advance':
                    detector.advance(body.get('seconds', 61))
                elif self.path == '/api/thresholds':
                    review, block = body.get('review'), body.get('block')
                    if any(isinstance(x, bool) or not isinstance(x, int) for x in [review, block]) or not 1 <= review < block <= 100:
                        raise ValueError('Use 1 ≤ review threshold < block threshold ≤ 100.')
                    detector.review_threshold, detector.block_threshold = review, block
                else:
                    return self.reply({'error': 'Not found'}, 404)
                self.reply(detector.state())
        except (ValueError, TypeError) as exc:
            self.reply({'error': str(exc)}, 400)

    def log_message(self, fmt, *args):
        pass


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'Radar Lab is running at http://127.0.0.1:{args.port}', flush=True)
    print('Press Ctrl+C to stop. All data stays in memory on this computer.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
