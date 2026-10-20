import ast
import json
import re
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from datasets import load_dataset
from engine import Detector
from evaluation import temporal_split

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / 'fixtures' / 'synthetic_manifest.json'
SCENARIOS = {
    'everyday_payments', 'card_testing', 'multi_account', 'repeated_trial',
    'slow_drip_device', 'slow_drip_card', 'mixed_stream', 'dgim_forgetting',
    'recidivist', 'free_forwarding', 'campus_wifi', 'travelers',
    'large_purchase', 'dgim_negative', 'stealth_abuse', 'bloom_collision',
}
DGIM_SCENARIOS = {'slow_drip_device', 'slow_drip_card', 'mixed_stream', 'dgim_forgetting'}


class SyntheticFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.events, cls.meta = load_dataset('synthetic')
        cls.detector = Detector()
        for event in cls.events:
            cls.detector.process(dict(event))
        cls.engine_events = cls.detector.events
        cls.by_id = {event['id']: event for event in cls.engine_events}
        cls.manifest = json.loads(MANIFEST_PATH.read_text(encoding='utf-8'))
        cls.instances = cls.manifest['instances']
        cls.scenario_by_id = {}
        for instance in cls.instances:
            for event_id in instance['event_ids']:
                cls.scenario_by_id[event_id] = instance['scenario']
        cls.train, cls.test = temporal_split(cls.engine_events, 0.7)

    def scenario_events(self, scenario):
        return [event for event in self.engine_events
                if self.scenario_by_id.get(event['id']) == scenario]

    def test_fixture_shape_and_manifest_contract(self):
        self.assertGreaterEqual(len(self.events), 2000)
        self.assertLessEqual(len(self.events), 3000)
        duration = max(row['ts'] for row in self.events) - min(row['ts'] for row in self.events)
        self.assertGreaterEqual(duration, 6 * 3600)
        self.assertLessEqual(duration, 12 * 3600)

        counts = self.manifest['counts']
        self.assertEqual(counts['events'], len(self.events))
        self.assertEqual(counts['abuse'] + counts['legitimate'] + counts['unknown'], len(self.events))
        self.assertLessEqual(abs(counts['abuse_rate'] - counts['abuse'] / len(self.events)), 0.0001)
        unknown_rate = counts['unknown'] / len(self.events)
        self.assertGreaterEqual(unknown_rate, 0.02)
        self.assertLessEqual(unknown_rate, 0.05)

        all_ids = [event_id for instance in self.instances
                   for event_id in instance['event_ids']]
        self.assertEqual(len(all_ids), len(set(all_ids)), 'manifest event ids must be unique')
        self.assertEqual(set(all_ids), set(self.by_id))
        self.assertEqual(set(self.manifest['scenario_counts']), SCENARIOS)

        scenario_ids = set()
        for instance in self.instances:
            self.assertIn(instance['scenario'], SCENARIOS)
            self.assertNotIn(instance['scenario_id'], scenario_ids)
            scenario_ids.add(instance['scenario_id'])
            self.assertTrue(instance['event_ids'])
            self.assertTrue(instance['description'])
            self.assertTrue(set(instance['expected_algorithms']) <=
                            {'exact', 'bloom', 'dgim', 'apriori', 'baseline'})
            self.assertIn(instance['expected_min_decision'], {'allow', 'review', 'block'})
            self.assertTrue(instance['entities'])

        bad = re.compile(r'(bot|test|abuse|fraud|legit|label)', re.IGNORECASE)
        for event in self.events:
            for key in ('account', 'device', 'ip', 'card', 'source_id'):
                self.assertIsNone(bad.search(event[key]), f'{key} contains a label word: {event[key]}')

    def test_every_scenario_appears_in_both_temporal_halves(self):
        train_by_scenario = Counter(self.scenario_by_id[event['id']] for event in self.train)
        test_by_scenario = Counter(self.scenario_by_id[event['id']] for event in self.test)
        for scenario in SCENARIOS:
            self.assertGreater(train_by_scenario[scenario], 0, f'{scenario} missing from train')
            self.assertGreater(test_by_scenario[scenario], 0, f'{scenario} missing from test')

        abuse_scenarios = {name for name, count in self.manifest['scenario_abuse'].items() if count}
        test_abuse_by_scenario = Counter(
            self.scenario_by_id[event['id']] for event in self.test if event['label'] == 'abuse')
        for scenario in abuse_scenarios:
            self.assertGreater(test_abuse_by_scenario[scenario], 0,
                               f'{scenario} has no abuse in the held-out test half')
        self.assertGreaterEqual(sum(test_abuse_by_scenario.values()), 25)

        print(f"\nTemporal split abuse: train={sum(e['label'] == 'abuse' for e in self.train)} "
              f"test={sum(e['label'] == 'abuse' for e in self.test)} "
              f"test_scenarios={dict(test_abuse_by_scenario)}")

    def test_dgim_points_are_confined_to_slow_scenarios(self):
        fired = [event for event in self.engine_events
                 if any('DGIM' in reason['reason'] for reason in event['reasons'])]
        self.assertGreaterEqual(len(fired), 20)
        self.assertTrue({self.scenario_by_id[event['id']] for event in fired} <= DGIM_SCENARIOS)
        for scenario in DGIM_SCENARIOS:
            self.assertTrue(any(self.scenario_by_id[event['id']] == scenario for event in fired),
                            f'expected DGIM contributions in {scenario}')

        for scenario in ('travelers', 'dgim_negative'):
            events = self.scenario_events(scenario)
            self.assertTrue(events)
            self.assertFalse(any(any('DGIM' in reason['reason'] for reason in event['reasons'])
                                 for event in events),
                             f'{scenario} must stay DGIM-free')

    def test_slow_drip_ablation_and_fast_burst_attribution(self):
        for scenario in ('slow_drip_device', 'slow_drip_card'):
            events = self.scenario_events(scenario)
            self.assertTrue(events)
            self.assertTrue(all(event['shadow']['base_score'] < 35 for event in events))
            flipped = [event for event in events if event['shadow']['flipped']]
            self.assertTrue(flipped, f'{scenario} should flip allow -> review')
            for event in flipped:
                reasons = [reason['reason'] for reason in event['reasons']]
                self.assertEqual(event['shadow']['base_action'], 'allow')
                self.assertIn(event['action'], ('review', 'block'))
                self.assertTrue(any('DGIM' in reason for reason in reasons))
                self.assertTrue(any('Bloom' in reason for reason in reasons))

        for scenario in ('card_testing', 'multi_account'):
            events = self.scenario_events(scenario)
            self.assertTrue(events)
            self.assertFalse(any(event['shadow']['flipped'] for event in events),
                             f'{scenario} should be decided by exact rules')

    def test_bloom_recidivist_and_collision_controls(self):
        recidivists = [instance for instance in self.instances
                       if instance['scenario'] == 'recidivist']
        self.assertTrue(recidivists)
        for instance in recidivists:
            clean_last = self.by_id[instance['event_ids'][-2]]
            returned = self.by_id[instance['event_ids'][-1]]
            self.assertEqual(clean_last['dgim']['device_est'], 0)
            self.assertTrue(returned['bloom']['device_seen'])
            self.assertFalse(any('DGIM' in reason['reason'] for reason in returned['reasons']))
            self.assertTrue(any('Bloom' in reason['reason'] for reason in returned['reasons']))
            self.assertEqual(returned['score'], returned['shadow']['base_score'] + 10)
            self.assertEqual(returned['action'], 'allow')

        collisions = [instance for instance in self.instances
                      if instance['scenario'] == 'bloom_collision']
        self.assertEqual(len(collisions), 2)
        for instance in collisions:
            event = self.by_id[instance['event_ids'][0]]
            self.assertTrue(event['bloom']['device_seen'])
            self.assertTrue(event['bloom']['card_seen'])
            self.assertFalse(event['bloom']['inserted'])
            self.assertEqual(event['score'], event['shadow']['base_score'])
            self.assertEqual(event['action'], 'allow')
            self.assertFalse(any('Bloom' in reason['reason'] or 'DGIM' in reason['reason']
                                 for reason in event['reasons']))
            self.assertNotIn(event['device'], self.detector.bloom_device.exact_members())
            self.assertNotIn(event['card'], self.detector.bloom_card.exact_members())
            self.assertEqual(len(instance['collision_positions']['device']), 3)
            self.assertEqual(len(instance['collision_positions']['card']), 3)
            self.assertTrue(all(self.detector.bloom_device.bits[pos]
                                for pos in instance['collision_positions']['device']))
            self.assertTrue(all(self.detector.bloom_card.bits[pos]
                                for pos in instance['collision_positions']['card']))

    def test_forgetting_returns_to_zero(self):
        forgetting = [instance for instance in self.instances
                      if instance['scenario'] == 'dgim_forgetting']
        self.assertTrue(forgetting)
        for instance in forgetting:
            device = instance['entities']['devices'][0]
            self.assertEqual(self.detector.dgim_state(device)['estimate'], 0)
            self.assertEqual(self.by_id[instance['event_ids'][-1]]['dgim']['device_est'], 0)
            self.assertTrue(any(any('DGIM' in reason['reason'] for reason in event['reasons'])
                                for event in self.scenario_events('dgim_forgetting')))

    def test_apriori_finds_documented_combinations(self):
        result = self.detector.apriori(min_support=0.01)
        itemsets = {frozenset(item['items']) for item in result['itemsets']}
        self.assertIn(frozenset(('card_velocity', 'failed_payment', 'country_mismatch')), itemsets)
        self.assertIn(frozenset(('device_reuse', 'repeat_trial', 'temporary_email')), itemsets)
        self.assertIn(frozenset(('country_mismatch',)), itemsets)
        singles = {next(iter(item)) for item in itemsets if len(item) == 1}
        self.assertIn('temporary_email', singles)
        self.assertIn('failed_payment', singles)
        self.assertTrue(result['rules'])
        self.assertTrue(any(rule['confidence'] < 1.0 for rule in result['rules']))
        # The unchanged engine adds failed_payment only to payments and
        # temporary_email only to lifecycle events, so that pair cannot share a
        # basket. Both signals are still frequent individually.

    def test_scoring_does_not_read_labels_statically_or_behaviourally(self):
        engine_source = (ROOT / 'engine.py').read_text(encoding='utf-8')
        tree = ast.parse(engine_source)
        functions = {node.name: node for node in ast.walk(tree)
                     if isinstance(node, ast.FunctionDef)}
        suspicious_source = ast.get_source_segment(engine_source, functions['suspicious_bit'])
        self.assertNotIn("'label'", suspicious_source)
        process_source = ast.get_source_segment(engine_source, functions['process'])
        marker = 'e.update(reasons=reasons, items=items, score=full_score)'
        self.assertIn(marker, process_source)
        self.assertNotIn("'label'", process_source.split(marker)[0])
        baseline_source = (ROOT / 'baseline.py').read_text(encoding='utf-8')
        self.assertNotIn(".get('label')", baseline_source)
        self.assertNotIn("['label']", baseline_source)

        event = {'type': 'payment', 'account': 'acct-a', 'device': 'dev-a',
                 'ip': 'ip-a', 'card': 'card-a', 'amount': 10, 'failed': True}
        a, b = Detector(), Detector()
        first = a.process(dict(event, label='abuse'))
        second = b.process(dict(event, label='legitimate'))
        for key in ('score', 'action', 'bloom', 'dgim', 'baseline', 'items'):
            self.assertEqual(first[key], second[key])

    @unittest.skipUnless(sys.executable, 'Python is required to regenerate the fixture')
    def test_regeneration_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            events_path = Path(tmp) / 'synthetic_events.jsonl'
            manifest_path = Path(tmp) / 'synthetic_manifest.json'
            result = subprocess.run(
                [sys.executable, 'tools/make_fixtures.py',
                 '--out', str(events_path), '--manifest-out', str(manifest_path)],
                cwd=ROOT, capture_output=True, text=True, timeout=300,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(events_path.read_bytes(),
                             (ROOT / 'fixtures' / 'synthetic_events.jsonl').read_bytes())
            self.assertEqual(manifest_path.read_bytes(), MANIFEST_PATH.read_bytes())


if __name__ == '__main__':
    unittest.main()
