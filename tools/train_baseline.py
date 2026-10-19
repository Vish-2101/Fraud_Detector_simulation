"""Train the label-free logistic-regression baseline (sklearn or stdlib fallback).

Run:
  python tools/train_baseline.py
  python tools/train_baseline.py --out models/baseline.json

Trains on the committed synthetic demo. Only label-free features are used.
Labels select train rows and provide y, but they never enter engine scoring,
Bloom, DGIM or Apriori baskets.

The server reads models/baseline.json with baseline.py (pure Python).
"""
import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from baseline import FEATURES, extract_features
from datasets import load_dataset
from engine import Detector
from evaluation import temporal_split


def collect(train_fraction=0.7):
    dataset = 'synthetic'
    events, meta = load_dataset(dataset)
    window = int(meta.get('window_seconds', 60))
    detector = Detector(window_seconds=window, dgim_window=window)
    for event in events:
        detector.process(event)
    train, test = temporal_split(detector.events, train_fraction)
    rows, labels = [], []
    for event in train:
        rows.append(extract_features(event))
        labels.append(1 if event['label'] == 'abuse' else 0)
    return rows, labels, {
        'dataset': dataset,
        'window_seconds': window,
        'train_events': len(train),
        'test_events': len(test),
        'train_positives': sum(labels),
        'events_replayed': len(detector.events),
    }


def fit_sklearn(rows, labels):
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    X = np.array([[row[name] for name in FEATURES] for row in rows], dtype=float)
    y = np.array(labels, dtype=int)
    mean = X.mean(axis=0)
    scale = X.std(axis=0)
    scale[scale == 0] = 1.0
    Z = (X - mean) / scale
    model = LogisticRegression(max_iter=2000, class_weight='balanced', random_state=42)
    model.fit(Z, y)
    return {
        'weights': [float(w) for w in model.coef_[0]],
        'intercept': float(model.intercept_[0]),
        'mean': [float(m) for m in mean],
        'scale': [float(s) for s in scale],
        'solver': 'sklearn.linear_model.LogisticRegression',
    }


def fit_stdlib(rows, labels):
    """Deterministic gradient-descent fallback for machines without sklearn."""
    X = [[row[name] for name in FEATURES] for row in rows]
    mean = [sum(col) / max(1, len(col)) for col in zip(*X)]
    scale = []
    for i in range(len(FEATURES)):
        variance = sum((row[i] - mean[i]) ** 2 for row in X) / max(1, len(X))
        scale.append(math.sqrt(variance) or 1.0)
    Z = [[(row[i] - mean[i]) / scale[i] for i in range(len(FEATURES))] for row in X]
    weights = [0.0] * len(FEATURES)
    intercept = 0.0
    positives = sum(labels) or 1
    negatives = len(labels) - positives or 1
    pos_weight = len(labels) / (2.0 * positives)
    neg_weight = len(labels) / (2.0 * negatives)
    lr = 0.1
    for _ in range(400):
        grad = [0.0] * len(FEATURES)
        grad_b = 0.0
        for x, y in zip(Z, labels):
            z = intercept + sum(w * v for w, v in zip(weights, x))
            p = 1.0 / (1.0 + math.exp(-max(-40.0, min(40.0, z))))
            weight = pos_weight if y else neg_weight
            error = (p - y) * weight
            grad_b += error
            for i, v in enumerate(x):
                grad[i] += error * v
        n = max(1, len(Z))
        intercept -= lr * grad_b / n
        for i in range(len(weights)):
            weights[i] -= lr * grad[i] / n
    return {
        'weights': weights,
        'intercept': intercept,
        'mean': mean,
        'scale': scale,
        'solver': 'stdlib gradient descent (fallback)',
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', default='models/baseline.json')
    parser.add_argument('--train-fraction', type=float, default=0.7)
    args = parser.parse_args()

    rows, labels, meta = collect(args.train_fraction)
    positive = sum(labels)
    negative = len(labels) - positive
    if positive < 2 or negative < 2:
        raise SystemExit('Need at least two positives and two negatives in the training split.')
    try:
        fitted = fit_sklearn(rows, labels)
    except ImportError:
        fitted = fit_stdlib(rows, labels)
    model = {
        'version': 1,
        'features': FEATURES,
        **fitted,
        'training': {
            **meta,
            'train_positives': positive,
            'train_negatives': negative,
            'temporal_split': 'first 70% by event time for training; last 30% held out',
            'label_policy': 'labels only build y; every feature is label-free',
        },
        'score_mapping': 'score = round(100 * sigmoid(z), 4); not a calibrated probability',
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(model, indent=2) + '\n', encoding='utf-8')
    print(f'wrote {out} features={len(FEATURES)} train={len(rows)} positives={positive} '
          f"solver={fitted['solver']}")


if __name__ == '__main__':
    main()
