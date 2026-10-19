"""Local, dependency-free demo server. Run: python3 server.py"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import RLock
from urllib.parse import urlparse, parse_qs
import argparse
import csv
import io
import json
import mimetypes

from engine import Detector
from datasets import catalog, load_dataset

ROOT = Path(__file__).parent
detector = Detector()
lock = RLock()
DEFAULT_REPLAY = {'dataset': None, 'sample': None, 'window_seconds': None}


def replay_payload(dataset, limit=None, process=False, window_seconds=None):
    events, meta = load_dataset(dataset, limit=None)
    window = int(window_seconds or meta.get('window_seconds') or 60)
    if limit:
        events = events[:int(limit)]
    detector.reset()
    detector.window_seconds = window
    detector.WINDOW = window
    detector.dgim_window = int(window)
    detector.DGIM_WINDOW = detector.dgim_window
    if events:
        detector.dataset_start = float(events[0].get('ts') or 0)
    detector.dataset = {'name': dataset, 'source': events[0].get('source', dataset),
                        'window_seconds': window, 'rows': len(events)}
    if process:
        for event in events:
            detector.process(event)
        return {'dataset': dataset, 'meta': meta, 'processed': len(events),
                'window_seconds': window, 'state': detector.state()}
    return {'dataset': dataset, 'meta': meta, 'count': len(events),
            'window_seconds': window, 'events': events,
            'note': 'Events are a time-contiguous prefix. Post them to /api/event, '
                    'or call /api/replay with process=true.'}


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
                confidence = (qs.get('min_confidence') or [None])[0]
                if support is not None:
                    try:
                        detector.apriori_support = float(support)
                    except (TypeError, ValueError):
                        pass
                if confidence is not None:
                    try:
                        detector.apriori_confidence = float(confidence)
                    except (TypeError, ValueError):
                        pass
                light = (qs.get('light') or ['0'])[0] in ('1', 'true')
                return self.reply(detector.state(dgim_entity=entity, light=light))
            if route == '/api/datasets':
                return self.reply({'datasets': catalog(), 'default': DEFAULT_REPLAY,
                                   'limits': {'max_events': detector.LIMIT}})
            if route == '/api/entity':
                qs2 = parse_qs(parsed.query)
                kind = (qs2.get('kind') or ['device'])[0]
                value = (qs2.get('value') or [''])[0]
                if kind not in ('device', 'card', 'account') or not value:
                    return self.reply({'error': 'Provide kind=device|card|account and a value.'}, 400)
                matches = [e for e in detector.events if e.get(kind) == value][-200:]
                return self.reply({'kind': kind, 'value': value, 'count': len(matches),
                                   'events': matches, 'dataset_start': detector.dataset_start})
            if route == '/api/dgim_demo':
                from algorithms.dgim import long_stream_demo
                qs2 = parse_qs(parsed.query)
                try:
                    n = int((qs2.get('n') or ['1000'])[0])
                    window = int((qs2.get('window') or ['100'])[0])
                except (TypeError, ValueError):
                    n, window = 1000, 100
                n = max(100, min(5000, n))
                window = max(10, min(1000, window))
                pattern = (qs2.get('pattern') or ['random'])[0]
                if pattern not in ('random', 'ones', 'worst'):
                    pattern = 'random'
                return self.reply(long_stream_demo(n=n, window=window, pattern=pattern))
            if route == '/api/export':
                out = io.StringIO()
                fields = ['id', 'ts', 'time', 'type', 'account', 'device', 'ip', 'card',
                          'amount', 'email_domain', 'disposable', 'forwarding_email',
                          'country_mismatch', 'failed', 'score', 'action', 'risk_level',
                          'label', 'review_threshold', 'block_threshold',
                          'bloom_device', 'bloom_card', 'bloom_inserted',
                          'dgim_bit', 'dgim_est', 'dgim_exact',
                          'shadow_base', 'shadow_flipped',
                          'baseline_score', 'baseline_action',
                          'stripe_risk_level', 'stripe_risk_score', 'source']
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
                    bl = e.get('baseline') or {}
                    row['baseline_score'] = bl.get('score', '')
                    row['baseline_action'] = bl.get('action', '')
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
            if not 0 <= length <= 2_000_000:
                raise ValueError('Request is too large.')
            body = json.loads(self.rfile.read(length) or b'{}')
            if not isinstance(body, dict):
                raise ValueError('Expected a JSON object.')
            with lock:
                if self.path == '/api/event':
                    detector.process(body)
                    return self.reply(detector.state(light=bool(body.get('light'))))
                if self.path == '/api/scenario':
                    return self.reply({'events': detector.scenario(body.get('name'))})
                if self.path == '/api/scenario_run':
                    detector.reset()
                    detector.dataset = {'name': 'synthetic', 'source': 'synthetic',
                                        'window_seconds': detector.window_seconds}
                    rows = detector.scenario(body.get('name'))
                    for row in rows:
                        detector.process(row)
                    return self.reply(detector.state())
                if self.path == '/api/reset':
                    detector.reset()
                elif self.path == '/api/advance':
                    detector.advance(body.get('seconds', 61))
                elif self.path == '/api/thresholds':
                    review, block = body.get('review'), body.get('block')
                    if any(isinstance(x, bool) or not isinstance(x, int) for x in [review, block]) or not 1 <= review < block <= 100:
                        raise ValueError('Use 1 <= review threshold < block threshold <= 100.')
                    detector.review_threshold, detector.block_threshold = review, block
                    detector.risk_cutoffs = {'elevated': review, 'highest': block}
                elif self.path == '/api/config':
                    self._config(body)
                elif self.path == '/api/replay':
                    dataset = body.get('dataset')
                    if not dataset:
                        dataset = DEFAULT_REPLAY.get('dataset')
                    if not dataset:
                        raise ValueError('Provide dataset (synthetic, ieee_cis, ieee_cis_fixture, stripe_fixture).')
                    limit = body.get('limit')
                    if limit is None and dataset == DEFAULT_REPLAY.get('dataset'):
                        limit = DEFAULT_REPLAY.get('sample')
                    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
                        raise ValueError('limit must be a positive integer.')
                    window = body.get('window_seconds')
                    if window is not None and (isinstance(window, bool) or not isinstance(window, int) or window < 1):
                        raise ValueError('window_seconds must be a positive integer.')
                    return self.reply(replay_payload(dataset, limit=limit,
                                                     process=bool(body.get('process')),
                                                     window_seconds=window))
                elif self.path == '/api/stripe_webhook':
                    from adapters.stripe_events import map_events
                    raw = body.get('events') if 'events' in body else body
                    if isinstance(raw, dict) and raw.get('object') == 'event':
                        raw = [raw]
                    if not isinstance(raw, list):
                        raise ValueError('Provide a Stripe event, an events list, or {events: [...]}.')
                    events = map_events(raw)
                    if events and detector.events and events[0]['ts'] < detector.clock:
                        detector.reset()
                    for event in events:
                        detector.process(event)
                    state = detector.state()
                    state['webhook'] = {'received': len(raw), 'mapped': len(events)}
                    return self.reply(state)
                elif self.path == '/api/bloom/config':
                    size = body.get('size', 512)
                    hashes = body.get('num_hashes', 3)
                    rotate = body.get('rotate_every', 0)
                    if any(isinstance(x, bool) or not isinstance(x, int) for x in [size, hashes, rotate]):
                        raise ValueError('size, num_hashes and rotate_every must be integers.')
                    if not 16 <= size <= 100000 or not 1 <= hashes <= 10 or not 0 <= rotate <= 1000000:
                        raise ValueError('Use 16<=size<=100000, 1<=hashes<=10, 0<=rotate_every<=1000000.')
                    detector.bloom_size, detector.bloom_hashes = size, hashes
                    detector.bloom_rotate_every = rotate
                    detector.bloom_device = detector._make_bloom()
                    detector.bloom_card = detector._make_bloom()
                elif self.path == '/api/bloom/find_false_positive':
                    from algorithms.bloom_filter import find_false_positive
                    size = int(body.get('size', 64))
                    hashes = int(body.get('num_hashes', 3))
                    size = max(16, min(4096, size))
                    hashes = max(1, min(10, hashes))
                    return self.reply(find_false_positive(size=size, num_hashes=hashes))
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
                                           'positions': bf.positions(key.strip()),
                                           'size': bf.size,
                                           'num_hashes': bf.num_hashes}
                    return self.reply(state)
                elif self.path == '/api/apriori':
                    support = body.get('min_support')
                    confidence = body.get('min_confidence')
                    if support is not None:
                        if isinstance(support, bool) or not isinstance(support, (int, float)) or not 0.01 <= support <= 1.0:
                            raise ValueError('min_support must be between 0.01 and 1.0.')
                        detector.apriori_support = float(support)
                    if confidence is not None:
                        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1.0:
                            raise ValueError('min_confidence must be between 0 and 1.')
                        detector.apriori_confidence = float(confidence)
                    state = detector.state()
                    state['apriori_class'] = detector.apriori_class_comparison()
                    return self.reply(state)
                else:
                    return self.reply({'error': 'Not found'}, 404)
                # Honor optional entity selection for DGIM panel.
                entity = body.get('dgim_entity') if isinstance(body.get('dgim_entity'), str) else None
                self.reply(detector.state(dgim_entity=entity or None))
        except (ValueError, TypeError, FileNotFoundError) as exc:
            self.reply({'error': str(exc)}, 400)
        except Exception as exc:  # keep the demo server alive on unexpected input
            self.reply({'error': f'Unexpected error: {exc}'}, 500)

    def _config(self, body):
        if 'window_seconds' in body:
            value = body['window_seconds']
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 86400 * 30:
                raise ValueError('window_seconds must be an int in 1..2592000.')
            detector.window_seconds = value
            detector.WINDOW = value
        if 'dgim_window' in body:
            value = body['dgim_window']
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 10000:
                raise ValueError('dgim_window must be an int in 1..10000.')
            detector.dgim_window = value
            detector.DGIM_WINDOW = value
            for stream in list(detector.dgim_device_streams.values()) + list(detector.dgim_card_streams.values()):
                stream.window_size = value
        if 'dgim_max_streams' in body:
            value = body['dgim_max_streams']
            if isinstance(value, bool) or not isinstance(value, int) or not 16 <= value <= 10000:
                raise ValueError('dgim_max_streams must be an int in 16..10000.')
            detector.dgim_max_streams = value
        if 'min_support' in body:
            ms = body['min_support']
            if isinstance(ms, bool) or not isinstance(ms, (int, float)) or not 0.01 <= ms <= 1.0:
                raise ValueError('min_support must be between 0.01 and 1.0.')
            detector.apriori_support = float(ms)
        if 'min_confidence' in body:
            mc = body['min_confidence']
            if isinstance(mc, bool) or not isinstance(mc, (int, float)) or not 0 <= mc <= 1.0:
                raise ValueError('min_confidence must be between 0 and 1.')
            detector.apriori_confidence = float(mc)
        if 'dgim_threshold' in body:
            dt = body['dgim_threshold']
            if isinstance(dt, bool) or not isinstance(dt, int) or not 1 <= dt <= 10000:
                raise ValueError('dgim_threshold must be a positive int.')
            detector.DGIM_THRESHOLD = dt
        if 'risk_cutoffs' in body:
            cut = body['risk_cutoffs']
            if not isinstance(cut, dict):
                raise ValueError('risk_cutoffs must be an object {elevated, highest}.')
            elevated, highest = cut.get('elevated'), cut.get('highest')
            if any(isinstance(x, bool) or not isinstance(x, int) for x in [elevated, highest]) or not 0 <= elevated < highest <= 100:
                raise ValueError('Use 0 <= elevated < highest <= 100.')
            detector.risk_cutoffs = {'elevated': elevated, 'highest': highest}

    def log_message(self, fmt, *args):
        pass


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--dataset', default=None,
                        help='Preselect a dataset: synthetic, ieee_cis, ieee_cis_fixture, stripe_fixture')
    parser.add_argument('--sample', type=int, default=None,
                        help='Time-contiguous replay prefix size (used with --dataset)')
    parser.add_argument('--window-seconds', type=int, default=None,
                        help='Exact event-time window; defaults to 60 synthetic / 3600 IEEE-CIS')
    args = parser.parse_args()
    if args.dataset:
        DEFAULT_REPLAY.update(dataset=args.dataset, sample=args.sample,
                              window_seconds=args.window_seconds)
        try:
            meta = {d['name']: d for d in catalog()}[args.dataset]
            detector.dataset = {'name': args.dataset, 'source': args.dataset,
                                'window_seconds': args.window_seconds or meta['window_seconds']}
            detector.window_seconds = detector.WINDOW = int(args.window_seconds or meta['window_seconds'])
        except KeyError:
            print(f'Unknown --dataset {args.dataset}', flush=True)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'Radar Lab is running at http://127.0.0.1:{args.port}', flush=True)
    print('Press Ctrl+C to stop. All data stays in memory on this computer.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
