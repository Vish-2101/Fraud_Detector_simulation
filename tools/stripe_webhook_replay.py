"""Replay saved Stripe-format events into the local webhook endpoint.

Standard library only.

Run:
  python tools/stripe_webhook_replay.py
  python tools/stripe_webhook_replay.py --file data/processed/stripe_events_live.json
  python tools/stripe_webhook_replay.py --dataset stripe_fixture
"""
import argparse
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_events(args):
    if args.dataset:
        from datasets import load_dataset
        events, meta = load_dataset(args.dataset)
        from adapters.stripe_events import map_events
        # Re-load raw Stripe JSON so we replay Stripe's shape, not mapped events.
        raw = json.loads(Path(meta['path']).read_text(encoding='utf-8'))
        return raw.get('events', raw) if isinstance(raw, dict) else raw
    path = Path(args.file)
    raw = json.loads(path.read_text(encoding='utf-8'))
    return raw.get('events', raw) if isinstance(raw, dict) else raw


def post(url, payload):
    data = json.dumps(payload).encode('utf-8')
    request = urllib.request.Request(url, data=data, method='POST',
                                     headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode('utf-8'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--file', default='fixtures/stripe_events_sample.json')
    parser.add_argument('--dataset', default=None,
                        help='Alternatively replay a catalog dataset (stripe_fixture).')
    parser.add_argument('--url', default='http://127.0.0.1:8765/api/stripe_webhook')
    parser.add_argument('--batch-size', type=int, default=200)
    args = parser.parse_args()
    events = load_events(args)
    if not isinstance(events, list):
        raise SystemExit('Expected a JSON list of Stripe events.')
    state = None
    for i in range(0, len(events), args.batch_size):
        state = post(args.url, {'events': events[i:i + args.batch_size]})
    print(f"posted {len(events)} Stripe events to {args.url}")
    if state:
        print(f"total={state.get('total')} counts={state.get('counts')} "
              f"webhook={state.get('webhook')}")


if __name__ == '__main__':
    main()
