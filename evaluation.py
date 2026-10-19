"""Temporal evaluation helpers (stdlib only).

Ground-truth labels are used ONLY here, after scoring. Scores and decisions are
produced by the engine without reading `label`; these functions compare stored
scores with stored labels for reporting and the Evidence tab.
"""
import math


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


def evaluate_view(test_events, score_key, threshold):
    cm, points = confusion(test_events, score_key, threshold)
    curve = pr_curve(points)
    return {
        'confusion': cm,
        **{k: v for k, v in metrics(cm).items() if k not in cm},
        'pr_curve': curve['points'],
        'pr_auc': curve['pr_auc'],
        'recall_at_1pct_fpr': recall_at_fpr(points, 0.01),
        'recall_at_5pct_fpr': recall_at_fpr(points, 0.05),
    }


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


def evaluation_report(events, review_threshold, block_threshold, train_fraction=0.7):
    train, test = temporal_split(events, train_fraction)
    views = {}
    for key in ('exact', 'full', 'baseline'):
        if key != 'full' and not any(_score(e, key) is not None for e in test):
            views[key] = {'available': False}
            continue
        views[key] = {'available': True, **evaluate_view(test, key, review_threshold)}
    return {
        'method': 'temporal_split_70_30',
        'split': {'train_n': len(train), 'test_n': len(test),
                  'cut_time': test[0].get('time') if test else None,
                  'note': 'First 70% by event time for tuning/training, last 30% held out.'},
        'thresholds': {'review': review_threshold, 'block': block_threshold},
        'views': views,
        'ablation': rule_ablation(test, review_threshold),
    }
