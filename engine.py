"""Educational stream detector. Synthetic identifiers only; no external services.

Primary analytical algorithms (syllabus): Apriori, DGIM, Bloom Filter.
Decision layer: transparent weighted risk rules -> allow/review/block.
A learned logistic-regression baseline is a third, shadow view; labels never
enter scoring, Bloom, DGIM, or Apriori baskets.

The clock advances by each event's `ts` when supplied (event time), otherwise
by the legacy `step` field (default 1 second). Exact windows are configurable.
"""
from collections import Counter, OrderedDict, defaultdict, deque
from itertools import combinations
import json
import math
import random
import time
from pathlib import Path

from algorithms.bloom_filter import BloomFilter, RotatingBloomFilter
from algorithms.dgim import DGIM
import baseline as baseline_module
from evaluation import evaluation_report

SCENARIOS_PATH = Path(__file__).parent / 'scenarios.json'

# Indicators produced by the probabilistic structures themselves. Recorded on
# each event for the inspector, but EXCLUDED from Apriori baskets by default:
# they fire on nearly every repeat event and would otherwise dominate all top
# itemsets, hiding the domain-signal combinations the demo is about.
AUX_INDICATORS = frozenset({'bloom_device_seen', 'bloom_card_seen', 'dgim_device_burst'})

# Short documented free-forwarding domain list (not the disposable list).
FORWARDING_DOMAINS = frozenset({'duck.com', 'mozmail.com', 'simplelogin.com',
                                'anonaddy.com', 'relay.firefox.com'})


def suspicious_bit(event):
    """Binary suspicious-event predicate for DGIM streams.

    1 when the event carries an upstream suspicious indicator:
      issuer failure, temporary-email signal, free-forwarding signal, or
      billing/device country mismatch. 0 otherwise.
    Computed from attributes/signals only; NEVER from ground-truth label.
    """
    return 1 if (event.get('failed') or event.get('disposable')
                 or event.get('country_mismatch') or event.get('forwarding_email')) else 0


DGIM_SIGNAL_LABELS = (
    ('failed', 'failed payment'),
    ('disposable', 'throwaway email'),
    ('country_mismatch', 'country mismatch'),
    ('forwarding_email', 'forwarding email'),
)


def dgim_signals(event):
    """Friendly names for the attributes that make an event suspicious."""
    return [label for key, label in DGIM_SIGNAL_LABELS if event.get(key)]


def dgim_status(estimate, threshold):
    """Presentation status for a DGIM estimate (algorithm untouched)."""
    if estimate >= threshold:
        return 'burst'
    return 'watch' if estimate > 0 else 'normal'


def dgim_fired(event):
    """True when the stored score for this event includes the DGIM +10."""
    return any('DGIM' in str(reason.get('reason', ''))
               for reason in event.get('reasons', []))


class Detector:
    WINDOW = 60  # backwards-compatible default (instance: window_seconds)
    DGIM_WINDOW = 60
    DGIM_THRESHOLD = 5
    BLOOM_OUTCOMES = ('definitely_absent', 'true_positive', 'false_positive')
    LIMIT = 60000  # session-bounded; the synthetic demo stays far below this
    TYPES = {'payment', 'signup', 'trial', 'usage', 'cancel'}
    APRIORI_MAX_EVENTS = 5000

    def __init__(self, window_seconds=60, dgim_window=60, dgim_threshold=5,
                 bloom_size=512, bloom_hashes=3, bloom_rotate_every=0,
                 dgim_max_streams=512):
        self.window_seconds = int(window_seconds)
        self.WINDOW = self.window_seconds
        self.dgim_window = int(dgim_window)
        self.DGIM_WINDOW = self.dgim_window
        self.DGIM_THRESHOLD = int(dgim_threshold)
        self.bloom_size = int(bloom_size)
        self.bloom_hashes = int(bloom_hashes)
        self.bloom_rotate_every = int(bloom_rotate_every)
        self.dgim_max_streams = int(dgim_max_streams)
        self.apriori_support = 0.12
        self.apriori_confidence = 0.0
        self.review_threshold = 35
        self.block_threshold = 65
        self.risk_cutoffs = {'elevated': 35, 'highest': 65}
        self.baseline_model = baseline_module.load_model()
        self.apriori_max_events = self.APRIORI_MAX_EVENTS
        self.dataset = {'name': 'synthetic', 'source': 'synthetic', 'window_seconds': self.window_seconds}
        self.reset()

    def reset(self):
        self.clock = 0
        self.dataset_start = 0
        self.events = []
        self.window = deque()
        self.device_accounts = defaultdict(Counter)
        self.ip_accounts = defaultdict(Counter)
        self.card_payments = Counter()
        self.device_trials = Counter()
        self.device_cancels = Counter()
        # Syllabus structures (session lifetime; cleared on reset).
        self.bloom_device = self._make_bloom()
        self.bloom_card = self._make_bloom()
        # Per-entity DGIM streams with LRU eviction to stay memory-bounded.
        self.dgim_device_streams = OrderedDict()
        self.dgim_card_streams = OrderedDict()
        self.rng = random.Random(42)
        self.run_id = 0
        self._scenario_cache = None
        # Incremental counters keep light replay state O(1) per event.
        self.action_counts = Counter()
        self._conf = Counter()
        self.series_counts = defaultdict(lambda: {'total': 0, 'flagged': 0})

    def _make_bloom(self):
        if self.bloom_rotate_every > 0:
            return RotatingBloomFilter(self.bloom_size, self.bloom_hashes, self.bloom_rotate_every)
        return BloomFilter(self.bloom_size, self.bloom_hashes)

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
        if (isinstance(seconds, bool) or not isinstance(seconds, (int, float))
                or not math.isfinite(seconds) or not 0 <= seconds <= 1e12):
            raise ValueError('Time advance must be between 0 and 1e12 seconds.')
        self.clock += seconds
        # Window is (now - window_seconds, now], including the current event.
        while self.window and self.window[0]['time'] <= self.clock - self.window_seconds:
            self._update(self.window.popleft(), -1)
        # DGIM is count-based (last N elements), not time-based, so a bare time
        # advance does not expire DGIM buckets.

    def advance_to(self, timestamp):
        timestamp = float(timestamp)
        if not math.isfinite(timestamp):
            raise ValueError('Event timestamp must be finite.')
        delta = timestamp - self.clock
        if delta < -1e-9:
            raise ValueError('Event timestamps must be non-decreasing.')
        self.advance(max(0.0, delta))

    def validate(self, raw):
        e = dict(raw)
        if e.get('type') not in self.TYPES:
            raise ValueError('Choose a valid event type.')
        for key in ['account', 'device', 'ip', 'card']:
            value = e.get(key, 'card-none' if key == 'card' else '')
            if key == 'device' and value in (None, ''):
                e[key] = 'unknown-device'
                continue
            if not isinstance(value, str) or not value.strip() or len(value) > 120:
                raise ValueError(f'{key} must contain 1-120 characters.')
            e[key] = value.strip()
        for key in ['amount', 'usage']:
            value = e.get(key, 0)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1000000:
                raise ValueError(f'{key} must be a number between 0 and 1,000,000.')
            e[key] = value
        for key in ['disposable', 'country_mismatch', 'failed', 'forwarding_email']:
            if not isinstance(e.get(key, False), bool):
                raise ValueError(f'{key} must be true or false.')
            e[key] = e.get(key, False)
        if e.get('label') not in ('legitimate', 'abuse', 'unknown', None):
            raise ValueError('Invalid ground-truth label.')
        e['label'] = e.get('label') or 'unknown'
        if e.get('ts') is not None:
            ts = e['ts']
            if isinstance(ts, bool) or not isinstance(ts, (int, float)) or not math.isfinite(ts):
                raise ValueError('ts must be a finite number of seconds.')
        if e.get('email_domain') is not None:
            e['email_domain'] = str(e['email_domain'])[:120]
        return e

    def _dgim_stream(self, store, key):
        d = store.get(key)
        if d is not None:
            store.move_to_end(key)
            return d
        d = DGIM(window_size=self.dgim_window, max_buckets=2)
        store[key] = d
        while len(store) > self.dgim_max_streams:
            store.popitem(last=False)
        return d

    def process(self, raw):
        if len(self.events) >= self.LIMIT:
            raise ValueError(f'Demo limit reached ({self.LIMIT} events). Reset to start a new session.')
        e = self.validate(raw)
        started = time.perf_counter()
        if e.get('ts') is not None:
            self.advance_to(e['ts'])
        else:
            self.advance(raw.get('step', 1))
        e.update(id=f'evt_{len(self.events)+1:05}', time=self.clock, ts=e.get('ts', self.clock))
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
        add(payment and features['card_attempts'] >= 4, 45,
            f"At least 4 attempts on this card in {self.window_seconds}s", 'card_velocity')
        add(payment and e['failed'], 15, 'Issuer failure signal', 'failed_payment')
        add(payment and e['amount'] >= 1000, 20, 'Payment amount at least $1,000', 'large_amount')
        add(payment and e['country_mismatch'], 20, 'Billing and device country differ', 'country_mismatch')
        add(lifecycle and features['device_accounts'] >= 3, 30,
            f"At least 3 accounts on this device in {self.window_seconds}s", 'device_reuse')
        add(lifecycle and features['device_trials'] >= 2, 30,
            f"At least 2 trial starts on this device in {self.window_seconds}s", 'repeat_trial')
        add(lifecycle and e['disposable'], 15, 'Temporary-email indicator (proxy)', 'temporary_email')
        add(lifecycle and e['forwarding_email'], 10, 'Free-forwarding email domain (proxy)', 'free_forwarding_email')
        add(lifecycle and features['ip_accounts'] >= 4, 10,
            f"At least 4 accounts share this IP in {self.window_seconds}s", 'shared_ip')
        add(e['type'] == 'usage' and e['usage'] >= 80, 20, 'At least 80% of trial credits consumed', 'heavy_usage')
        add(e['type'] == 'trial' and features['recent_cancels'] > 0, 20,
            'Trial follows a cancellation on this device', 'trial_after_cancel')

        # Raw basket indicators for Apriori. Bloom/DGIM markers are recorded on
        # the event but excluded from baskets inside apriori().
        if device_seen and 'bloom_device_seen' not in items:
            items.append('bloom_device_seen')
        if card_seen and 'bloom_card_seen' not in items:
            items.append('bloom_card_seen')
        if dgim_burst and 'dgim_device_burst' not in items:
            items.append('dgim_device_burst')

        base_score = min(100, sum(r['points'] for r in reasons))
        e.update(features=features, reasons=reasons, items=items, score=base_score)
        base_action = self.action(base_score)
        # Modest (+10), corroborated, never blocks alone: Bloom positives are
        # common in legitimate reuse and DGIM is approximate.
        aux_reasons = []
        aux_items = []
        if lifecycle and device_seen and (features['device_accounts'] >= 2 or features['device_trials'] >= 1 or e['disposable']):
            aux_reasons.append({'points': 10, 'reason': 'Device possibly seen before (Bloom, corroborated)'})
            aux_items.append('bloom_device_seen')
        if payment and card_seen and (features['card_attempts'] >= 2 or e['failed']):
            aux_reasons.append({'points': 10, 'reason': 'Card possibly seen before (Bloom, corroborated)'})
            aux_items.append('bloom_card_seen')
        if dgim_burst and e['type'] != 'cancel' and bit == 1 and (e['failed'] or e['disposable'] or e['country_mismatch'] or e['forwarding_email']):
            aux_reasons.append({'points': 10, 'reason': f'Unusually high recent suspicious activity on device (DGIM est. {dgim_est}/{self.dgim_window})'})
            aux_items.append('dgim_device_burst')
        for r in aux_reasons:
            reasons.append(r)
        for item in aux_items:
            if item not in items:
                items.append(item)
        full_score = min(100, base_score + sum(r['points'] for r in aux_reasons))
        e.update(reasons=reasons, items=items, score=full_score)
        e['shadow'] = {'base_score': base_score, 'base_action': base_action,
                       'delta': full_score - base_score,
                       'flipped': base_action != self.action(full_score)}
        e.update(action=self.action(e['score']), review_threshold=self.review_threshold,
                 block_threshold=self.block_threshold, risk_level=self.risk_bucket(e['score']))
        e['bloom'] = {'device_result': device_bloom, 'card_result': card_bloom,
                      'device_seen': device_seen, 'card_seen': card_seen}
        e['dgim'] = {'bit': bit, 'device_est': dgim_est, 'device_exact': dgim_exact,
                     'window': self.dgim_window, 'threshold': self.DGIM_THRESHOLD, 'burst': dgim_burst,
                     'buckets': dev_stream.bucket_summary()[-8:]}

        # ---- Learned baseline: third shadow view (labels never read) ----
        if self.baseline_model:
            vector = baseline_module.extract_features(e)
            probability = baseline_module.probability(self.baseline_model, vector)
            learned = round(probability * 100, 4)
            e['baseline'] = {'score': learned, 'probability': round(probability, 6),
                             'action': self.action(learned),
                             'features': {k: round(v, 4) for k, v in vector.items()}}
        else:
            e['baseline'] = None
        e['latency_ms'] = round((time.perf_counter() - started) * 1000, 3)

        # ---- Bloom: insert AFTER query, high-risk identifiers only ----
        high_risk = (bit == 1) or (e['action'] != 'allow')
        if high_risk:
            event_meta = {'event_id': e['id'], 'action': e['action'], 'score': e['score']}
            self.bloom_device.add(e['device'], **event_meta)
            self.bloom_card.add(e['card'], **event_meta)
        e['bloom']['inserted'] = high_risk
        self.events.append(e)
        self.action_counts[e['action']] += 1
        if e['label'] in ('abuse', 'legitimate'):
            positive = e['action'] != 'allow'
            truth = e['label'] == 'abuse'
            self._conf['tp' if positive and truth else 'fp' if positive else 'fn' if truth else 'tn'] += 1
        bucket = int(e['time'] // max(10, int(self.window_seconds // 6) or 10)) * max(10, int(self.window_seconds // 6) or 10)
        self.series_counts[bucket]['total'] += 1
        self.series_counts[bucket]['flagged'] += e['action'] != 'allow'
        return e

    def action(self, score):
        return 'block' if score >= self.block_threshold else 'review' if score >= self.review_threshold else 'allow'

    def risk_bucket(self, score):
        if score >= self.risk_cutoffs.get('highest', 65):
            return 'highest'
        if score >= self.risk_cutoffs.get('elevated', 35):
            return 'elevated'
        return 'normal'

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
        return dict(**cm, precision=ratio(cm['tp'], cm['tp'] + cm['fp']),
                    recall=ratio(cm['tp'], cm['tp'] + cm['fn']),
                    false_positive_rate=ratio(cm['fp'], cm['fp'] + cm['tn']),
                    labeled=sum(cm.values()))

    # ---- Apriori ----
    def _source_events(self):
        if len(self.events) > self.apriori_max_events:
            return self.events[-self.apriori_max_events:]
        return self.events

    def suggested_min_support(self, events=None):
        source = self._source_events() if events is None else events
        labeled = [e for e in source if e.get('label') in ('abuse', 'legitimate')]
        if not labeled:
            return 0.12
        prevalence = sum(e['label'] == 'abuse' for e in labeled) / len(labeled)
        return round(max(0.02, min(0.12, prevalence)), 4)

    def apriori(self, min_support=None, include_labels=False, events=None, min_confidence=None):
        """Level-wise Apriori with subset pruning; at most 3-item sets.

        Baskets EXCLUDE Bloom/DGIM aux indicators so domain-signal
        combinations are not drowned out by repeat-event markers.

        include_labels=True is OFFLINE, EVALUATION-ONLY: each basket gains
        `label:abuse` / `label:legitimate` as an item. Scoring never sees this.

        Returns itemsets, rules, analyzed, min_support and per-level counts.
        """
        if min_support is None:
            min_support = self.apriori_support
        if min_confidence is None:
            min_confidence = self.apriori_confidence
        source = self._source_events() if events is None else events
        baskets = []
        for e in source:
            basket = set(e.get('items', [])) - AUX_INDICATORS
            if include_labels:
                label = e.get('label')
                if label in ('abuse', 'legitimate'):
                    basket = set(basket) | {f'label:{label}'}
            baskets.append(basket)
        n = len(baskets)
        if not n:
            return {'itemsets': [], 'rules': [], 'analyzed': 0, 'min_support': min_support,
                    'levels': []}
        minimum = max(1, math.ceil(n * min_support))
        counts = {}

        def count_of(candidate):
            key = frozenset(candidate)
            if key not in counts:
                counts[key] = sum(key <= b for b in baskets)
            return counts[key]

        level = {frozenset([item]) for basket in baskets for item in basket}
        results = []
        levels = []
        for size in range(1, 4):
            candidates = sorted(level, key=lambda x: sorted(x))
            frequent = set()
            for candidate in candidates:
                c = count_of(candidate)
                if c >= minimum:
                    frequent.add(candidate)
                    results.append({'items': sorted(candidate), 'count': c, 'support': round(c / n, 4)})
            levels.append({'level': size, 'candidates': len(candidates), 'frequent': len(frequent)})
            level = {a | b for a in frequent for b in frequent
                     if len(a | b) == size + 1
                     and all(frozenset(s) in frequent for s in combinations(a | b, size))}
            if not level:
                break
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
                    if conf < min_confidence:
                        continue
                    lift = conf / (cons_c / n) if cons_c else None
                    rules.append({'antecedent': sorted(ant), 'consequent': sorted(cons),
                                  'count': full_count, 'support': round(full_count / n, 4),
                                  'confidence': round(conf, 4),
                                  'lift': round(lift, 4) if lift is not None else None})
        results = sorted(results, key=lambda p: (-len(p['items']), -p['count'], p['items']))[:16]
        rules = sorted(rules, key=lambda r: (-r['confidence'], -r['lift'] or 0, -r['count']))[:8]
        return {'itemsets': results, 'rules': rules, 'analyzed': n,
                'min_support': min_support, 'levels': levels}

    def apriori_class_comparison(self, min_support=None):
        """Label-free baskets on the abuse-only subset vs all events.

        Labels only select the subset; they are NOT added as basket items.
        This is evaluation-only analysis, never a scoring input.
        """
        source = self._source_events()
        suggested = self.suggested_min_support(source)
        support = suggested if min_support is None else min_support
        abuse = [e for e in source if e.get('label') == 'abuse']
        return {
            'suggested_min_support': suggested,
            'prevalence': round(len(abuse) / len(source), 4) if source else 0,
            'min_support': support,
            'all_events': self.apriori(min_support=support, events=source),
            'abuse_only': self.apriori(min_support=support, events=abuse) if abuse else
            {'itemsets': [], 'rules': [], 'analyzed': 0, 'min_support': support, 'levels': []},
            'note': 'Baskets stay label-free; labels only select the fraud-only subset for offline comparison.',
        }

    def apriori_time_split(self, min_support=None):
        """Mine the first half of the session, test rules on the second half."""
        if min_support is None:
            min_support = self.apriori_support
        source = self._source_events()
        n = len(source)
        if n < 4:
            return {'train_n': 0, 'test_n': n, 'checked': [], 'note': 'Need at least 4 events for a time split.'}
        cut = n // 2
        train = self.apriori(min_support=min_support, include_labels=True, events=source[:cut])
        test_baskets = []
        for e in source[cut:]:
            basket = (set(e.get('items', [])) - AUX_INDICATORS)
            if e.get('label') in ('abuse', 'legitimate'):
                basket = basket | {f"label:{e['label']}"}
            test_baskets.append(basket)
        m = len(test_baskets)
        checked = []
        for r in train['rules'][:8]:
            ant, cons = frozenset(r['antecedent']), frozenset(r['consequent'])
            ant_c = sum(ant <= b for b in test_baskets)
            full_c = sum((ant | cons) <= b for b in test_baskets)
            test_conf = round(full_c / ant_c, 4) if ant_c else None
            checked.append({'antecedent': r['antecedent'], 'consequent': r['consequent'],
                            'train_confidence': r['confidence'], 'test_confidence': test_conf,
                            'test_antecedent_count': ant_c, 'lift': r['lift']})
        return {'train_n': cut, 'test_n': m, 'checked': checked, 'note': ''}

    # ---- state ----
    def _dgim_ranked_entities(self):
        """Rank every device/card stream by current estimate, highest first."""
        ranked = []
        for kind, store in (('device', self.dgim_device_streams),
                            ('card', self.dgim_card_streams)):
            for name, stream in store.items():
                estimate = stream.estimate()
                ranked.append({
                    'name': name,
                    'kind': kind,
                    'estimate': estimate,
                    'exact': stream.exact_count(),
                    'events': stream.pos,
                    'bucket_count': len(stream.buckets),
                    'status': dgim_status(estimate, self.DGIM_THRESHOLD),
                })
        ranked.sort(key=lambda row: (-row['estimate'], -row['events'],
                                     row['name'], row['kind']))
        return ranked

    @staticmethod
    def _dgim_default(ranked):
        """Highest-ranked entity; when all are NORMAL, the busiest one."""
        if not ranked:
            return None
        abnormal = [row for row in ranked if row['estimate'] > 0]
        if abnormal:
            return abnormal[0]
        return sorted(ranked, key=lambda row: (-row['events'], row['name'],
                                               row['kind']))[0]

    def _dgim_entity_view(self, kind, entity, ranked):
        store = self.dgim_card_streams if kind == 'card' else self.dgim_device_streams
        stream = store.get(entity)
        entity_events = [e for e in self.events if e.get(kind) == entity]
        if stream is not None:
            state = stream.to_state()
            state['all_buckets'] = stream.bucket_summary()
        else:
            state = {
                'window_size': self.dgim_window, 'max_buckets': 2,
                'stream_pos': 0, 'estimate': 0, 'exact': 0,
                'buckets': [], 'all_buckets': [], 'bucket_count': 0,
                'invariants': {'powers_of_two': True, 'max_per_size_ok': True,
                               'ordered': True, 'no_expired': True},
            }
        state.update(entity=entity, kind=kind, threshold=self.DGIM_THRESHOLD,
                     ranked=ranked, entities=[row['name'] for row in ranked],
                     recent=[], latest=None, first_fired=None,
                     view_buckets=state.get('all_buckets', []))
        state['all_normal'] = bool(ranked) and all(
            row['status'] == 'normal' for row in ranked)
        if not entity_events:
            return state

        # Replay only for the display history; scoring still uses the live
        # stream above. Bucket positions are stream positions, one per event.
        if stream is not None and 0 < stream.pos <= len(entity_events):
            replay_events = entity_events[-stream.pos:]
        else:
            replay_events = entity_events
        window = self.dgim_window
        recent_events = replay_events[-window:]
        recent_start = len(replay_events) - len(recent_events)
        replay = DGIM(window_size=window, max_buckets=2)
        recent = []
        first_fired = None
        for index, event in enumerate(replay_events):
            bit = suspicious_bit(event)
            replay.update(bit)
            fired = dgim_fired(event)
            if fired and first_fired is None:
                first_fired = {'id': event['id'], 'pos': index + 1}
            if index < recent_start:
                continue
            dgim_points = sum(r['points'] for r in event.get('reasons', [])
                              if 'DGIM' in str(r.get('reason', '')))
            score_before = min(100, sum(r['points'] for r in event.get('reasons', [])
                                        if 'DGIM' not in str(r.get('reason', ''))))
            recent.append({
                'id': event['id'],
                'pos': index + 1,
                'time': event['time'],
                'ts': event.get('ts'),
                'type': event['type'],
                'action': event['action'],
                'score': event['score'],
                'bit': bit,
                'cancel': event['type'] == 'cancel',
                'signals': dgim_signals(event),
                'estimate': replay.estimate(),
                'exact': replay.exact_count(),
                'fired': fired,
                'dgim_points': dgim_points,
                'score_before_dgim': score_before,
                'score_after_dgim': event['score'],
                'device_estimate': event.get('dgim', {}).get('device_est'),
                'device_threshold': event.get('dgim', {}).get('threshold'),
                'device_burst': event.get('dgim', {}).get('burst'),
            })

        exact = sum(row['bit'] for row in recent)
        if stream is None:
            state.update(stream_pos=len(replay_events), estimate=replay.estimate(),
                         buckets=replay.bucket_summary()[-12:],
                         all_buckets=replay.bucket_summary(),
                         view_buckets=replay.bucket_summary(),
                         bucket_count=len(replay.buckets),
                         invariants=replay.check_invariants())
        state['exact'] = exact
        if recent:
            # Keep the newest visible point aligned with the live estimate.
            recent[-1]['estimate'] = state['estimate']
            recent[-1]['exact'] = exact
        state['recent'] = recent
        state['latest'] = recent[-1] if recent else None
        state['first_fired'] = first_fired
        return state

    def dgim_state(self, entity=None, kind=None):
        ranked = self._dgim_ranked_entities()
        if entity is None:
            picked = self._dgim_default(ranked)
            if picked is not None:
                entity, kind = picked['name'], picked['kind']
        elif kind not in ('device', 'card'):
            kind = 'device' if entity in self.dgim_device_streams else (
                'card' if entity in self.dgim_card_streams else 'device')
        return self._dgim_entity_view(kind or 'device', entity, ranked)

    def bloom_state(self):
        return {
            'device': self.bloom_device.to_state(),
            'card': self.bloom_card.to_state(),
        }

    def bloom_inspection(self, kind, key):
        """Query-only Bloom inspection with an explicit, stable outcome."""
        if kind not in ('device', 'card'):
            raise ValueError('Provide kind=device|card.')
        key = str(key).strip()
        if not key:
            raise ValueError('Provide a non-empty key.')
        bf = self.bloom_device if kind == 'device' else self.bloom_card
        result = bf.classify(key, count_query=False)
        if result.get('outcome') not in self.BLOOM_OUTCOMES:
            raise ValueError('Bloom check produced an invalid outcome.')
        result.update(kind=kind, size=bf.size, num_hashes=bf.num_hashes,
                      hint=self._bloom_hint(kind, result['key']))
        return result

    def _bloom_hint(self, kind, key):
        """Explain a wrong-kind or account-ID query without mutating a filter."""
        bf = self.bloom_device if kind == 'device' else self.bloom_card
        if key in bf.exact_members():
            return None
        other = 'card' if kind == 'device' else 'device'
        other_event = next((e for e in reversed(self.events)
                            if e.get(other) == key), None)
        if other_event is not None:
            return {
                'kind': other,
                'message': f'This is a {other} ID; switch Query kind to {other}.',
                'event_id': other_event.get('id'),
                'choices': [{'kind': other, 'key': key,
                             'label': f'Switch Query kind to {other}'}],
            }
        account_event = next((e for e in reversed(self.events)
                              if e.get('account') == key), None)
        if account_event is not None:
            choices = []
            for role in ('device', 'card'):
                value = account_event.get(role)
                if value:
                    choices.append({'kind': role, 'key': value,
                                    'label': f'Check {role} {value}'})
            return {
                'kind': 'account',
                'message': 'This looks like an account ID. Bloom stores device and card IDs.',
                'event_id': account_event.get('id'),
                'choices': choices,
            }
        return None

    def fast_metrics(self):
        cm = {k: self._conf.get(k, 0) for k in ('tp', 'fp', 'tn', 'fn')}

        def ratio(a, b):
            return round(a / b, 4) if b else None
        return dict(**cm, precision=ratio(cm['tp'], cm['tp'] + cm['fp']),
                    recall=ratio(cm['tp'], cm['tp'] + cm['fn']),
                    false_positive_rate=ratio(cm['fp'], cm['fp'] + cm['tn']),
                    labeled=sum(cm.values()))

    def state(self, dgim_entity=None, dgim_entity_kind=None, light=False):
        counts = self.action_counts
        series = self.series_counts
        result = dict(
            clock=self.clock, dataset_start=self.dataset_start, total=len(self.events), active=len(self.window),
            counts=dict(counts),
            thresholds={'review': self.review_threshold, 'block': self.block_threshold},
            risk_cutoffs=dict(self.risk_cutoffs),
            window_seconds=self.window_seconds,
            dgim_config={'window': self.dgim_window, 'threshold': self.DGIM_THRESHOLD,
                         'max_streams': self.dgim_max_streams,
                         'streams': len(self.dgim_device_streams)},
            apriori_config={'min_support': self.apriori_support,
                            'min_confidence': self.apriori_confidence,
                            'suggested_min_support': self.suggested_min_support()},
            metrics=self.fast_metrics() if light else self.metrics(),
            events=list(reversed(self.events[-150:])),
            series=[dict(time=t, **v) for t, v in sorted(series.items())][-40:],
            bloom=self.bloom_state(),
            dgim=self.dgim_state(dgim_entity, dgim_entity_kind),
            dataset=self.dataset,
            baseline_available=bool(self.baseline_model),
            baseline_training=self.baseline_model.get('training') if self.baseline_model else None,
            average_latency_ms=round(sum(e['latency_ms'] for e in self.events) / max(1, len(self.events)), 3),
        )
        if light:
            result.update(patterns=[], rules=[], analyzed=0,
                          apriori_eval={'itemsets': [], 'rules': [], 'analyzed': 0},
                          apriori_class=None, apriori_current=None, time_split=None,
                          shadow=self.shadow_summary(light=True), evaluation=None)
            return result
        ap = self.apriori()
        ap_eval = self.apriori(include_labels=True)
        result.update(
            patterns=ap['itemsets'], rules=ap['rules'], analyzed=ap['analyzed'],
            apriori_levels=ap['levels'],
            apriori_eval={'itemsets': ap_eval['itemsets'][:8], 'rules': ap_eval['rules'][:8],
                          'analyzed': ap_eval['analyzed']},
            apriori_class=self.apriori_class_comparison(),
            apriori_current=self.apriori_class_comparison(min_support=self.apriori_support),
            time_split=self.apriori_time_split(),
            shadow=self.shadow_summary(),
            evaluation=evaluation_report(self.events, self.review_threshold, self.block_threshold),
        )
        return result

    def shadow_summary(self, light=False):
        flipped = [e for e in self.events if e.get('shadow', {}).get('flipped')]
        toward_review = [e for e in flipped
                         if self.action(e['shadow']['base_score']) == 'allow' and e['action'] != 'allow']
        summary = {'compared': len(self.events), 'flipped': len(flipped),
                   'allow_to_flagged': len(toward_review),
                   'flipped_ids': [e['id'] for e in flipped][-12:]}
        if not light:
            baseline_flipped = [e for e in self.events
                                if e.get('baseline') and e['baseline']['action'] != e['action']]
            summary['baseline_view'] = {
                'compared': len(self.events),
                'flipped_vs_full': len(baseline_flipped),
                'baseline_flagged': sum(1 for e in self.events
                                        if e.get('baseline') and e['baseline']['action'] != 'allow'),
            }
        return summary

    def replay(self, events, limit=None):
        """Reset and process a time-ordered event list; used by tests/API."""
        self.reset()
        selected = events[:limit] if limit else events
        for event in selected:
            self.process(event)
        return self.state()

    # ---- scenarios ----
    def _params(self):
        if self._scenario_cache is None:
            try:
                with SCENARIOS_PATH.open('r', encoding='utf-8') as fh:
                    self._scenario_cache = json.load(fh)
            except (OSError, ValueError):
                self._scenario_cache = {}
        return self._scenario_cache

    def _scenario_event(self, prefix, i, **kw):
        event = dict(type='payment', account=f'{prefix}-user-{i}', device=f'{prefix}-device-{i}',
                     ip=f'{prefix}-ip-{i}', card=f'{prefix}-card-{i}', amount=49, label='legitimate', **kw)
        return event

    def scenario(self, name):
        params = self._params().get(name, {})
        if name not in {'normal', 'card_testing', 'trial_abuse', 'multi_account_device', 'mixed'}:
            raise ValueError('Unknown scenario.')
        self.run_id += 1
        prefix = f'r{self.run_id}'

        def event(i, **kw):
            return self._scenario_event(prefix, i, **kw)

        rows = []
        if name == 'normal':
            rows = [event(i) for i in range(8)]
        elif name == 'card_testing':
            for i in range(8):
                e = event(i)
                e.update(card=f'{prefix}-tested-card', device=f'{prefix}-bot', amount=1,
                         failed=True, country_mismatch=True, label='abuse')
                rows.append(e)
        elif name == 'trial_abuse':
            cycles = int(params.get('accounts_per_device', 4))
            gap = float(params.get('inter_signup_gap', 1))
            for i in range(cycles):
                for kind in ['signup', 'trial', 'usage', 'cancel']:
                    e = event(i)
                    e.update(type=kind, device=f'{prefix}-reused-laptop', ip=f'{prefix}-repeat-ip',
                             disposable=True, usage=95 if kind == 'usage' else 0,
                             step=gap,
                             label='unknown' if kind == 'cancel' else 'abuse')
                    rows.append(e)
        elif name == 'multi_account_device':
            accounts = int(params.get('accounts_per_device', 6))
            gap = float(params.get('inter_signup_gap', 8))
            disposable_rate = float(params.get('disposable_email_rate', 0.5))
            for i in range(accounts):
                e = event(i)
                e.update(type='signup' if i % 2 == 0 else 'trial',
                         device=f'{prefix}-shared-tablet', ip=f'{prefix}-shared-ip',
                         disposable=(i % 2 == 0 or disposable_rate >= 0.5),
                         label='abuse', step=gap)
                rows.append(e)
        else:
            for child in ['normal', 'card_testing', 'trial_abuse', 'multi_account_device']:
                rows.extend(self.scenario(child))
            for i in range(3):
                e = event(100 + i)
                e.update(amount=1500, country_mismatch=True)
                rows.append(e)
            e = event(999)
            e.update(label='abuse')
            rows.append(e)
        return rows
