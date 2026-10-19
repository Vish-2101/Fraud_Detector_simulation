import json
import math
import random
import unittest
from pathlib import Path

import baseline
from adapters.ieee_cis import map_transaction, schema_valid as ieee_schema_valid
from adapters.stripe_events import map_events, schema_valid as stripe_schema_valid
from algorithms.bloom_filter import BloomFilter, RotatingBloomFilter
from algorithms.dgim import DGIM, long_stream_demo
from datasets import load_dataset
from engine import Detector
from evaluation import evaluate_view, pr_curve, recall_at_fpr, temporal_split

ROOT = Path(__file__).resolve().parents[1]


def payment(**changes):
    event = {'ts': 0, 'type': 'payment', 'account': 'acct-a', 'device': 'device-a',
             'ip': 'ip-a', 'card': 'card-a', 'amount': 10}
    event.update(changes)
    return event


class LabelLeakageTests(unittest.TestCase):
    def test_labels_never_change_score_bloom_dgim_or_basket_items(self):
        raw = [
            payment(ts=0, label='abuse'),
            payment(ts=1, account='acct-b', device='device-a', card='card-a', failed=True),
            payment(ts=2, account='acct-c', device='device-a', card='card-a',
                    country_mismatch=True),
            {'ts': 3, 'type': 'signup', 'account': 'acct-d', 'device': 'device-a',
             'ip': 'ip-a', 'card': 'card-a', 'disposable': True},
        ]
        a, b = Detector(), Detector()
        results_a, results_b = [], []
        for i, event in enumerate(raw):
            copy = dict(event)
            copy['label'] = 'abuse' if i % 2 == 0 else 'legitimate'
            results_a.append(a.process(dict(copy, label='abuse')))
            results_b.append(b.process(dict(copy, label='legitimate')))
        for ea, eb in zip(results_a, results_b):
            self.assertEqual(ea['score'], eb['score'])
            self.assertEqual(ea['action'], eb['action'])
            self.assertEqual(ea['bloom'], eb['bloom'])
            self.assertEqual(ea['dgim']['bit'], eb['dgim']['bit'])
            self.assertEqual(ea['dgim']['device_est'], eb['dgim']['device_est'])
            self.assertEqual(ea['baseline'], eb['baseline'])
            self.assertNotIn('abuse', ea['items'])
            self.assertNotIn('legitimate', ea['items'])
        self.assertEqual(a.bloom_device.to_state()['bits'], b.bloom_device.to_state()['bits'])
        self.assertEqual(a.dgim_state('device-a')['estimate'], b.dgim_state('device-a')['estimate'])
        self.assertEqual(a.apriori()['itemsets'], b.apriori()['itemsets'])

    def test_stripe_risk_fields_do_not_score(self):
        base = payment(ts=100)
        a, b = Detector(), Detector()
        ea = a.process(dict(base, stripe_risk_level='highest', stripe_risk_score=99))
        eb = b.process(dict(base, stripe_risk_level='normal', stripe_risk_score=1))
        self.assertEqual(ea['score'], eb['score'])
        self.assertEqual(ea['action'], eb['action'])

    def test_class_comparison_does_not_add_label_items(self):
        d = Detector()
        d.events = [
            {'items': ['failed_payment'], 'label': 'abuse'},
            {'items': ['failed_payment', 'country_mismatch'], 'label': 'abuse'},
            {'items': ['large_amount'], 'label': 'legitimate'},
            {'items': [], 'label': 'legitimate'},
        ]
        out = d.apriori_class_comparison(min_support=0.25)
        for section in ('all_events', 'abuse_only'):
            for itemset in out[section]['itemsets']:
                self.assertTrue(all(not item.startswith('label:') for item in itemset['items']))


class EventTimeTests(unittest.TestCase):
    def test_event_time_clock_and_determinism(self):
        events = [
            payment(ts=0, failed=True),
            payment(ts=10, failed=True),
            payment(ts=10, account='acct-b', failed=True),
            payment(ts=70, account='acct-c', failed=True),
            payment(ts=131, account='acct-d', failed=True),
            payment(ts=131, account='acct-e', failed=True),
        ]
        a, b = Detector(window_seconds=60), Detector(window_seconds=60)
        first = [a.process(dict(e)) for e in events]
        second = [b.process(dict(e)) for e in events]
        self.assertEqual([(e['score'], e['action']) for e in first],
                         [(e['score'], e['action']) for e in second])
        self.assertEqual(a.clock, 131)
        self.assertEqual([e['time'] for e in first], [0, 10, 10, 70, 131, 131])
        # At t=131 the events at t=10 and t=70 are outside (131-60, 131].
        self.assertEqual(len(a.window), 2)
        self.assertEqual(a.events[-1]['features']['card_attempts'], 2)

    def test_clock_rejects_going_backwards(self):
        d = Detector()
        d.process(payment(ts=100))
        with self.assertRaises(ValueError):
            d.process(payment(ts=90))


class BloomSizingTests(unittest.TestCase):
    def test_for_capacity_meets_target_and_has_no_false_negatives(self):
        n, target = 1000, 0.01
        bf = BloomFilter.for_capacity(n, target)
        self.assertGreaterEqual(bf.size, 16)
        self.assertGreaterEqual(bf.num_hashes, 1)
        for i in range(n):
            bf.add(f'key-{i}')
        for i in range(n):
            self.assertEqual(bf.check(f'key-{i}'), 'possibly_present')
        unseen = 5000
        for i in range(unseen):
            bf.check(f'unseen-{i}')
        self.assertLessEqual(bf.theoretical_fpp(), 0.02)
        self.assertLessEqual(bf.measured_fpp, 0.05)

    def test_rotating_mode_forgets_after_two_generations(self):
        rf = RotatingBloomFilter(size=256, num_hashes=3, rotate_every=2)
        rf.add('old-key')
        rf.rotate()
        self.assertEqual(rf.check('old-key'), 'possibly_present')
        rf.rotate()
        self.assertEqual(rf.check('old-key', count_query=False), 'definitely_absent')


class DgimBoundTests(unittest.TestCase):
    def test_relative_error_within_documented_bound(self):
        for seed in range(20):
            rng = random.Random(seed)
            d = DGIM(window_size=200)
            history = []
            for step in range(500):
                bit = 1 if rng.random() < 0.35 else 0
                history.append(bit)
                d.update(bit)
                exact = sum(history[-200:])
                if exact >= 10:
                    guess = d.estimate()
                    self.assertLessEqual(abs(guess - exact) / exact, 0.6,
                                         f'seed={seed} step={step} exact={exact} est={guess}')
            self.assertTrue(all(d.check_invariants().values()))

    def test_scale_demo_storage_tradeoff(self):
        for pattern in ('random', 'ones', 'worst'):
            demo = long_stream_demo(n=1000, window=100, pattern=pattern)
            self.assertEqual(len(demo['samples']), 50)
            self.assertLessEqual(demo['summary']['max_buckets'], 16)
            self.assertLessEqual(demo['summary']['max_rel_error'], 0.6)

    def test_lru_caps_stream_count(self):
        d = Detector()
        d.dgim_max_streams = 16
        for i in range(100):
            d.process(payment(ts=i, device=f'dev-{i}', card=f'card-{i}', account=f'acct-{i}'))
        self.assertLessEqual(len(d.dgim_device_streams), 16)
        self.assertLessEqual(len(d.dgim_card_streams), 16)


class AdapterTests(unittest.TestCase):
    def test_ieee_fixture_maps_to_valid_events(self):
        events, meta = load_dataset('ieee_cis_fixture')
        self.assertGreaterEqual(len(events), 400)
        for event in events:
            self.assertTrue(ieee_schema_valid(event))
            self.assertIn(event['label'], ('abuse', 'legitimate'))
            self.assertEqual(event['type'], 'payment')
            self.assertFalse(event['failed'])
            self.assertEqual(event['source'], 'ieee_cis')

    def test_stripe_fixture_maps_to_valid_events(self):
        path = ROOT / 'fixtures' / 'stripe_events_sample.json'
        raw = json.loads(path.read_text(encoding='utf-8'))
        events = map_events(raw)
        self.assertGreaterEqual(len(events), 30)
        for event in events:
            self.assertTrue(stripe_schema_valid(event))
            self.assertIn('stripe_risk_level', event)
            self.assertIn(event['type'], {'payment', 'signup', 'trial', 'usage', 'cancel'})
            self.assertEqual(event['source'], 'stripe')

    def test_ieee_active_rules_reflect_payment_only_mapping(self):
        from adapters.ieee_cis import active_rules
        events, _ = load_dataset('ieee_cis_fixture', limit=100)
        rules = active_rules(events)
        self.assertTrue(rules['card_velocity'])
        self.assertTrue(rules['large_amount'])
        self.assertTrue(rules['country_mismatch'])
        for name in ('device_reuse', 'repeat_trial', 'temporary_email',
                     'free_forwarding_email', 'shared_ip', 'heavy_usage',
                     'trial_after_cancel', 'bloom_device_seen'):
            self.assertFalse(rules[name], f'{name} must be inactive for payment-only data')

    def test_ieee_proxy_mapping(self):
        event = map_transaction({
            'TransactionID': '1', 'TransactionDT': '100', 'TransactionAmt': '50',
            'card1': '123', 'card2': '1', 'card3': '2', 'card5': '3',
            'addr1': '10', 'addr2': '20', 'dist1': '5', 'P_emaildomain': 'mailinator.com',
            'isFraud': '1'})
        self.assertTrue(ieee_schema_valid(event))
        self.assertTrue(event['disposable'])
        self.assertTrue(event['country_mismatch'])
        self.assertEqual(event['label'], 'abuse')
        event2 = map_transaction({
            'TransactionID': '2', 'TransactionDT': '101', 'TransactionAmt': '5',
            'card1': '123', 'isFraud': '0'})
        self.assertIsNone(event2['device'])
        self.assertFalse(event2['country_mismatch'])


class ReplayAndBaselineTests(unittest.TestCase):
    def test_replay_of_committed_fixture_is_deterministic(self):
        events_a, _ = load_dataset('ieee_cis_fixture', limit=250)
        events_b, _ = load_dataset('ieee_cis_fixture', limit=250)
        self.assertEqual(events_a, events_b)
        a = Detector(window_seconds=3600)
        b = Detector(window_seconds=3600)
        for event in events_a:
            a.process(dict(event))
        for event in events_b:
            b.process(dict(event))
        keys = [(e['id'], e['time'], e['score'], e['action'], e['bloom']['device_seen'],
                 e['dgim']['device_est']) for e in a.events]
        self.assertEqual(keys, [(e['id'], e['time'], e['score'], e['action'],
                                 e['bloom']['device_seen'], e['dgim']['device_est'])
                                for e in b.events])

    def test_stripe_fixture_replay_is_deterministic(self):
        events_a, _ = load_dataset('stripe_fixture')
        events_b, _ = load_dataset('stripe_fixture')
        self.assertEqual(events_a, events_b)
        a, b = Detector(window_seconds=3600), Detector(window_seconds=3600)
        for event in events_a:
            a.process(dict(event))
        for event in events_b:
            b.process(dict(event))
        self.assertEqual(a.state()['counts'], b.state()['counts'])
        self.assertEqual([e['score'] for e in a.events], [e['score'] for e in b.events])

    def test_baseline_json_matches_sklearn_transform(self):
        try:
            import numpy as np
            from sklearn.linear_model import LogisticRegression
        except ImportError:
            self.skipTest('scikit-learn not installed (runtime does not require it)')
        model = baseline.load_model()
        self.assertIsNotNone(model, 'models/baseline.json should be committed')
        rows = [
            {'amount_log': math.log1p(10), 'amount_ge_1000': 0, 'disposable': 0,
             'country_mismatch': 0, 'failed': 0, 'device_accounts': 1, 'card_attempts': 1,
             'device_trials': 0, 'ip_accounts': 1, 'dgim_device_est': 0,
             'bloom_device_seen': 0, 'bloom_card_seen': 0, 'rule_score': 0},
            {'amount_log': math.log1p(1500), 'amount_ge_1000': 1, 'disposable': 1,
             'country_mismatch': 1, 'failed': 1, 'device_accounts': 4, 'card_attempts': 5,
             'device_trials': 2, 'ip_accounts': 4, 'dgim_device_est': 6,
             'bloom_device_seen': 1, 'bloom_card_seen': 1, 'rule_score': 90},
        ]
        X = np.array([[row[name] for name in model['features']] for row in rows], dtype=float)
        Z = (X - np.array(model['mean'])) / np.array(model['scale'])
        clf = LogisticRegression()
        clf.coef_ = np.array([model['weights']], dtype=float)
        clf.intercept_ = np.array([model['intercept']], dtype=float)
        clf.classes_ = np.array([0, 1])
        expected = clf.predict_proba(Z)[:, 1]
        for row, probability in zip(rows, expected):
            self.assertAlmostEqual(baseline.probability(model, row), float(probability), places=9)
            self.assertAlmostEqual(baseline.score(model, row), float(probability) * 100, places=3)

    def test_apriori_auto_support_and_class_comparison(self):
        d = Detector()
        d.events = [{'items': ['x'], 'label': 'abuse'} for _ in range(3)]
        d.events += [{'items': ['y'], 'label': 'legitimate'} for _ in range(97)]
        self.assertAlmostEqual(d.suggested_min_support(d.events), 0.03, places=4)
        out = d.apriori_class_comparison()
        self.assertEqual(out['abuse_only']['analyzed'], 3)
        self.assertEqual(out['all_events']['analyzed'], 100)
        self.assertAlmostEqual(out['prevalence'], 0.03, places=4)

    def test_risk_bucket_and_threshold_mapping(self):
        d = Detector()
        self.assertEqual(d.risk_bucket(10), 'normal')
        self.assertEqual(d.risk_bucket(35), 'elevated')
        self.assertEqual(d.risk_bucket(65), 'highest')
        d.risk_cutoffs = {'elevated': 20, 'highest': 80}
        self.assertEqual(d.risk_bucket(20), 'elevated')
        self.assertEqual(d.risk_bucket(80), 'highest')


class EvaluationTests(unittest.TestCase):
    def test_temporal_split_and_pr_metrics(self):
        events = [
            {'time': 1, 'label': 'legitimate', 'score': 5, 'shadow': {'base_score': 5}},
            {'time': 2, 'label': 'abuse', 'score': 90, 'shadow': {'base_score': 80}},
            {'time': 3, 'label': 'legitimate', 'score': 10, 'shadow': {'base_score': 5}},
            {'time': 4, 'label': 'abuse', 'score': 70, 'shadow': {'base_score': 60}},
            {'time': 5, 'label': 'legitimate', 'score': 50, 'shadow': {'base_score': 5}},
        ]
        train, test = temporal_split(events, 0.6)
        self.assertEqual([e['time'] for e in train], [1, 2, 3])
        self.assertEqual([e['time'] for e in test], [4, 5])
        view = evaluate_view(test, 'full', 35)
        self.assertEqual(view['confusion']['tp'], 1)
        self.assertEqual(view['confusion']['fp'], 1)
        self.assertIsNotNone(view['pr_auc'])
        self.assertIsNotNone(view['recall_at_5pct_fpr'])

    def test_pr_curve_and_recall_at_fpr(self):
        curve = pr_curve([(90, True), (80, False), (70, True), (10, False)])
        self.assertGreater(curve['pr_auc'], 0)
        hit = recall_at_fpr([(90, True), (80, False), (70, True), (10, False)], 0.5)
        self.assertEqual(hit['recall'], 1.0)


if __name__ == '__main__':
    unittest.main()
