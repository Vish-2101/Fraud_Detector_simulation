"""Train the three Evidence-ladder logistic-regression models.

Run:
  python tools/train_ladder.py

Uses a strict event-time split: first 70% of labeled events for training, last
30% held out. Inside the training part, the last 20% is validation for choosing
C. The test split is never used for scaling, C, or thresholds.

scikit-learn is optional. Without it, a deterministic batch-gradient-descent
fallback runs. Both paths export the same pure-Python JSON format.
"""
import argparse
import json
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from baseline import FEATURE_SETS, LADDER_MODELS, probability
from datasets import fixture_sha256, load_dataset
from engine import Detector
from evaluation import pr_curve, temporal_split

C_GRID = [0.01, 0.1, 1.0, 10.0]
BOOTSTRAP_SEED = 20261020


def split_train_validation(train, fraction=0.8):
    """First 80% of the training window fits; the last 20% chooses C."""
    cut = int(len(train) * fraction)
    return train[:cut], train[cut:]


def collect():
    load_started = time.perf_counter()
    events, meta = load_dataset('synthetic')
    load_seconds = time.perf_counter() - load_started
    window = int(meta.get('window_seconds', 60))
    detector = Detector(window_seconds=window, dgim_window=window)
    replay_started = time.perf_counter()
    for event in events:
        detector.process(event)
    replay_seconds = time.perf_counter() - replay_started
    train, test = temporal_split(detector.events, 0.7)
    fit_events, validation_events = split_train_validation(train, 0.8)
    labels = {event.get('id'): 1 if event.get('label') == 'abuse' else 0
              for event in train + test}
    payload = {
        'events': detector.events,
        'train': train,
        'test': test,
        'fit': fit_events,
        'validation': validation_events,
        'labels': labels,
        'meta': {
            'dataset': 'synthetic',
            'window_seconds': window,
            'events_replayed': len(detector.events),
            'train_events': len(train),
            'train_positives': sum(labels[e['id']] for e in train),
            'test_events': len(test),
            'test_positives': sum(labels[e['id']] for e in test),
            'validation_events': len(validation_events),
            'validation_positives': sum(labels[e['id']] for e in validation_events),
            'train_time_range': {'start_ts': train[0].get('ts') if train else None,
                                 'end_ts': train[-1].get('ts') if train else None},
        },
    }
    return payload, {'load': load_seconds, 'replay': replay_seconds}


def rows_for(events, feature_set, labels):
    features = FEATURE_SETS[feature_set]
    rows = [{name: float(event['model_features'][feature_set][name]) for name in features}
            for event in events]
    return rows, [labels[event['id']] for event in events]


def fit_scaler(rows, features):
    count = max(1, len(rows))
    mean = [sum(row[name] for row in rows) / count for name in features]
    scale = []
    for name, center in zip(features, mean):
        variance = sum((row[name] - center) ** 2 for row in rows) / count
        scale.append(math.sqrt(variance) or 1.0)
    return mean, scale


def transform(rows, features, mean, scale):
    return [[(row[name] - mean[i]) / scale[i] for i, name in enumerate(features)]
            for row in rows]


def fit_sklearn(rows, labels, features, C, mean, scale):
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    X = np.array(transform(rows, features, mean, scale), dtype=float)
    y = np.array(labels, dtype=int)
    model = LogisticRegression(C=C, max_iter=3000, class_weight='balanced',
                               random_state=42, solver='lbfgs')
    model.fit(X, y)
    return {
        'weights': [float(w) for w in model.coef_[0]],
        'intercept': float(model.intercept_[0]),
        'solver': 'sklearn.linear_model.LogisticRegression',
        'iterations': int(model.n_iter_[0]),
    }


def fit_stdlib(rows, labels, features, C, mean, scale,
               max_iter=700, tolerance=1e-7, learning_rate=0.5):
    """Deterministic full-batch gradient descent with L2 regularization."""
    X = transform(rows, features, mean, scale)
    weights = [0.0] * len(features)
    intercept = 0.0
    positives = sum(labels) or 1
    negatives = len(labels) - positives or 1
    pos_weight = len(labels) / (2.0 * positives)
    neg_weight = len(labels) / (2.0 * negatives)
    n = max(1, len(X))
    iterations = 0
    converged = False
    for iteration in range(1, max_iter + 1):
        iterations = iteration
        grad = [0.0] * len(features)
        grad_b = 0.0
        for x, truth in zip(X, labels):
            z = intercept + sum(w * v for w, v in zip(weights, x))
            p = 1.0 / (1.0 + math.exp(-max(-40.0, min(40.0, z))))
            weight = pos_weight if truth else neg_weight
            error = (p - truth) * weight
            grad_b += error
            for i, value in enumerate(x):
                grad[i] += error * value
        grad_b /= n
        for i in range(len(weights)):
            grad[i] = grad[i] / n + weights[i] / C
        intercept -= learning_rate * grad_b
        for i in range(len(weights)):
            weights[i] -= learning_rate * grad[i]
        if max(abs(g) for g in grad + [grad_b]) < tolerance:
            converged = True
            break
    return {
        'weights': weights,
        'intercept': intercept,
        'solver': 'stdlib batch gradient descent (fallback)',
        'iterations': iterations,
        'converged': converged,
    }


def validation_ap(rows, labels, features, fitted):
    model = {
        'features': features,
        'weights': fitted['weights'],
        'intercept': fitted['intercept'],
        'mean': fitted['mean'],
        'scale': fitted['scale'],
    }
    points = [(probability(model, row) * 100, bool(truth))
              for row, truth in zip(rows, labels)]
    return pr_curve(points)['pr_auc']


def train_feature_set(name, feature_set, payload, use_sklearn):
    features = FEATURE_SETS[feature_set]
    labels = payload['labels']
    fit_rows, fit_labels = rows_for(payload['fit'], feature_set, labels)
    validation_rows, validation_labels = rows_for(payload['validation'], feature_set, labels)
    train_rows, train_labels = rows_for(payload['train'], feature_set, labels)

    validation_started = time.perf_counter()
    candidates = []
    for C in C_GRID:
        fit_started = time.perf_counter()
        mean, scale = fit_scaler(fit_rows, features)
        if use_sklearn:
            fitted = fit_sklearn(fit_rows, fit_labels, features, C, mean, scale)
        else:
            fitted = fit_stdlib(fit_rows, fit_labels, features, C, mean, scale)
        fitted.update(mean=mean, scale=scale)
        score = validation_ap(validation_rows, validation_labels, features, fitted)
        candidates.append((score if score is not None else -1.0, C, fitted,
                           time.perf_counter() - fit_started))
    best_score, best_c, _, _ = max(candidates, key=lambda item: (item[0], -item[1]))
    chosen_c = best_c if best_score >= 0 else 1.0
    validation_seconds = time.perf_counter() - validation_started

    fit_started = time.perf_counter()
    mean, scale = fit_scaler(train_rows, features)
    if use_sklearn:
        fitted = fit_sklearn(train_rows, train_labels, features, chosen_c, mean, scale)
    else:
        fitted = fit_stdlib(train_rows, train_labels, features, chosen_c, mean, scale)
    fit_seconds = time.perf_counter() - fit_started
    fitted.update(mean=mean, scale=scale, C=chosen_c)

    pairs = sorted(zip(features, fitted['weights']),
                   key=lambda pair: (-abs(pair[1]), pair[0]))[:5]
    meta = payload['meta']
    model = {
        'version': 1,
        'name': name,
        'feature_set': feature_set,
        'features': features,
        **fitted,
        'fixture_sha256': fixture_sha256('synthetic'),
        'training': {
            'dataset': meta['dataset'],
            'window_seconds': meta['window_seconds'],
            'train_events': meta['train_events'],
            'train_positives': meta['train_positives'],
            'test_events': meta['test_events'],
            'test_positives': meta['test_positives'],
            'validation_events': meta['validation_events'],
            'validation_positives': meta['validation_positives'],
            'fit_events': len(payload['fit']),
            'events_replayed': meta['events_replayed'],
            'time_range': meta['train_time_range'],
            'temporal_split': 'first 70% by event time for training; last 30% held out',
            'validation_split': 'last 20% of the training window, by event time',
            'label_policy': 'labels only build y; every feature is label-free',
            'validation_average_precision': round(best_score, 4) if best_score >= 0 else None,
        },
        'top_weights': [{'feature': feature, 'weight': round(weight, 4)}
                        for feature, weight in pairs],
        'score_mapping': 'score = round(100 * sigmoid(z), 4); not a calibrated probability',
    }
    return model, validation_seconds, fit_seconds


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out-dir', default='models')
    args = parser.parse_args()

    payload, timings = collect()
    try:
        import sklearn  # noqa: F401
        use_sklearn = True
        path = 'scikit-learn'
    except ImportError:
        use_sklearn = False
        path = 'stdlib fallback'
    print(f"load: {len(payload['events'])} fixture events in {timings['load']:.2f}s")
    print(f"replay: features recorded in {timings['replay']:.2f}s")
    print(f"split: {payload['meta']['train_events']} train "
          f"({payload['meta']['train_positives']} abuse), "
          f"{payload['meta']['validation_events']} validation, "
          f"{payload['meta']['test_events']} test "
          f"({payload['meta']['test_positives']} abuse)")
    print(f"training path: {path}")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, set_name in (('raw', 'raw'), ('raw_bd', 'raw_bd'), ('all', 'all')):
        started = time.perf_counter()
        model, validation_seconds, fit_seconds = train_feature_set(
            name, set_name, payload, use_sklearn)
        out = out_dir / LADDER_MODELS[name]
        export_started = time.perf_counter()
        out.write_text(json.dumps(model, indent=2) + '\n', encoding='utf-8')
        export_seconds = time.perf_counter() - export_started
        elapsed = time.perf_counter() - started
        print(f"\n{name}: {out} features={len(model['features'])} "
              f"C={model['C']:g} iterations={model.get('iterations')}")
        print(f"  train {validation_seconds + fit_seconds:.2f}s "
              f"(validation {validation_seconds:.2f}s, fit {fit_seconds:.2f}s) "
              f"| export {export_seconds:.3f}s | total {elapsed:.2f}s")
        print('  top weights: ' + ', '.join(
            f"{item['feature']}={item['weight']:.3f}"
            for item in model['top_weights']))


if __name__ == '__main__':
    main()
