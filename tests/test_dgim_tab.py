import hashlib
import json
import shutil
import subprocess
import unittest
from pathlib import Path

from engine import Detector, dgim_status


ROOT = Path(__file__).resolve().parents[1]

# Captured from the pre-redesign engine for the same deterministic replays.
GOLDEN_DIGESTS = {
    'normal': '60694c7788c8d89b5fc8eeebd97805c598346fd77fc031406ca13f1f94de9fe3',
    'card_testing': 'b33a79387af0a08f8add4490ccc2439c3a4195f5f0dcbb814fbcb9e03d0c750a',
    'trial_abuse': 'f51c3d545fafa3da9229a52dcb9fe04b07e5b70649b3fa2b6879c4822c51c9e5',
    'multi_account_device': '56993f39796da6dce5735cabc8146abbf4941c3579cbc5dbb2ceb66a8a74a71e',
    'mixed': 'adacd48bcad997839cc0631058faf018339b7b277cbe729fbf356ecba622b418',
}


def event(**changes):
    row = dict(type='payment', account='acct', device='device-a', ip='ip-a',
               card='card-a', amount=10)
    row.update(changes)
    return row


def replay(name):
    detector = Detector()
    for row in detector.scenario(name):
        detector.process(row)
    return detector


class DgimRankingTests(unittest.TestCase):
    def test_status_boundaries(self):
        self.assertEqual(dgim_status(5, 5), 'burst')
        self.assertEqual(dgim_status(4, 5), 'watch')
        self.assertEqual(dgim_status(1, 5), 'watch')
        self.assertEqual(dgim_status(0, 5), 'normal')

    def test_ranking_is_sorted_by_estimate_and_badges_match(self):
        detector = replay('card_testing')
        state = detector.state()['dgim']
        estimates = [row['estimate'] for row in state['ranked']]
        self.assertEqual(estimates, sorted(estimates, reverse=True))
        self.assertTrue(all(row['status'] == dgim_status(row['estimate'], detector.DGIM_THRESHOLD)
                            for row in state['ranked']))
        self.assertEqual(state['ranked'][0]['status'], 'burst')
        self.assertEqual(state['entity'], state['ranked'][0]['name'])
        self.assertEqual(state['kind'], state['ranked'][0]['kind'])

    def test_default_when_all_entities_are_normal_is_the_busiest(self):
        detector = Detector()
        for i in range(3):
            detector.process(event(device='busy-device', card='busy-card',
                                   account=f'a{i}', step=1))
        detector.process(event(device='busy-device', card='other-card',
                               account='b', step=1))
        detector.process(event(device='quiet-device', card='quiet-card',
                               account='c', step=1))
        state = detector.state()['dgim']
        self.assertTrue(state['all_normal'])
        self.assertEqual(state['entity'], 'busy-device')
        self.assertEqual(state['kind'], 'device')
        self.assertEqual(state['recent'][0]['score'], 0)


class DgimEntityViewTests(unittest.TestCase):
    def test_stream_cells_match_exact_counter_and_estimate_bound(self):
        detector = replay('card_testing')
        for kind, name in [('device', 'r1-bot'), ('card', 'r1-tested-card')]:
            view = detector.dgim_state(name, kind)
            bits = sum(row['bit'] for row in view['recent'])
            self.assertEqual(bits, view['exact'])
            self.assertEqual(len(view['recent']), 8)
            self.assertLessEqual(abs(view['estimate'] - view['exact']),
                                 view['exact'] * 0.5 + 1)
            self.assertTrue(view['view_buckets'])
            self.assertEqual(view['first_fired']['id'], 'evt_00006')

    def test_latest_result_is_the_stored_dgim_contribution(self):
        detector = replay('card_testing')
        latest = detector.dgim_state()['latest']
        self.assertTrue(latest['fired'])
        self.assertEqual(latest['dgim_points'], 10)
        self.assertEqual(latest['score_after_dgim'],
                         min(100, latest['score_before_dgim'] + 10))

    def test_cancel_events_are_marked_not_scored(self):
        detector = replay('trial_abuse')
        view = detector.dgim_state('r1-reused-laptop', 'device')
        cancels = [row for row in view['recent'] if row['cancel']]
        self.assertTrue(cancels)
        self.assertTrue(all(not row['fired'] for row in cancels))
        self.assertTrue(all(row['action'] == 'allow' for row in cancels))

    def test_empty_entity_view(self):
        detector = Detector()
        view = detector.dgim_state('missing-device', 'device')
        self.assertEqual(view['estimate'], 0)
        self.assertEqual(view['exact'], 0)
        self.assertEqual(view['recent'], [])
        self.assertIsNone(view['latest'])
        self.assertEqual(detector.state()['dgim']['recent'], [])

    def test_changing_n_and_threshold_does_not_rewrite_stored_scores(self):
        detector = replay('card_testing')
        stored = [(row['id'], row['score'], row['action']) for row in detector.events]
        before = detector.dgim_state('r1-bot', 'device')
        self.assertEqual(before['ranked'][0]['status'], 'burst')

        detector.DGIM_THRESHOLD = 7
        detector.dgim_window = 30
        detector.DGIM_WINDOW = 30
        for stream in list(detector.dgim_device_streams.values()) + list(detector.dgim_card_streams.values()):
            stream.window_size = 30
        after = detector.dgim_state('r1-bot', 'device')
        self.assertEqual(after['ranked'][0]['status'], 'watch')
        self.assertEqual(stored, [(row['id'], row['score'], row['action'])
                                  for row in detector.events])


class DgimGoldenReplayTests(unittest.TestCase):
    def test_scoring_and_dgim_outputs_are_byte_for_byte_unchanged(self):
        for name, expected in GOLDEN_DIGESTS.items():
            detector = replay(name)
            rows = [(row['id'], row['type'], row['score'], row['action'],
                     row['dgim']['bit'], row['dgim']['device_est'],
                     row['dgim']['device_exact'], row['dgim']['burst'],
                     tuple(row.get('items', []))) for row in detector.events]
            blob = json.dumps(rows, separators=(',', ':')).encode()
            self.assertEqual(hashlib.sha256(blob).hexdigest(), expected, name)


class DgimViewHelperTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js is required for the DGIM view tests')
    def test_plain_language_view_helpers(self):
        result = subprocess.run(
            ['node', '--test', 'tests/dgim_view.test.js'],
            cwd=ROOT, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
