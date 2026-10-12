"""Educational stream detector. Synthetic identifiers only; no Stripe integration."""
from collections import Counter, defaultdict, deque
from itertools import combinations
import hashlib
import math
import random
import time


class CountMinSketch:
    """Insert-only frequency estimate. Never used for enforcement."""
    def __init__(self, width=64, depth=4):
        self.width, self.depth = width, depth
        self.rows = [[0] * width for _ in range(depth)]

    def columns(self, key):
        return [int.from_bytes(hashlib.blake2b(f'{i}:{key}'.encode(), digest_size=8).digest(), 'big') % self.width for i in range(self.depth)]

    def add(self, key):
        for row, col in zip(self.rows, self.columns(key)):
            row[col] += 1

    def estimate(self, key):
        return min(row[col] for row, col in zip(self.rows, self.columns(key)))


class Detector:
    WINDOW = 60
    LIMIT = 2000
    TYPES = {'payment', 'signup', 'trial', 'usage', 'cancel'}

    def __init__(self):
        self.reset()

    def reset(self):
        self.clock = 0
        self.events = []
        self.window = deque()
        self.device_accounts = defaultdict(Counter)
        self.ip_accounts = defaultdict(Counter)
        self.card_payments = Counter()
        self.device_trials = Counter()
        self.device_cancels = Counter()
        self.sketch = CountMinSketch()
        self.exact = Counter()
        self.review_threshold = 35
        self.block_threshold = 65
        self.rng = random.Random(42)
        self.run_id = 0

    def _update(self, e, delta):
        for field, index in [('device', self.device_accounts), ('ip', self.ip_accounts)]:
            key = e[field]
            index[key][e['account']] += delta
            if index[key][e['account']] <= 0:
                del index[key][e['account']]
            if not index[key]:
                del index[key]
        for applies, index, key in [
            (e['type'] == 'payment', self.card_payments, e['card']),
            (e['type'] == 'trial', self.device_trials, e['device']),
            (e['type'] == 'cancel', self.device_cancels, e['device'])]:
            if applies:
                index[key] += delta
                if index[key] <= 0:
                    del index[key]

    def advance(self, seconds):
        if not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or not 0 <= seconds <= 3600:
            raise ValueError('Time advance must be between 0 and 3600 seconds.')
        self.clock += seconds
        # Window is (now - 60, now], including the current event.
        while self.window and self.window[0]['time'] <= self.clock - self.WINDOW:
            self._update(self.window.popleft(), -1)

    def validate(self, raw):
        e = dict(raw)
        if e.get('type') not in self.TYPES:
            raise ValueError('Choose a valid event type.')
        for key in ['account', 'device', 'ip', 'card']:
            value = e.get(key, 'card-none' if key == 'card' else '')
            if not isinstance(value, str) or not value.strip() or len(value) > 80:
                raise ValueError(f'{key} must contain 1–80 characters.')
            e[key] = value.strip()
        for key in ['amount', 'usage']:
            value = e.get(key, 0)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1000000:
                raise ValueError(f'{key} must be a number between 0 and 1,000,000.')
            e[key] = value
        for key in ['disposable', 'country_mismatch', 'failed']:
            if not isinstance(e.get(key, False), bool):
                raise ValueError(f'{key} must be true or false.')
            e[key] = e.get(key, False)
        if e.get('label') not in ('legitimate', 'abuse', 'unknown', None):
            raise ValueError('Invalid ground-truth label.')
        e['label'] = e.get('label') or 'unknown'
        return e

    def process(self, raw):
        if len(self.events) >= self.LIMIT:
            raise ValueError('Demo limit reached (2,000 events). Reset to start a new session.')
        e = self.validate(raw)
        started = time.perf_counter()
        self.advance(raw.get('step', 1))
        e.update(id=f'evt_{len(self.events)+1:04}', time=self.clock)
        self.window.append(e)
        self._update(e, 1)
        self.exact[e['device']] += 1
        self.sketch.add(e['device'])
        features = {
            'device_accounts': len(self.device_accounts[e['device']]),
            'ip_accounts': len(self.ip_accounts[e['ip']]),
            'card_attempts': self.card_payments[e['card']],
            'device_trials': self.device_trials[e['device']],
            'recent_cancels': self.device_cancels[e['device']],
        }
        reasons, items = [], []
        def add(condition, points, title, item):
            if condition:
                reasons.append({'points': points, 'reason': title})
                items.append(item)
        payment = e['type'] == 'payment'
        lifecycle = e['type'] in {'signup', 'trial', 'usage'}
        add(payment and features['card_attempts'] >= 4, 45, 'At least 4 attempts on this card in 60s', 'card_velocity')
        add(payment and e['failed'], 15, 'Simulated issuer failure signal', 'failed_payment')
        add(payment and e['amount'] >= 1000, 20, 'Payment amount at least $1,000', 'large_amount')
        add(payment and e['country_mismatch'], 20, 'Billing and device country differ', 'country_mismatch')
        add(lifecycle and features['device_accounts'] >= 3, 30, 'At least 3 accounts on this device in 60s', 'device_reuse')
        add(lifecycle and features['device_trials'] >= 2, 30, 'At least 2 trial starts on this device in 60s', 'repeat_trial')
        add(lifecycle and e['disposable'], 15, 'Synthetic temporary-email indicator', 'temporary_email')
        add(lifecycle and features['ip_accounts'] >= 4, 10, 'At least 4 accounts share this IP in 60s', 'shared_ip')
        add(e['type'] == 'usage' and e['usage'] >= 80, 20, 'At least 80% of trial credits consumed', 'heavy_usage')
        add(e['type'] == 'trial' and features['recent_cancels'] > 0, 20, 'Trial follows a cancellation on this device', 'trial_after_cancel')
        e.update(features=features, reasons=reasons, items=items, score=min(100, sum(r['points'] for r in reasons)))
        e.update(action=self.action(e['score']), review_threshold=self.review_threshold, block_threshold=self.block_threshold)
        e['latency_ms'] = round((time.perf_counter() - started) * 1000, 3)
        self.events.append(e)
        return e

    def action(self, score):
        return 'block' if score >= self.block_threshold else 'review' if score >= self.review_threshold else 'allow'

    def metrics(self):
        # Replay thresholds over stored scores; truth is never a scoring input.
        cm = dict(tp=0, fp=0, tn=0, fn=0)
        for e in self.events:
            if e['label'] == 'unknown':
                continue
            positive = self.action(e['score']) != 'allow'
            truth = e['label'] == 'abuse'
            cm['tp' if positive and truth else 'fp' if positive else 'fn' if truth else 'tn'] += 1
        def ratio(a, b):
            return round(a/b, 4) if b else None
        return dict(**cm, precision=ratio(cm['tp'], cm['tp']+cm['fp']), recall=ratio(cm['tp'], cm['tp']+cm['fn']), false_positive_rate=ratio(cm['fp'], cm['fp']+cm['tn']), labeled=sum(cm.values()))

    def apriori(self, min_support=0.12):
        """Level-wise Apriori with subset pruning; at most 3-item sets."""
        baskets = [set(e['items']) for e in self.events]
        n = len(baskets)
        if not n:
            return []
        minimum = max(1, math.ceil(n * min_support))
        level = {frozenset([item]) for basket in baskets for item in basket}
        results = []
        for size in range(1, 4):
            frequent = set()
            for candidate in sorted(level, key=lambda x: sorted(x)):
                count = sum(candidate <= b for b in baskets)
                if count >= minimum:
                    frequent.add(candidate)
                    results.append({'items': sorted(candidate), 'count': count, 'support': round(count/n, 4)})
            level = {a | b for a in frequent for b in frequent if len(a | b) == size+1 and all(frozenset(s) in frequent for s in combinations(a | b, size))}
        return sorted(results, key=lambda p: (-len(p['items']), -p['count'], p['items']))[:16]

    def state(self):
        counts = Counter(e['action'] for e in self.events)
        series = defaultdict(lambda: {'total': 0, 'flagged': 0})
        for e in self.events:
            bucket = int(e['time'] // 10) * 10
            series[bucket]['total'] += 1
            series[bucket]['flagged'] += e['action'] != 'allow'
        devices = self.exact.most_common(12)
        return dict(clock=self.clock, total=len(self.events), active=len(self.window), counts=dict(counts),
                    thresholds={'review': self.review_threshold, 'block': self.block_threshold},
                    metrics=self.metrics(), events=list(reversed(self.events[-150:])),
                    series=[dict(time=t, **v) for t,v in sorted(series.items())][-40:],
                    sketch={'width': self.sketch.width, 'depth': self.sketch.depth, 'counters': 256,
                            'keys': len(self.exact), 'comparisons': [{'key': k, 'exact': v, 'estimate': self.sketch.estimate(k)} for k,v in devices]},
                    patterns=self.apriori(), average_latency_ms=round(sum(e['latency_ms'] for e in self.events)/max(1, len(self.events)), 3))

    def scenario(self, name):
        if name not in {'normal', 'card_testing', 'trial_abuse', 'shared_ip', 'mixed'}:
            raise ValueError('Unknown scenario.')
        self.run_id += 1
        prefix = f'r{self.run_id}'
        def event(i, **kw):
            return dict(type='payment', account=f'{prefix}-user-{i}', device=f'{prefix}-device-{i}', ip=f'{prefix}-ip-{i}', card=f'{prefix}-card-{i}', amount=49, label='legitimate', **kw)
        rows = []
        if name == 'normal':
            rows = [event(i) for i in range(8)]
        elif name == 'card_testing':
            for i in range(8):
                e = event(i)
                e.update(card=f'{prefix}-tested-card', device=f'{prefix}-bot', amount=1, failed=True, country_mismatch=True, label='abuse')
                rows.append(e)
        elif name == 'shared_ip':
            for i in range(8):
                e = event(i)
                e.update(type='signup', ip=f'{prefix}-campus-wifi')
                rows.append(e)
        elif name == 'trial_abuse':
            for i in range(4):
                for kind in ['signup', 'trial', 'usage', 'cancel']:
                    e = event(i)
                    e.update(type=kind, device=f'{prefix}-reused-laptop', ip=f'{prefix}-repeat-ip', disposable=True, usage=95 if kind == 'usage' else 0,
                             label='unknown' if kind == 'cancel' else 'abuse')
                    rows.append(e)
        else:
            # Deterministic mixed demonstration; add plausible ambiguous examples.
            for child in ['normal', 'card_testing', 'trial_abuse', 'shared_ip']:
                rows.extend(self.scenario(child))
            for i in range(3):
                e = event(100+i)
                e.update(amount=1500, country_mismatch=True)  # legitimate traveler: false positive
                rows.append(e)
            e = event(999)
            e.update(label='abuse')  # stealth abuse: false negative
            rows.append(e)
        return rows
