"""Educational stream detector. Synthetic identifiers only; no Stripe integration.

Primary analytical algorithms (syllabus): Apriori, DGIM, Bloom Filter.
Decision layer: transparent weighted risk rules -> allow/review/block.
Score is a heuristic, not a fraud probability.
"""
from collections import Counter, defaultdict, deque
from itertools import combinations
import math
import random
import time

try:
    from algorithms.bloom_filter import BloomFilter
    from algorithms.dgim import DGIM
except ImportError:  # allow running from repo root or as module
    from bloom_filter import BloomFilter  # type: ignore
    from dgim import DGIM  # type: ignore


def suspicious_bit(event):
    """Binary suspicious-event predicate for DGIM streams.

    1 when the event carries an upstream suspicious indicator:
      simulated issuer failure, temporary-email signal, or billing/device
      country mismatch. 0 otherwise.
    Computed from attributes/signals only; NEVER from ground-truth label.
    Covers card-testing (failed) and trial abuse (disposable) while keeping
    everyday payments and shared-campus signups at 0.
    """
    return 1 if (event.get('failed') or event.get('disposable') or event.get('country_mismatch')) else 0


class Detector:
    WINDOW = 60  # exact sliding window in simulated seconds: (now-60, now]
    DGIM_WINDOW = 60  # classical DGIM: last N *stream elements* per entity
    DGIM_THRESHOLD = 5  # estimate >= threshold -> modest dgim_device_burst signal
    LIMIT = 2000
    TYPES = {'payment', 'signup', 'trial', 'usage', 'cancel'}

    def __init__(self):
        self.apriori_support = 0.12
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
        # Syllabus structures (session lifetime; cleared on reset).
        # Bloom filters are insert-only: expiry of exact counters does NOT
        # delete Bloom bits; reset() is the only way to clear them.
        self.bloom_device = BloomFilter(size=512, num_hashes=3)
        self.bloom_card = BloomFilter(size=512, num_hashes=3)
        # Per-entity DGIM streams (device + card). Created on demand.
        self.dgim_device_streams = {}
        self.dgim_card_streams = {}
        self.review_threshold = 35
        self.block_threshold = 65
        self.rng = random.Random(42)
        self.run_id = 0

    # ---- exact window ----
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
        # Note: DGIM is count-based (last N elements), not time-based, so a
        # bare time advance does not expire DGIM buckets. Under the default
        # 1-event-per-second playback, N=60 elements ~= 60 seconds.

    def validate(self, raw):
        e = dict(raw)
        if e.get('type') not in self.TYPES:
            raise ValueError('Choose a valid event type.')
        for key in ['account', 'device', 'ip', 'card']:
            value = e.get(key, 'card-none' if key == 'card' else '')
            if not isinstance(value, str) or not value.strip() or len(value) > 80:
                raise ValueError(f'{key} must contain 1-80 characters.')
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

    def _dgim_stream(self, store, key):
        d = store.get(key)
        if d is None:
            d = DGIM(window_size=self.DGIM_WINDOW, max_buckets=2)
            store[key] = d
        return d

    def process(self, raw):
        if len(self.events) >= self.LIMIT:
            raise ValueError('Demo limit reached (2,000 events). Reset to start a new session.')
        e = self.validate(raw)
        started = time.perf_counter()
        self.advance(raw.get('step', 1))
        e.update(id=f'evt_{len(self.events)+1:04}', time=self.clock)
        self.window.append(e)
        self._update(e, 1)

        # ---- Bloom: query BEFORE insert (first sighting must be absent) ----
        device_bloom = self.bloom_device.check(e['device'])
        card_bloom = self.bloom_card.check(e['card'])
        device_seen = device_bloom == 'possibly_present'
        card_seen = card_bloom == 'possibly_present'

        # ---- DGIM: binary suspicious predicate (never label) ----
        bit = suspicious_bit(e)
        dev_stream = self._dgim_stream(self.dgim_device_streams, e['device'])
        card_stream = self._dgim_stream(self.dgim_card_streams, e['card'])
        dev_stream.update(bit)
        card_stream.update(bit)
        dgim_est = dev_stream.estimate()  # pure query, no mutation
        dgim_exact = dev_stream.exact_count()
        dgim_burst = dgim_est >= self.DGIM_THRESHOLD

        features = {
            'device_accounts': len(self.device_accounts[e['device']]),
            'ip_accounts': len(self.ip_accounts[e['ip']]),
            'card_attempts': self.card_payments[e['card']],
            'device_trials': self.device_trials[e['device']],
            'recent_cancels': self.device_cancels[e['device']],
            'dgim_device_est': dgim_est,
            'dgim_device_exact': dgim_exact,
            'dgim_bit': bit,
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
        # Contextual probabilistic signals: modest, corroborated, never block alone.
        # Bloom positives are common in legitimate reuse (family devices), and
        # DGIM is approximate, so each contributes at most +10 and only with
        # corroborating evidence. Max combined (+20) stays below review (35).
        add(lifecycle and device_seen and (features['device_accounts'] >= 2 or features['device_trials'] >= 1 or e['disposable']),
            10, 'Device possibly seen before (Bloom, corroborated)', 'bloom_device_seen')
        add(payment and card_seen and (features['card_attempts'] >= 2 or e['failed']),
            10, 'Card possibly seen before (Bloom, corroborated)', 'bloom_card_seen')
        add(dgim_burst and e['type'] != 'cancel' and bit == 1 and (e['failed'] or e['disposable'] or e['country_mismatch']),
            10, f'Unusually high recent suspicious activity on device (DGIM est. {dgim_est}/60)', 'dgim_device_burst')

        # Raw basket indicators for Apriori (added whenever condition true,
        # independent of whether the corroborated scoring rule fired).
        if device_seen and 'bloom_device_seen' not in items:
            items.append('bloom_device_seen')
        if card_seen and 'bloom_card_seen' not in items:
            items.append('bloom_card_seen')
        if dgim_burst and 'dgim_device_burst' not in items:
            items.append('dgim_device_burst')

        e.update(features=features, reasons=reasons, items=items, score=min(100, sum(r['points'] for r in reasons)))
        e.update(action=self.action(e['score']), review_threshold=self.review_threshold, block_threshold=self.block_threshold)
        e['bloom'] = {'device_result': device_bloom, 'card_result': card_bloom,
                      'device_seen': device_seen, 'card_seen': card_seen}
        e['dgim'] = {'bit': bit, 'device_est': dgim_est, 'device_exact': dgim_exact,
                     'window': self.DGIM_WINDOW, 'threshold': self.DGIM_THRESHOLD, 'burst': dgim_burst,
                     'buckets': dev_stream.bucket_summary()[-8:]}
        e['latency_ms'] = round((time.perf_counter() - started) * 1000, 3)

        # ---- Bloom: insert AFTER query ----
        self.bloom_device.add(e['device'])
        self.bloom_card.add(e['card'])
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
            return round(a / b, 4) if b else None
        return dict(**cm, precision=ratio(cm['tp'], cm['tp'] + cm['fp']), recall=ratio(cm['tp'], cm['tp'] + cm['fn']),
                    false_positive_rate=ratio(cm['fp'], cm['fp'] + cm['tn']), labeled=sum(cm.values()))

    def apriori(self, min_support=None):
        """Level-wise Apriori with subset pruning; at most 3-item sets.

        Returns {'itemsets': [...], 'rules': [...], 'analyzed': n}.
        Rules carry support, confidence and lift. Co-occurrence is not causation.
        """
        if min_support is None:
            min_support = self.apriori_support
        baskets = [set(e.get('items', [])) for e in self.events]
        n = len(baskets)
        if not n:
            return {'itemsets': [], 'rules': [], 'analyzed': 0, 'min_support': min_support}
        # Ignore empty baskets for support denominator? No: denominator is all
        # session events (documented), so support = count / n.
        minimum = max(1, math.ceil(n * min_support))
        counts = {}

        def count_of(candidate):
            key = frozenset(candidate)
            if key not in counts:
                counts[key] = sum(key <= b for b in baskets)
            return counts[key]

        level = {frozenset([item]) for basket in baskets for item in basket}
        frequent_sets = set()
        results = []
        for size in range(1, 4):
            frequent = set()
            for candidate in sorted(level, key=lambda x: sorted(x)):
                c = count_of(candidate)
                if c >= minimum:
                    frequent.add(candidate)
                    frequent_sets.add(candidate)
                    results.append({'items': sorted(candidate), 'count': c, 'support': round(c / n, 4)})
            level = {a | b for a in frequent for b in frequent
                     if len(a | b) == size + 1
                     and all(frozenset(s) in frequent for s in combinations(a | b, size))}
            if not level:
                break
        # Association rules from frequent itemsets (size >= 2).
        support_of = {frozenset(r['items']): r['support'] for r in results}
        rules = []
        for r in results:
            items = r['items']
            if len(items) < 2:
                continue
            full = frozenset(items)
            full_count = r['count']
            for k in range(1, len(items)):
                for ant in combinations(items, k):
                    ant = frozenset(ant)
                    cons = full - ant
                    if not cons:
                        continue
                    ant_c = count_of(ant)
                    cons_c = count_of(cons)
                    if ant_c == 0 or cons_c == 0:
                        continue
                    conf = full_count / ant_c
                    lift = conf / (cons_c / n) if cons_c else None
                    rules.append({'antecedent': sorted(ant), 'consequent': sorted(cons),
                                  'count': full_count, 'support': round(full_count / n, 4),
                                  'confidence': round(conf, 4),
                                  'lift': round(lift, 4) if lift is not None else None})
        # Pruning note: candidates above already required all immediate
        # subsets frequent (Apriori property).
        results = sorted(results, key=lambda p: (-len(p['items']), -p['count'], p['items']))[:16]
        rules = sorted(rules, key=lambda r: (-r['confidence'], -r['lift'] or 0, -r['count']))[:8]
        return {'itemsets': results, 'rules': rules, 'analyzed': n, 'min_support': min_support}

    def dgim_state(self, entity=None):
        """DGIM inspection state for a device entity (default: most recent event)."""
        if entity is None and self.events:
            entity = self.events[-1]['device']
        streams = self.dgim_device_streams
        if entity in streams:
            d = streams[entity]
            st = d.to_state()
            st['entity'] = entity
            return st
        return {'entity': entity, 'window_size': self.DGIM_WINDOW, 'stream_pos': 0,
                'estimate': 0, 'exact': 0, 'buckets': [], 'bucket_count': 0,
                'entities': sorted(streams.keys())[:24]}

    def bloom_state(self):
        return {
            'device': self.bloom_device.to_state(),
            'card': self.bloom_card.to_state(),
        }

    def state(self, dgim_entity=None):
        counts = Counter(e['action'] for e in self.events)
        series = defaultdict(lambda: {'total': 0, 'flagged': 0})
        for e in self.events:
            bucket = int(e['time'] // 10) * 10
            series[bucket]['total'] += 1
            series[bucket]['flagged'] += e['action'] != 'allow'
        ap = self.apriori()
        return dict(clock=self.clock, total=len(self.events), active=len(self.window), counts=dict(counts),
                    thresholds={'review': self.review_threshold, 'block': self.block_threshold},
                    dgim_config={'window': self.DGIM_WINDOW, 'threshold': self.DGIM_THRESHOLD},
                    apriori_config={'min_support': self.apriori_support},
                    metrics=self.metrics(), events=list(reversed(self.events[-150:])),
                    series=[dict(time=t, **v) for t, v in sorted(series.items())][-40:],
                    bloom=self.bloom_state(),
                    dgim=self.dgim_state(dgim_entity),
                    patterns=ap['itemsets'], rules=ap['rules'], analyzed=ap['analyzed'],
                    average_latency_ms=round(sum(e['latency_ms'] for e in self.events) / max(1, len(self.events)), 3))

    def scenario(self, name):
        if name not in {'normal', 'card_testing', 'trial_abuse', 'shared_ip', 'mixed'}:
            raise ValueError('Unknown scenario.')
        self.run_id += 1
        prefix = f'r{self.run_id}'

        def event(i, **kw):
            return dict(type='payment', account=f'{prefix}-user-{i}', device=f'{prefix}-device-{i}',
                        ip=f'{prefix}-ip-{i}', card=f'{prefix}-card-{i}', amount=49, label='legitimate', **kw)
        rows = []
        if name == 'normal':
            # 8 independent legitimate payments: fresh device/card each time,
            # so Bloom reports absent and DGIM bits stay 0.
            rows = [event(i) for i in range(8)]
        elif name == 'card_testing':
            for i in range(8):
                e = event(i)
                e.update(card=f'{prefix}-tested-card', device=f'{prefix}-bot', amount=1,
                         failed=True, country_mismatch=True, label='abuse')
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
                    e.update(type=kind, device=f'{prefix}-reused-laptop', ip=f'{prefix}-repeat-ip',
                             disposable=True, usage=95 if kind == 'usage' else 0,
                             label='unknown' if kind == 'cancel' else 'abuse')
                    rows.append(e)
        else:
            # Deterministic mixed demonstration; add plausible ambiguous examples.
            for child in ['normal', 'card_testing', 'trial_abuse', 'shared_ip']:
                rows.extend(self.scenario(child))
            for i in range(3):
                e = event(100 + i)
                e.update(amount=1500, country_mismatch=True)  # legitimate traveler: false positive
                rows.append(e)
            e = event(999)
            e.update(label='abuse')  # stealth abuse: false negative
            rows.append(e)
        return rows
