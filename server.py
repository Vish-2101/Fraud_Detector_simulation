"""Local, dependency-free demo server. Run: python3 server.py"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import RLock
from urllib.parse import urlparse, parse_qs
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
        parsed = urlparse(self.path)
        route = parsed.path
        with lock:
            if route == '/api/state':
                qs = parse_qs(parsed.query)
                entity = (qs.get('entity') or [None])[0]
                support = (qs.get('min_support') or [None])[0]
                if support is not None:
                    try:
                        detector.apriori_support = float(support)
                    except (TypeError, ValueError):
                        pass
                return self.reply(detector.state(dgim_entity=entity))
            if route == '/api/dgim_demo':
                from algorithms.dgim import long_stream_demo
                qs2 = parse_qs(parsed.query)
                try:
                    n = int((qs2.get('n') or ['1000'])[0])
                except (TypeError, ValueError):
                    n = 1000
                n = max(100, min(5000, n))
                return self.reply(long_stream_demo(n=n))
            if route == '/api/export':
                out = io.StringIO()
                fields = ['id', 'time', 'type', 'account', 'device', 'ip', 'card', 'amount', 'score', 'action',
                          'label', 'review_threshold', 'block_threshold',
                          'bloom_device', 'bloom_card', 'bloom_inserted',
                          'dgim_bit', 'dgim_est', 'dgim_exact',
                          'shadow_base', 'shadow_flipped']
                writer = csv.DictWriter(out, fields, extrasaction='ignore')
                writer.writeheader()
                for e in detector.events:
                    row = dict(e)
                    b = e.get('bloom', {})
                    g = e.get('dgim', {})
                    row['bloom_device'] = b.get('device_result', '')
                    row['bloom_card'] = b.get('card_result', '')
                    row['bloom_inserted'] = b.get('inserted', False)
                    row['dgim_bit'] = g.get('bit', '')
                    row['dgim_est'] = g.get('device_est', '')
                    row['dgim_exact'] = g.get('device_exact', '')
                    sh = e.get('shadow', {})
                    row['shadow_base'] = sh.get('base_score', '')
                    row['shadow_flipped'] = sh.get('flipped', False)
                    writer.writerow({k: "'" + v if isinstance(v, str) and v.startswith(('=', '+', '-', '@', '\t', '\r')) else v for k, v in row.items()})
                return self.reply(out.getvalue().encode(), content_type='text/csv; charset=utf-8')
        allowed = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}
        if route not in allowed:
            return self.reply({'error': 'Not found'}, 404)
        path = ROOT / 'static' / allowed[route]
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
                        raise ValueError('Use 1 <= review threshold < block threshold <= 100.')
                    detector.review_threshold, detector.block_threshold = review, block
                elif self.path == '/api/config':
                    # Optional tuning: apriori min_support, DGIM threshold.
                    if 'min_support' in body:
                        ms = body['min_support']
                        if isinstance(ms, bool) or not isinstance(ms, (int, float)) or not 0.01 <= ms <= 1.0:
                            raise ValueError('min_support must be between 0.01 and 1.0.')
                        detector.apriori_support = float(ms)
                    if 'dgim_threshold' in body:
                        dt = body['dgim_threshold']
                        if isinstance(dt, bool) or not isinstance(dt, int) or not 1 <= dt <= 60:
                            raise ValueError('dgim_threshold must be an int in 1..60.')
                        detector.DGIM_THRESHOLD = dt
                    if 'dgim_entity' in body and isinstance(body['dgim_entity'], str) and body['dgim_entity'].strip():
                        return self.reply(detector.state(dgim_entity=body['dgim_entity'].strip()))
                elif self.path == '/api/inspect':
                    # Dry Bloom membership check: increments query stats only,
                    # never inserts. Demonstrates unseen vs seen identifiers.
                    kind, key = body.get('kind', 'device'), body.get('key', '')
                    if kind not in ('device', 'card') or not isinstance(key, str) or not key.strip():
                        raise ValueError('Provide kind=device|card and a non-empty key.')
                    bf = detector.bloom_device if kind == 'device' else detector.bloom_card
                    result = bf.check(key.strip())
                    state = detector.state()
                    state['inspection'] = {'kind': kind, 'key': key.strip(),
                                           'result': result,
                                           'positions': bf.positions(key.strip())}
                    return self.reply(state)
                else:
                    return self.reply({'error': 'Not found'}, 404)
                # Honor optional entity selection for DGIM panel.
                entity = body.get('dgim_entity') if isinstance(body.get('dgim_entity'), str) else None
                self.reply(detector.state(dgim_entity=entity or None))
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
