import unittest
from engine import Detector, suspicious_bit
from algorithms.bloom_filter import BloomFilter
from algorithms.dgim import DGIM


class DetectorTests(unittest.TestCase):
    def event(self, **changes):
        e = dict(type='payment', account='a', device='d', ip='i', card='c', amount=10)
        e.update(changes)
        return e

    def test_card_testing_and_no_truth_leakage(self):
        d = Detector()
        rows = [d.process(e) for e in d.scenario('card_testing')]
        self.assertEqual(rows[0]['action'], 'review')
        self.assertEqual(rows[3]['action'], 'block')
        a, b = Detector(), Detector()
        self.assertEqual(a.process(self.event(label='abuse'))['score'], b.process(self.event(label='legitimate'))['score'])

    def test_shared_ip_is_not_enough(self):
        d = Detector()
        rows = [d.process(e) for e in d.scenario('shared_ip')]
        self.assertTrue(all(e['action'] == 'allow' for e in rows))
        self.assertEqual(rows[-1]['score'], 10)

    def test_window_boundary_and_distinct_reference_counts(self):
        d = Detector()
        d.process(self.event(failed=True))  # t=1 (suspicious -> Bloom insert)
        d.process(self.event(failed=True))  # t=2
        self.assertEqual(len(d.device_accounts['d']), 1)
        d.advance(59)  # t=61; first expires, second remains
        self.assertEqual(len(d.window), 1)
        self.assertEqual(d.device_accounts['d']['a'], 1)
        d.advance(1)
        self.assertEqual(len(d.window), 0)
        self.assertEqual(len(d.device_accounts), 0)
        self.assertEqual(len(d.card_payments), 0)
        # Bloom is session-lifetime: bits persist after exact-window expiry.
        self.assertEqual(d.bloom_device.check('d', count_query=False), 'possibly_present')

    def test_trial_abuse_and_replay(self):
        d = Detector()
        for e in d.scenario('mixed'):
            d.process(e)
        self.assertGreater(d.metrics()['fp'], 0)
        self.assertGreater(d.metrics()['fn'], 0)
        self.assertTrue(any(e['type'] == 'trial' and e['action'] == 'block' for e in d.events))
        old = [e['action'] for e in d.events]
        d.review_threshold = 90
        d.block_threshold = 99
        self.assertEqual([e['action'] for e in d.events], old)
        self.assertEqual(d.metrics()['fp'], 0)

    def test_cancel_stays_zero(self):
        d = Detector()
        e = d.process(self.event(type='signup', account='a', device='d', ip='i', card='c'))
        self.assertEqual(e['action'], 'allow')
        c = d.process(dict(type='cancel', account='a', device='d', ip='i', card='c', label='unknown'))
        self.assertEqual(c['score'], 0)
        self.assertEqual(c['action'], 'allow')

    def test_apriori_known_baskets(self):
        d = Detector()
        d.events = [{'items': b} for b in [['a', 'b'], ['a', 'b'], ['a'], ['c']]]
        out = d.apriori(.5)
        result = {tuple(p['items']): p['support'] for p in out['itemsets']}
        self.assertEqual(result, {('a',): .75, ('b',): .5, ('a', 'b'): .5})
        self.assertEqual(out['analyzed'], 4)

    def test_apriori_pruning_confidence_lift(self):
        d = Detector()
        # a,b co-occur; c is rare -> pruned at 50%: {a,c} requires {c} frequent (fails).
        d.events = [{'items': b} for b in [['a', 'b'], ['a', 'b'], ['a'], ['a']]]
        out = d.apriori(.5)
        itemsets = {tuple(p['items']) for p in out['itemsets']}
        self.assertIn(('a', 'b'), itemsets)
        rule = [r for r in out['rules'] if r['antecedent'] == ['a'] and r['consequent'] == ['b']]
        self.assertTrue(rule)
        # support(a,b)=0.5, support(a)=1.0 -> conf 0.5; support(b)=0.5 -> lift 1.0
        self.assertAlmostEqual(rule[0]['confidence'], 0.5)
        self.assertAlmostEqual(rule[0]['lift'], 1.0)
        # Empty dataset
        d2 = Detector()
        out2 = d2.apriori()
        self.assertEqual(out2['itemsets'], [])
        self.assertEqual(out2['rules'], [])
        self.assertEqual(out2['analyzed'], 0)

    def test_apriori_support_boundary(self):
        d = Detector()
        d.events = [{'items': ['x']}, {'items': ['y']}]
        self.assertEqual(len(d.apriori(0.5)['itemsets']), 2)  # ceil(2*0.5)=1
        self.assertEqual(d.apriori(1.0)['itemsets'], [])  # ceil=2, each count 1
        self.assertEqual(len(d.apriori(0.01)['itemsets']), 2)

    def test_invalid_inputs_do_not_mutate(self):
        d = Detector()
        for changes in [dict(amount=float('nan')), dict(step=-1), dict(type='bad'), dict(disposable='false'), dict(account='')]:
            with self.assertRaises(ValueError):
                d.process(self.event(**changes))
        self.assertEqual(len(d.events), 0)
        self.assertEqual(len(d.window), 0)
        self.assertEqual(d.clock, 0)

    def test_unknown_metrics_and_reset(self):
        d = Detector()
        d.process(self.event())
        self.assertIsNone(d.metrics()['precision'])
        self.assertEqual(d.metrics()['labeled'], 0)
        d.reset()
        self.assertEqual(d.state()['total'], 0)
        self.assertEqual(d.bloom_device.check('d', count_query=False), 'definitely_absent')
        self.assertEqual(d.dgim_state('d')['estimate'], 0)

    def test_label_never_influences_algorithms(self):
        a, b = Detector(), Detector()
        ea = a.process(self.event(label='abuse', device='x', card='y'))
        eb = b.process(self.event(label='legitimate', device='x', card='y'))
        self.assertEqual(ea['score'], eb['score'])
        self.assertEqual(ea['bloom'], eb['bloom'])
        self.assertEqual(ea['dgim']['bit'], eb['dgim']['bit'])
        self.assertEqual(suspicious_bit({'failed': False, 'disposable': False, 'country_mismatch': False}), 0)
        self.assertEqual(suspicious_bit({'failed': True, 'disposable': False, 'country_mismatch': False}), 1)

    def test_bloom_query_before_insert(self):
        d = Detector()
        first = d.process(self.event(device='fresh-dev', card='fresh-card', failed=True))
        self.assertFalse(first['bloom']['device_seen'])
        self.assertFalse(first['bloom']['card_seen'])
        self.assertTrue(first['bloom']['inserted'])  # suspicious -> inserted
        second = d.process(self.event(device='fresh-dev', card='fresh-card', account='b', failed=True))
        self.assertTrue(second['bloom']['device_seen'])
        self.assertTrue(second['bloom']['card_seen'])

    def test_bloom_benign_not_inserted(self):
        # High-risk-only insertion: benign allow events leave no Bloom trace.
        d = Detector()
        e = d.process(self.event(device='benign-dev', card='benign-card'))
        self.assertEqual(e['action'], 'allow')
        self.assertFalse(e['bloom']['inserted'])
        self.assertEqual(d.bloom_device.check('benign-dev', count_query=False), 'definitely_absent')

    def test_bloom_alone_cannot_block(self):
        d = Detector()
        # Repeat a benign identifier with no other signals: Bloom hits but no
        # corroboration-heavy score; stays allow (max +20 < review 35).
        for i in range(6):
            e = d.process(self.event(device='family-tablet', card=f'card-{i}', account=f'u{i}'))
            self.assertEqual(e['action'], 'allow')

    def test_dgim_burst_needs_corroboration(self):
        d = Detector()
        rows = [d.process(e) for e in d.scenario('card_testing')]
        bursts = [r for r in rows if 'dgim_device_burst' in r['items']]
        self.assertTrue(bursts)  # burst appears after enough 1s
        # Normal traffic: no burst, estimate ~0.
        n = Detector()
        nrows = [n.process(e) for e in n.scenario('normal')]
        self.assertTrue(all('dgim_device_burst' not in r['items'] for r in nrows))
        self.assertEqual(nrows[-1]['dgim']['device_est'], 0)

    def test_low_slow_shadow_flips(self):
        # Low-and-slow: exact 60s window forgets each 90s-spaced event, but
        # persistent Bloom + count-based DGIM lift later events allow->review.
        d = Detector()
        rows = [d.process(e) for e in d.scenario('low_slow')]
        self.assertTrue(all(r['shadow']['base_action'] == 'allow' for r in rows))
        flipped = [r for r in rows if r['shadow']['flipped']]
        self.assertGreaterEqual(len(flipped), 1)
        s = d.shadow_summary()
        self.assertGreaterEqual(s['flipped'], 1)
        self.assertGreaterEqual(s['allow_to_flagged'], 1)
        # Honest ablation on bursty traffic: exact rules already decide.
        b = Detector()
        for e in b.scenario('normal'):
            b.process(e)
        self.assertEqual(b.shadow_summary()['flipped'], 0)

    def test_apriori_excludes_aux_indicators(self):
        d = Detector()
        for e in d.scenario('card_testing'):
            d.process(e)
        out = d.apriori()
        for p in out['itemsets']:
            self.assertFalse(set(p['items']) & {'bloom_device_seen', 'bloom_card_seen', 'dgim_device_burst'})
        # Domain signals surface instead.
        flat = {i for p in out['itemsets'] for i in p['items']}
        self.assertIn('failed_payment', flat)

    def test_apriori_label_eval_and_time_split(self):
        d = Detector()
        for e in d.scenario('mixed'):
            d.process(e)
        ev = d.apriori(include_labels=True)
        label_rules = [r for r in ev['rules'] if 'label:abuse' in r['consequent']]
        self.assertTrue(label_rules)
        self.assertTrue(all(r['lift'] is not None and r['lift'] > 0 for r in label_rules))
        ts = d.apriori_time_split()
        self.assertGreater(ts['train_n'], 0)
        self.assertGreater(ts['test_n'], 0)
        self.assertTrue(ts['checked'])
        # Scoring stayed label-free: labels never appear in items.
        self.assertTrue(all('label:abuse' not in e.get('items', []) for e in d.events))

    def test_dgim_long_stream_tradeoff(self):
        from algorithms.dgim import long_stream_demo
        demo = long_stream_demo(n=1000, window=100, density=0.3, seed=42)
        self.assertEqual(len(demo['samples']), 50)
        self.assertLessEqual(demo['summary']['max_rel_error'], 0.6)
        # Memory: buckets O(log n), far below the exact 100-bit window.
        self.assertLessEqual(demo['summary']['max_buckets'], 16)
        self.assertEqual(demo['summary']['exact_storage'], 100)

    def test_state_contains_new_algorithm_state(self):
        d = Detector()
        for e in d.scenario('card_testing'):
            d.process(e)
        s = d.state()
        self.assertIn('bloom', s)
        self.assertIn('dgim', s)
        self.assertIn('rules', s)
        self.assertNotIn('sketch', s)
        self.assertGreaterEqual(s['bloom']['device']['insertions'], 8)

    def test_reset_clears_all_three_algorithms(self):
        d = Detector()
        for e in d.scenario('card_testing'):
            d.process(e)
        d.reset()
        s = d.state()
        self.assertEqual(s['total'], 0)
        self.assertEqual(s['patterns'], [])
        self.assertEqual(s['rules'], [])
        self.assertEqual(s['bloom']['device']['insertions'], 0)
        self.assertEqual(s['dgim']['estimate'], 0)


class BloomFilterTests(unittest.TestCase):
    def test_empty_reports_absent(self):
        bf = BloomFilter(size=128, num_hashes=3)
        self.assertEqual(bf.check('anything'), 'definitely_absent')

    def test_insert_then_query(self):
        bf = BloomFilter(size=128, num_hashes=3)
        bf.add('dev-1')
        self.assertEqual(bf.check('dev-1'), 'possibly_present')

    def test_deterministic_positions(self):
        a, b = BloomFilter(size=256, num_hashes=3), BloomFilter(size=256, num_hashes=3)
        self.assertEqual(a.positions('key-x'), b.positions('key-x'))
        self.assertEqual(len(set(a.positions('key-x'))), len(a.positions('key-x')) if len(set(a.positions('key-x'))) else 0)

    def test_occupancy_and_fpp(self):
        bf = BloomFilter(size=512, num_hashes=3)
        self.assertEqual(bf.theoretical_fpp(), 0.0)
        for i in range(20):
            bf.add(f'k-{i}')
        self.assertGreater(bf.bits_set, 0)
        self.assertGreater(bf.occupancy, 0)
        # Standard formula check against manual computation.
        import math as m
        expected = round((1.0 - m.exp(-3 * 20 / 512)) ** 3, 4)
        self.assertAlmostEqual(bf.theoretical_fpp(), expected)
        self.assertGreaterEqual(bf.theoretical_fpp(), 0.0)

    def test_small_filter_can_collide(self):
        bf = BloomFilter(size=16, num_hashes=3)
        for i in range(30):
            bf.add(f'item-{i}')
        # With 30 inserts into 16 bits, some unseen key is likely positive;
        # either outcome is acceptable, but stats must be consistent.
        r = bf.check('definitely-new-zzz')
        self.assertIn(r, ('possibly_present', 'definitely_absent'))
        self.assertGreaterEqual(bf.queries, 1)

    def test_reset(self):
        bf = BloomFilter(size=64, num_hashes=2)
        bf.add('a')
        bf.reset()
        self.assertEqual(bf.check('a', count_query=False), 'definitely_absent')
        self.assertEqual(bf.insertions, 0)

    def test_positive_is_not_proof(self):
        # A positive is reported as possibly_present, never as confirmed fraud.
        bf = BloomFilter(size=64, num_hashes=2)
        bf.add('seen')
        self.assertEqual(bf.check('seen'), 'possibly_present')
        self.assertNotIn('confirmed', bf.check('seen'))


class DGIMTests(unittest.TestCase):
    def test_empty_stream(self):
        d = DGIM(window_size=8)
        self.assertEqual(d.estimate(), 0)
        self.assertEqual(d.exact_count(), 0)
        self.assertEqual(d.buckets, [])

    def test_all_zero_stream(self):
        d = DGIM(window_size=8)
        for _ in range(10):
            d.update(0)
        self.assertEqual(d.estimate(), 0)
        self.assertEqual(d.exact_count(), 0)

    def test_all_one_stream_within_bound(self):
        d = DGIM(window_size=16)
        for _ in range(8):
            d.update(1)
        est = d.estimate()
        # Classical DGIM guarantees relative error <= 50% for non-zero counts.
        self.assertGreaterEqual(est, 8 * 0.5)
        self.assertLessEqual(est, 8 * 1.5 + 1)

    def test_mixed_stream_error_bound(self):
        bits = [1, 0, 1, 1, 0, 0, 1, 0, 1, 1, 0, 1]
        d = DGIM(window_size=12)
        for b in bits:
            d.update(b)
        exact = sum(bits[-12:])
        est = d.estimate()
        if exact == 0:
            self.assertEqual(est, 0)
        else:
            self.assertGreaterEqual(est, exact * 0.5 - 1)
            self.assertLessEqual(est, exact * 1.5 + 1)

    def test_power_of_two_and_merge_invariant(self):
        d = DGIM(window_size=64)
        for _ in range(20):
            d.update(1)
        inv = d.check_invariants()
        self.assertTrue(inv['powers_of_two'])
        self.assertTrue(inv['max_per_size_ok'])
        for b in d.buckets:
            self.assertEqual(b['size'] & (b['size'] - 1), 0)

    def test_expiration(self):
        d = DGIM(window_size=5)
        for b in [1, 1, 1, 1, 1]:
            d.update(b)
        for b in [0, 0, 0, 0, 0]:
            d.update(b)
        self.assertEqual(d.exact_count(), 0)
        self.assertEqual(d.estimate(), 0)
        self.assertTrue(d.check_invariants()['no_expired'])

    def test_boundary_window(self):
        d = DGIM(window_size=4)
        for b in [1, 1, 1, 1]:
            d.update(b)
        self.assertGreaterEqual(d.estimate(), 1)
        d.update(0)  # position 5; oldest 1 expires
        self.assertEqual(d.exact_count(), 3)

    def test_query_does_not_mutate(self):
        d = DGIM(window_size=8)
        for b in [1, 0, 1, 1]:
            d.update(b)
        before = d.snapshot()
        d.estimate()
        d.estimate()
        self.assertEqual(before, d.snapshot())

    def test_reset(self):
        d = DGIM(window_size=8)
        for _ in range(5):
            d.update(1)
        d.reset()
        self.assertEqual(d.estimate(), 0)
        self.assertEqual(d.buckets, [])
        self.assertEqual(d.pos, 0)


if __name__ == '__main__':
    unittest.main()
