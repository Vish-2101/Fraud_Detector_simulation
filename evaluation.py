"""Temporal evaluation helpers (stdlib only).

Ground-truth labels are used ONLY here, after scoring. Scores and decisions are
produced by the engine without reading `label`; these functions compare stored
scores with stored labels for reporting and the Evidence tab.
"""
import math
import random


LADDER_ROWS = [
    {'id': 'allow_all', 'label': 'Allow everything (reference)', 'trained': False,
     'uses_rule_score': False, 'uses_bloom_dgim': False, 'reference': True},
    {'id': 'amount_rule', 'label': 'Single rule: amount >= $1,000', 'trained': False,
     'uses_rule_score': False, 'uses_bloom_dgim': False, 'reference': False},
    {'id': 'exact_rules', 'label': 'Exact rules only (existing shadow)', 'trained': False,
     'uses_rule_score': True, 'uses_bloom_dgim': False, 'reference': False},
    {'id': 'full_engine', 'label': 'Full engine: rules + Bloom/DGIM', 'trained': False,
     'uses_rule_score': True, 'uses_bloom_dgim': True, 'reference': False},
    {'id': 'lr_raw', 'label': 'LR-raw', 'trained': True,
     'uses_rule_score': False, 'uses_bloom_dgim': False, 'reference': False},
    {'id': 'lr_raw_bd', 'label': 'LR-raw+BD', 'trained': True,
     'uses_rule_score': False, 'uses_bloom_dgim': True, 'reference': False},
    {'id': 'lr_all', 'label': 'LR-all (reference: uses the engine\'s own outputs, not an independent baseline)',
     'trained': True, 'uses_rule_score': True, 'uses_bloom_dgim': True, 'reference': True},
]


def labeled(events):
    return [e for e in events if e.get('label') in ('abuse', 'legitimate')]


def temporal_split(events, train_fraction=0.7):
    """Split by stored event time, never randomly.

    The first `train_fraction` of time-ordered labeled events are for tuning and
    training; the remainder is the held-out test split.
    """
    ordered = sorted(labeled(events), key=lambda e: (e.get('time', 0), e.get('id', '')))
    cut = int(len(ordered) * train_fraction)
    return ordered[:cut], ordered[cut:]


def confusion(events, score_key, threshold):
    cm = {'tp': 0, 'fp': 0, 'tn': 0, 'fn': 0}
    scores = []
    for e in events:
        score = _score(e, score_key)
        if score is None:
            continue
        positive = score >= threshold
        truth = e.get('label') == 'abuse'
        scores.append((score, truth))
        if positive and truth:
            cm['tp'] += 1
        elif positive and not truth:
            cm['fp'] += 1
        elif truth:
            cm['fn'] += 1
        else:
            cm['tn'] += 1
    return cm, scores


def _score(event, key):
    if key == 'exact':
        return (event.get('shadow') or {}).get('base_score')
    if key == 'baseline':
        return (event.get('baseline') or {}).get('score')
    return event.get('score')


def _ladder_score(event, row_id):
    """Return one Evidence-ladder score. Labels are never read."""
    if row_id == 'allow_all':
        return 0.0
    if row_id == 'amount_rule':
        try:
            amount = float(event.get('amount') or 0)
        except (TypeError, ValueError):
            amount = 0.0
        return 100.0 if amount >= 1000 else 0.0
    if row_id == 'exact_rules':
        return (event.get('shadow') or {}).get('base_score')
    if row_id == 'full_engine':
        return event.get('score')
    key = {'lr_raw': 'raw', 'lr_raw_bd': 'raw_bd', 'lr_all': 'all'}.get(row_id)
    return (event.get('ladder') or {}).get(key) if key else None


def _row_points(events, row_id):
    points = []
    for event in events:
        score = _ladder_score(event, row_id)
        if score is None:
            return None
        points.append((score, event.get('label') == 'abuse'))
    return points


def _ratio(a, b):
    return a / b if b else None


def metrics(cm):
    tp, fp, tn, fn = cm['tp'], cm['fp'], cm['tn'], cm['fn']
    return {
        **cm,
        'precision': round(_ratio(tp, tp + fp), 4) if tp + fp else None,
        'recall': round(_ratio(tp, tp + fn), 4) if tp + fn else None,
        'false_positive_rate': round(_ratio(fp, fp + tn), 4) if fp + tn else None,
        'labeled': tp + fp + tn + fn,
        'positives': tp + fn,
    }


def pr_curve(points, max_points=240):
    """Precision/recall sweep plus average precision (AP = area under PR).

    `points` is a list of (score, truth). Ties are handled by grouping equal
    scores so the curve is deterministic. The returned curve is downsampled to
    `max_points` for payload size; AP is computed on the full sweep.
    """
    total_pos = sum(1 for _, truth in points if truth)
    if not points or not total_pos:
        return {'points': [], 'pr_auc': None, 'average_precision': None}
    ordered = sorted(points, key=lambda p: -p[0])
    curve = []
    tp = fp = 0
    i = 0
    while i < len(ordered):
        score = ordered[i][0]
        while i < len(ordered) and ordered[i][0] == score:
            if ordered[i][1]:
                tp += 1
            else:
                fp += 1
            i += 1
        recall = tp / total_pos
        precision = tp / (tp + fp) if tp + fp else 0.0
        curve.append({'score': round(score, 4), 'recall': round(recall, 4),
                      'precision': round(precision, 4), 'tp': tp, 'fp': fp})
    # Average precision: sum (R_i - R_{i-1}) * P_i, high-score end first.
    ap = 0.0
    prev_recall = 0.0
    for point in curve:
        ap += max(0.0, point['recall'] - prev_recall) * point['precision']
        prev_recall = point['recall']
    if len(curve) > max_points:
        stride = max(1, len(curve) // max_points)
        curve = curve[::stride]
    return {'points': curve, 'pr_auc': round(ap, 4), 'average_precision': round(ap, 4)}


def recall_at_fpr(points, max_fpr):
    """Best recall achieved while the false-positive rate stays <= max_fpr."""
    total_neg = sum(1 for _, truth in points if not truth)
    if total_neg == 0:
        return None
    ordered = sorted(points, key=lambda p: -p[0])
    tp = fp = 0
    best = 0.0
    best_point = None
    total_pos = sum(1 for _, truth in points if truth)
    i = 0
    while i < len(ordered):
        score = ordered[i][0]
        while i < len(ordered) and ordered[i][0] == score:
            if ordered[i][1]:
                tp += 1
            else:
                fp += 1
            i += 1
        fpr = fp / total_neg
        recall = tp / total_pos if total_pos else 0.0
        if fpr <= max_fpr and recall >= best:
            best = recall
            best_point = {'score': round(score, 4), 'recall': round(recall, 4),
                          'fpr': round(fpr, 4), 'tp': tp, 'fp': fp}
    return {'recall': round(best, 4), 'point': best_point}


def _view_from_points(points, threshold):
    cm = {'tp': 0, 'fp': 0, 'tn': 0, 'fn': 0}
    for score, truth in points:
        positive = score >= threshold
        if positive and truth:
            cm['tp'] += 1
        elif positive and not truth:
            cm['fp'] += 1
        elif truth:
            cm['fn'] += 1
        else:
            cm['tn'] += 1
    curve = pr_curve(points)
    return {
        'confusion': cm,
        **{k: v for k, v in metrics(cm).items() if k not in cm},
        'pr_curve': curve['points'],
        'pr_auc': curve['pr_auc'],
        'recall_at_1pct_fpr': recall_at_fpr(points, 0.01),
        'recall_at_5pct_fpr': recall_at_fpr(points, 0.05),
    }


def evaluate_view(test_events, score_key, threshold):
    _, points = confusion(test_events, score_key, threshold)
    return _view_from_points(points, threshold)


def ladder_rows(test_events, threshold):
    if not test_events:
        return [{**definition, 'available': False} for definition in LADDER_ROWS]
    rows = []
    for definition in LADDER_ROWS:
        points = _row_points(test_events, definition['id'])
        if points is None:
            rows.append({**definition, 'available': False})
            continue
        rows.append({**definition, 'available': True,
                     **_view_from_points(points, threshold)})
    return rows


def _percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return round(ordered[index], 4)


def _bootstrap_metrics(points, max_fpr):
    """Fast AP and recall@FPR for one bootstrap sample (no payload objects)."""
    total_pos = sum(1 for _, truth in points if truth)
    total_neg = len(points) - total_pos
    if not total_pos:
        return None, None
    ordered = sorted(points, key=lambda point: -point[0])
    tp = fp = 0
    previous_recall = 0.0
    ap = 0.0
    best_recall = 0.0
    index = 0
    while index < len(ordered):
        score = ordered[index][0]
        while index < len(ordered) and ordered[index][0] == score:
            if ordered[index][1]:
                tp += 1
            else:
                fp += 1
            index += 1
        recall = tp / total_pos
        precision = tp / (tp + fp)
        ap += max(0.0, recall - previous_recall) * precision
        previous_recall = recall
        if total_neg and fp / total_neg <= max_fpr and recall >= best_recall:
            best_recall = recall
    return round(ap, 4), round(best_recall, 4)


def bootstrap_ladder(test_events, rows, n=500, seed=20261020, max_fpr=0.05):
    """Paired bootstrap intervals for AP and recall@5%FPR, on demand."""
    if not test_events:
        return rows
    rng = random.Random(seed)
    collected = {row['id']: {'pr_auc': [], 'recall': []}
                 for row in rows if row.get('available')}
    arrays = {}
    for row in rows:
        if not row.get('available'):
            continue
        scores = [_ladder_score(event, row['id']) for event in test_events]
        if any(score is None for score in scores):
            collected.pop(row['id'], None)
            continue
        arrays[row['id']] = scores
    truths = [event.get('label') == 'abuse' for event in test_events]
    size = len(test_events)
    for _ in range(n):
        indices = [rng.randrange(size) for _ in range(size)]
        for row_id, scores in arrays.items():
            points = [(scores[i], truths[i]) for i in indices]
            ap, recall = _bootstrap_metrics(points, max_fpr)
            store = collected[row_id]
            if ap is not None:
                store['pr_auc'].append(ap)
            if recall is not None:
                store['recall'].append(recall)
    for row in rows:
        store = collected.get(row['id'])
        if not store:
            continue
        row['pr_auc_ci'] = {'low': _percentile(store['pr_auc'], 0.025),
                            'high': _percentile(store['pr_auc'], 0.975)}
        row['recall_at_5pct_fpr_ci'] = {'low': _percentile(store['recall'], 0.025),
                                        'high': _percentile(store['recall'], 0.975)}
    return rows


def rule_ablation(test_events, threshold):
    """How many test decisions flip when each scored rule is removed.

    Uses stored reasons (already label-free). A flip means review/block changes
    to a different action after subtracting that rule's points.
    """
    counts = {}
    for e in test_events:
        base = e.get('score') or 0
        for reason in e.get('reasons') or []:
            title = reason.get('reason', 'unknown')
            points = reason.get('points', 0)
            reduced = max(0, base - points)
            before = action_for(base, threshold)
            after = action_for(reduced, threshold)
            entry = counts.setdefault(title, {'rule': title, 'flips': 0, 'events': 0})
            entry['events'] += 1
            if before != after:
                entry['flips'] += 1
    return sorted(counts.values(), key=lambda r: (-r['flips'], r['rule']))


def action_for(score, review_threshold, block_threshold=None):
    if block_threshold is None:
        block_threshold = max(review_threshold + 1, 65)
    if score >= block_threshold:
        return 'block'
    if score >= review_threshold:
        return 'review'
    return 'allow'


def evaluation_report(events, review_threshold, block_threshold, train_fraction=0.7,
                      bootstrap=False):
    train, test = temporal_split(events, train_fraction)
    views = {}
    for key in ('exact', 'full', 'baseline'):
        if key != 'full' and not any(_score(e, key) is not None for e in test):
            views[key] = {'available': False}
            continue
        views[key] = {'available': True, **evaluate_view(test, key, review_threshold)}
    ladder = ladder_rows(test, review_threshold)
    if bootstrap:
        ladder = bootstrap_ladder(test, ladder)
    positives = sum(1 for e in test if e.get('label') == 'abuse')
    return {
        'method': 'temporal_split_70_30',
        'split': {'train_n': len(train), 'test_n': len(test),
                  'cut_time': test[0].get('time') if test else None,
                  'note': 'First 70% by event time for tuning/training, last 30% held out.'},
        'thresholds': {'review': review_threshold, 'block': block_threshold},
        'views': views,
        'ladder': ladder,
        'ladder_diagnostics': {
            'test_positives': positives,
            'few_abuse_events': positives < 20,
            'warning': ('too few cases for reliable comparison' if positives < 20 else ''),
            'bootstrap': {'n': 500, 'seed': 20261020, 'done': bool(bootstrap),
                          'note': '95% percentile interval from paired test-event resamples.'},
        },
        'ablation': rule_ablation(test, review_threshold),
    }
