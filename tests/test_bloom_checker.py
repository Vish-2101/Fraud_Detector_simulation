import shutil
import subprocess
import unittest
from pathlib import Path

from engine import Detector


ROOT = Path(__file__).resolve().parents[1]


def replay(name):
    detector = Detector()
    for row in detector.scenario(name):
        detector.process(row)
    return detector


class BloomInspectionTests(unittest.TestCase):
    def test_inserted_device_is_true_positive_with_all_bits_lit(self):
        detector = replay('card_testing')
        info = detector.bloom_inspection('device', '  r1-bot  ')
        self.assertEqual(info['outcome'], 'true_positive')
        self.assertEqual(info['key'], 'r1-bot')
        self.assertEqual(info['inserted_event']['event_id'], 'evt_00001')
        self.assertTrue(info['inserted_event']['action'])
        self.assertGreaterEqual(info['inserted_event']['score'], 0)
        self.assertEqual(len(info['positions']), detector.bloom_device.num_hashes)
        self.assertTrue(all(detector.bloom_device.bits[p]
                            for p in info['positions']))

    def test_never_inserted_device_is_definitely_absent(self):
        detector = Detector(bloom_size=4096)
        detector.bloom_device.add('known-device', event_id='evt_00001')
        info = detector.bloom_inspection('device', 'never-inserted-device')
        self.assertEqual(info['outcome'], 'definitely_absent')
        self.assertIsNone(info['inserted_event'])
        self.assertEqual(info['bit_sources'], [])

    def test_tiny_filter_collision_is_false_positive_with_sources(self):
        detector = Detector(bloom_size=16, bloom_hashes=3)
        info = None
        for i in range(300):
            detector.bloom_device.add(f'inserted-{i}',
                                      event_id=f'evt_{i:05}',
                                      action='review', score=40)
            candidate = f'probe-{i}'
            if detector.bloom_device.check(candidate, count_query=False) == 'possibly_present':
                info = detector.bloom_inspection('device', candidate)
                break
        self.assertIsNotNone(info, 'expected a forced collision in the tiny filter')
        self.assertEqual(info['outcome'], 'false_positive')
        self.assertEqual(len(info['bit_sources']), detector.bloom_device.num_hashes)
        for source in info['bit_sources']:
            self.assertTrue(source['identifiers'])
            self.assertTrue(all(key in detector.bloom_device.exact_members()
                                for key in source['identifiers']))


class BloomHintTests(unittest.TestCase):
    def test_account_queried_as_device_is_absent_with_account_hint(self):
        detector = Detector()
        event = detector.process({
            'type': 'payment', 'account': 'acct-42', 'device': 'dev-42',
            'card': 'card-42', 'ip': 'ip-42', 'amount': 10, 'failed': True,
        })
        info = detector.bloom_inspection('device', 'acct-42')
        self.assertEqual(info['outcome'], 'definitely_absent')
        self.assertEqual(info['hint']['kind'], 'account')
        self.assertIn('account ID', info['hint']['message'])
        self.assertEqual(info['hint']['event_id'], event['id'])
        self.assertEqual(
            [choice['kind'] for choice in info['hint']['choices']],
            ['device', 'card'],
        )
        self.assertEqual(info['hint']['choices'][0]['key'], 'dev-42')
        self.assertEqual(info['hint']['choices'][1]['key'], 'card-42')

    def test_card_queried_as_device_offers_kind_switch(self):
        detector = Detector()
        detector.process({
            'type': 'payment', 'account': 'acct-7', 'device': 'dev-7',
            'card': 'card-7', 'ip': 'ip-7', 'amount': 10, 'failed': True,
        })
        info = detector.bloom_inspection('device', 'card-7')
        self.assertEqual(info['outcome'], 'definitely_absent')
        self.assertEqual(info['hint']['kind'], 'card')
        self.assertEqual(info['hint']['message'],
                         'This is a card ID; switch Query kind to card.')
        self.assertEqual(info['hint']['choices'][0]['kind'], 'card')

    def test_hint_is_not_added_for_an_inserted_identifier(self):
        detector = Detector()
        detector.process({
            'type': 'payment', 'account': 'acct-9', 'device': 'dev-9',
            'card': 'card-9', 'ip': 'ip-9', 'amount': 10, 'failed': True,
        })
        self.assertIsNone(detector.bloom_inspection('device', 'dev-9')['hint'])


class BloomImmutabilityTests(unittest.TestCase):
    def test_inspection_does_not_mutate_filter_or_later_scoring(self):
        first = {
            'type': 'payment', 'account': 'a', 'device': 'query-dev',
            'card': 'query-card', 'ip': 'ip-a', 'amount': 10, 'failed': True,
        }
        second = {
            'type': 'payment', 'account': 'b', 'device': 'query-dev',
            'card': 'query-card', 'ip': 'ip-b', 'amount': 20, 'failed': True,
        }
        control, tested = Detector(), Detector()
        control.process(dict(first))
        tested.process(dict(first))
        before = tested.bloom_state()
        tested.bloom_inspection('device', 'query-dev')
        tested.bloom_inspection('device', 'never-inserted-device')
        tested.bloom_inspection('device', 'a')
        self.assertEqual(before, tested.bloom_state())

        expected = control.process(dict(second))
        actual = tested.process(dict(second))
        self.assertEqual(actual['score'], expected['score'])
        self.assertEqual(actual['action'], expected['action'])
        self.assertEqual(actual['bloom'], expected['bloom'])


class BloomViewNodeTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js is required for the Bloom view tests')
    def test_no_ui_path_renders_unknown_result(self):
        result = subprocess.run(
            ['node', '--test', 'tests/bloom_view.test.js'],
            cwd=ROOT, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
