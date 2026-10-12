import unittest
from engine import Detector, CountMinSketch


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
        d.process(self.event())  # t=1
        d.process(self.event())  # t=2
        self.assertEqual(len(d.device_accounts['d']), 1)
        d.advance(59)  # t=61; first expires, second remains
        self.assertEqual(len(d.window), 1)
        self.assertEqual(d.device_accounts['d']['a'], 1)
        d.advance(1)
        self.assertEqual(len(d.window), 0)
        self.assertEqual(len(d.device_accounts), 0)
        self.assertEqual(len(d.card_payments), 0)
        self.assertEqual(d.sketch.estimate('d'), 2)  # lifetime sketch unaffected

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

    def test_sketch_never_undercounts(self):
        cms = CountMinSketch(width=4, depth=3)
        exact = {}
        for i in range(300):
            key = f'device-{i%37}'
            exact[key] = exact.get(key, 0)+1
            cms.add(key)
        self.assertTrue(all(cms.estimate(k) >= count for k,count in exact.items()))
        self.assertTrue(any(cms.estimate(k) > count for k,count in exact.items()))

    def test_apriori_known_baskets(self):
        d = Detector()
        d.events = [{'items': b} for b in [['a','b'], ['a','b'], ['a'], ['c']]]
        result = {tuple(p['items']): p['support'] for p in d.apriori(.5)}
        self.assertEqual(result, {('a',):.75, ('b',):.5, ('a','b'):.5})

    def test_invalid_inputs_do_not_mutate(self):
        d = Detector()
        for changes in [dict(amount=float('nan')),dict(step=-1),dict(type='bad'),dict(disposable='false'),dict(account='')]:
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
        self.assertEqual(d.sketch.estimate('d'), 0)


if __name__ == '__main__':
    unittest.main()
