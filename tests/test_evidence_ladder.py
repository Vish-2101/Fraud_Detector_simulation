import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

import baseline
import server
from datasets import fixture_sha256, load_dataset
from engine import Detector
from evaluation import (LADDER_ROWS, bootstrap_ladder, evaluation_report,
                        ladder_rows, temporal_split)
from tools import train_ladder

ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / 'models'


def event(ts, label, score=0, amount=10, base=None, ladder=None):
    return {
        'id': f'e{ts}',
        'time': ts,
        'ts': ts,
        'label': label,
        'score': score,
        'amount': amount,
        'shadow': {'base_score': score if base is None else base},
        'ladder': ladder,
    }


class FeatureSetTests(unittest.TestCase):
    def test_raw_features_exclude_engine_outputs(self):
        for name in ('rule_score', 'bloom_device_seen', 'bloom_card_seen',
                     'dgim_device_est', 'reason', 'reasons'):
            self.assertNotIn(name, baseline.RAW_FEATURES)
        self.assertNotIn('rule_score', baseline.RAW_FEATURES)
        self.assertEqual(baseline.RAW_FEATURES,
                         [name for name in baseline.RAW_FEATURES])
        self.assertEqual(baseline.BD_FEATURES[:len(baseline.RAW_FEATURES)],
                         baseline.RAW_FEATURES)
        self.assertEqual(baseline.BD_FEATURES[-3:],
                         ['bloom_device_seen', 'bloom_card_seen', 'dgim_device_est'])

    def test_engine_records_three_label_free_vectors(self):
        detector = Detector()
        scored = detector.process({
            'type': 'payment', 'account': 'a1', 'device': 'd1', 'ip': 'ip1',
            'card': 'c1', 'amount': 1500, 'failed': True, 'country_mismatch': True,
            'label': 'abuse'})
        vectors = scored['model_features']
        self.assertEqual(set(vectors), {'raw', 'raw_bd', 'all'})
        self.assertEqual(list(vectors['raw']), baseline.RAW_FEATURES)
        self.assertEqual(list(vectors['raw_bd']), baseline.BD_FEATURES)
        self.assertEqual(list(vectors['all']), baseline.FEATURES)
        self.assertEqual(vectors['raw']['amount'], 1500.0)
        self.assertEqual(vectors['raw']['amount_ge_1000'], 1.0)
        self.assertEqual(vectors['raw']['type_payment'], 1.0)
        self.assertAlmostEqual(vectors['all']['rule_score'], scored['score'])


class TrainingPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload, _ = train_ladder.collect()

    def test_split_is_strictly_time_ordered_and_disjoint(self):
        train = self.payload['train']
        test = self.payload['test']
        fit = self.payload['fit']
        validation = self.payload['validation']
        for group in (train, test, fit, validation):
            times = [e['time'] for e in group]
            self.assertEqual(times, sorted(times))
        self.assertEqual(fit + validation, train)
        self.assertLess(fit[-1]['time'], validation[0]['time'])
        self.assertLess(validation[-1]['time'], test[0]['time'])
        self.assertFalse({e['id'] for e in train} & {e['id'] for e in test})

    def test_scaler_is_fit_on_training_rows_only(self):
        features = baseline.RAW_FEATURES
        rows, _ = train_ladder.rows_for(self.payload['train'], 'raw',
                                        self.payload['labels'])
        mean, scale = train_ladder.fit_scaler(rows, features)
        expected = [sum(row[name] for row in rows) / len(rows) for name in features]
        for actual, wanted in zip(mean, expected):
            self.assertAlmostEqual(actual, wanted, places=12)
        self.assertTrue(all(value > 0 for value in scale))

    def test_exported_models_include_required_metadata(self):
        for name, filename in baseline.LADDER_MODELS.items():
            path = MODEL_DIR / filename
            self.assertTrue(path.exists(), f'{filename} should be committed')
            model = json.loads(path.read_text(encoding='utf-8'))
            for key in ('features', 'mean', 'scale', 'weights', 'intercept', 'C',
                        'training', 'fixture_sha256'):
                self.assertIn(key, model)
            self.assertEqual(model['fixture_sha256'], fixture_sha256('synthetic'))
            self.assertIn(model['C'], train_ladder.C_GRID)
            self.assertEqual(len(model['features']), len(model['weights']))
            self.assertEqual(len(model['features']), len(model['mean']))
            self.assertEqual(len(model['features']), len(model['scale']))

    def test_stdlib_fallback_is_deterministic(self):
        features = baseline.RAW_FEATURES
        rows, labels = train_ladder.rows_for(
            self.payload['fit'][:120], 'raw', self.payload['labels'])
        mean, scale = train_ladder.fit_scaler(rows, features)
        first = train_ladder.fit_stdlib(rows, labels, features, 1.0, mean, scale,
                                        max_iter=30)
        second = train_ladder.fit_stdlib(rows, labels, features, 1.0, mean, scale,
                                         max_iter=30)
        self.assertEqual(first['weights'], second['weights'])
        self.assertEqual(first['intercept'], second['intercept'])
        self.assertEqual(first['iterations'], second['iterations'])


class RuntimeModelTests(unittest.TestCase):
    def test_runtime_scorer_matches_exported_model_transform(self):
        models = baseline.load_ladder_models(MODEL_DIR)
        detector = Detector()
        scored = detector.process({
            'type': 'trial', 'account': 'a1', 'device': 'd1', 'ip': 'ip1',
            'card': 'c1', 'amount': 1200, 'disposable': True,
            'forwarding_email': True, 'label': 'legitimate'})
        try:
            import numpy as np
            from sklearn.linear_model import LogisticRegression
        except ImportError:
            self.skipTest('scikit-learn not installed')
        for name, model in models.items():
            self.assertIsNotNone(model, name)
            row = scored['model_features'][name]
            X = np.array([[row[name] for name in model['features']]], dtype=float)
            Z = (X - np.array(model['mean'])) / np.array(model['scale'])
            clf = LogisticRegression()
            clf.coef_ = np.array([model['weights']], dtype=float)
            clf.intercept_ = np.array([model['intercept']], dtype=float)
            clf.classes_ = np.array([0, 1])
            expected = float(clf.predict_proba(Z)[0, 1])
            self.assertAlmostEqual(baseline.probability(model, row), expected, places=6)

    def test_stale_hash_detection(self):
        models = baseline.load_ladder_models(MODEL_DIR)
        good = baseline.ladder_status(models, fixture_sha256('synthetic'))
        self.assertFalse(good['stale'])
        bad = baseline.ladder_status(models, '0' * 64)
        self.assertTrue(bad['stale'])
        self.assertEqual(bad['message'], 'model is stale, retrain')

    def test_allow_all_and_amount_rule_are_label_free_counts(self):
        test_events = [
            event(1, 'abuse', score=90, amount=1500),
            event(2, 'legitimate', score=10, amount=1500),
            event(3, 'abuse', score=10, amount=10),
            event(4, 'legitimate', score=10, amount=10),
        ]
        rows = {row['id']: row for row in ladder_rows(test_events, 35)}
        self.assertEqual(rows['allow_all']['confusion'], {'tp': 0, 'fp': 0, 'tn': 2, 'fn': 2})
        self.assertEqual(rows['allow_all']['recall'], 0.0)
        self.assertEqual(rows['amount_rule']['confusion'], {'tp': 1, 'fp': 1, 'tn': 1, 'fn': 1})
        self.assertEqual(rows['full_engine']['confusion']['tp'], 1)


class EvidenceReportTests(unittest.TestCase):
    def test_bootstrap_intervals_and_warning(self):
        test_events = []
        for index in range(30):
            abuse = index < 8
            test_events.append(event(index, 'abuse' if abuse else 'legitimate',
                                     score=90 if abuse else 5,
                                     amount=1500 if abuse else 10,
                                     ladder={'raw': 90 if abuse else 5,
                                             'raw_bd': 90 if abuse else 5,
                                             'all': 90 if abuse else 5}))
        rows = ladder_rows(test_events, 35)
        rows = bootstrap_ladder(test_events, rows, n=30, seed=7)
        self.assertEqual([row['id'] for row in rows],
                         [row['id'] for row in LADDER_ROWS])
        self.assertTrue(rows[0]['pr_auc_ci']['low'] <= rows[0]['pr_auc_ci']['high'])
        report = evaluation_report(test_events, 35, 65)
        self.assertTrue(report['ladder_diagnostics']['few_abuse_events'])
        self.assertEqual(report['ladder_diagnostics']['warning'],
                         'too few cases for reliable comparison')
        self.assertEqual(len(report['ladder']), 7)


class EvidenceEndpointTests(unittest.TestCase):
    def test_endpoint_returns_seven_rows_and_keeps_existing_views(self):
        server.detector.reset()
        events, _ = load_dataset('synthetic')
        for row in events:
            server.detector.process(row)
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            url = f'http://127.0.0.1:{httpd.server_port}/api/evidence'
            with urlopen(url, timeout=30) as response:
                report = json.loads(response.read().decode())
        finally:
            httpd.shutdown()
            httpd.server_close()
        self.assertEqual(len(report['ladder']), 7)
        self.assertIn('full', report['views'])
        self.assertIn('baseline', report['views'])
        self.assertTrue(report['ladder_diagnostics']['bootstrap']['done'])
        for row in report['ladder']:
            self.assertIn('pr_auc_ci', row)
            self.assertIn('recall_at_5pct_fpr_ci', row)
        self.assertTrue(report['ladder_models']['models'])


if __name__ == '__main__':
    unittest.main()
