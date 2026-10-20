"""Label-free feature extraction and pure-Python scoring for learned baselines.

The runtime server is standard-library only. Tools may use scikit-learn to fit
models, but they export only JSON: feature names, scaler (mean/scale), weights
and intercept. This module reproduces the logistic regression transform without
any third-party package.

Labels are NEVER read here. Every feature is derived from the event payload and
the detector's label-free state (rule score, velocities, Bloom/DGIM flags).
"""
import json
import math
from pathlib import Path

ROOT = Path(__file__).parent
MODEL_PATH = ROOT / 'models' / 'baseline.json'
MODELS_DIR = ROOT / 'models'

EVENT_TYPES = ('payment', 'signup', 'trial', 'usage', 'cancel')

# Feature names are stable: the exporter writes them, the server reads them.
FEATURES = [
    'amount_log',
    'amount_ge_1000',
    'disposable',
    'country_mismatch',
    'failed',
    'device_accounts',
    'card_attempts',
    'device_trials',
    'ip_accounts',
    'dgim_device_est',
    'bloom_device_seen',
    'bloom_card_seen',
    'rule_score',
]

# Evidence-ladder feature sets. RAW deliberately excludes rule_score, Bloom,
# DGIM and reason flags so it cannot copy the engine's own decisions.
RAW_FEATURES = [
    'amount',
    'amount_log',
    'amount_ge_1000',
    'disposable',
    'forwarding_email',
    'country_mismatch',
    'failed',
    *[f'type_{name}' for name in EVENT_TYPES],
    'device_accounts',
    'card_attempts',
    'device_trials',
    'ip_accounts',
]
BD_FEATURES = RAW_FEATURES + ['bloom_device_seen', 'bloom_card_seen', 'dgim_device_est']
FEATURE_SETS = {'raw': RAW_FEATURES, 'raw_bd': BD_FEATURES, 'all': FEATURES}
LADDER_MODELS = {
    'raw': 'ladder_raw.json',
    'raw_bd': 'ladder_raw_bd.json',
    'all': 'ladder_all.json',
}


def _num(value, default=0.0):
    if isinstance(value, bool):
        return float(value)
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def extract_raw(event):
    """Return raw, label-free features for one scored event."""
    features = event.get('features') or {}
    amount = max(0.0, _num(event.get('amount')))
    event_type = event.get('type')
    vector = {
        'amount': amount,
        'amount_log': math.log1p(amount),
        'amount_ge_1000': 1.0 if amount >= 1000 else 0.0,
        'disposable': 1.0 if event.get('disposable') else 0.0,
        'forwarding_email': 1.0 if event.get('forwarding_email') else 0.0,
        'country_mismatch': 1.0 if event.get('country_mismatch') else 0.0,
        'failed': 1.0 if event.get('failed') else 0.0,
    }
    for name in EVENT_TYPES:
        vector[f'type_{name}'] = 1.0 if event_type == name else 0.0
    vector.update({
        'device_accounts': _num(features.get('device_accounts')),
        'card_attempts': _num(features.get('card_attempts')),
        'device_trials': _num(features.get('device_trials')),
        'ip_accounts': _num(features.get('ip_accounts')),
    })
    return {name: vector[name] for name in RAW_FEATURES}


def extract_raw_bd(event):
    """Raw features plus Bloom lookups and the DGIM device estimate."""
    raw = extract_raw(event)
    bloom = event.get('bloom') or {}
    dgim = event.get('dgim') or {}
    raw.update({
        'bloom_device_seen': 1.0 if bloom.get('device_seen') else 0.0,
        'bloom_card_seen': 1.0 if bloom.get('card_seen') else 0.0,
        'dgim_device_est': _num(dgim.get('device_est')),
    })
    return {name: raw[name] for name in BD_FEATURES}


def extract_features(event):
    """Return the ordered ``all`` feature set for one scored event."""
    features = event.get('features') or {}
    bloom = event.get('bloom') or {}
    dgim = event.get('dgim') or {}
    amount = max(0.0, _num(event.get('amount')))
    return {
        'amount_log': math.log1p(amount),
        'amount_ge_1000': 1.0 if amount >= 1000 else 0.0,
        'disposable': 1.0 if event.get('disposable') else 0.0,
        'country_mismatch': 1.0 if event.get('country_mismatch') else 0.0,
        'failed': 1.0 if event.get('failed') else 0.0,
        'device_accounts': _num(features.get('device_accounts')),
        'card_attempts': _num(features.get('card_attempts')),
        'device_trials': _num(features.get('device_trials')),
        'ip_accounts': _num(features.get('ip_accounts')),
        'dgim_device_est': _num(dgim.get('device_est')),
        'bloom_device_seen': 1.0 if bloom.get('device_seen') else 0.0,
        'bloom_card_seen': 1.0 if bloom.get('card_seen') else 0.0,
        'rule_score': _num(event.get('score')),
    }


def event_features(event):
    """Return the three recorded model vectors for a replayed event."""
    recorded = event.get('model_features')
    if isinstance(recorded, dict):
        return recorded
    return {
        'raw': extract_raw(event),
        'raw_bd': extract_raw_bd(event),
        'all': extract_features(event),
    }


def load_model(path=None):
    """Load models/baseline.json. Returns None when the model is not present."""
    path = Path(path) if path else MODEL_PATH
    if not path.exists():
        return None
    try:
        with path.open('r', encoding='utf-8') as fh:
            model = json.load(fh)
        required = {'features', 'weights', 'intercept', 'mean', 'scale'}
        if not required <= set(model):
            return None
        if len(model['features']) != len(model['weights']):
            return None
        return model
    except (OSError, ValueError):
        return None


def load_ladder_models(directory=None):
    """Load the three Evidence-ladder models keyed by feature-set name."""
    directory = Path(directory) if directory else MODELS_DIR
    return {
        name: load_model(directory / filename)
        for name, filename in LADDER_MODELS.items()
    }


def model_status(name, model, fixture_sha256=None):
    """Describe one learned model without exposing training rows."""
    if not model:
        return {'name': name, 'available': False, 'stale': False,
                'message': 'model unavailable — retrain', 'card': None}
    expected = model.get('fixture_sha256')
    stale = bool(fixture_sha256 and expected != fixture_sha256)
    training = model.get('training') or {}
    pairs = list(zip(model.get('features') or [], model.get('weights') or []))
    top = sorted(pairs, key=lambda pair: (-abs(_num(pair[1])), pair[0]))[:5]
    return {
        'name': name,
        'available': True,
        'stale': stale,
        'message': 'model is stale, retrain' if stale else '',
        'card': {
            'C': model.get('C'),
            'features': len(model.get('features') or []),
            'train_events': training.get('train_events'),
            'train_positives': training.get('train_positives'),
            'test_events': training.get('test_events'),
            'test_positives': training.get('test_positives'),
            'time_range': training.get('time_range'),
            'top_weights': [{'feature': feature, 'weight': round(_num(weight), 4)}
                            for feature, weight in top],
        },
    }


def ladder_status(models, fixture_sha256=None):
    rows = [model_status(name, model, fixture_sha256)
            for name, model in (models or {}).items()]
    stale = any(row['stale'] for row in rows)
    missing = any(not row['available'] for row in rows)
    return {
        'models': rows,
        'stale': stale or missing,
        'message': ('model is stale, retrain' if stale else
                    'model unavailable — retrain' if missing else ''),
        'fixture_sha256': fixture_sha256,
    }


def _aligned(model, features):
    names = model['features']
    return [features.get(name, 0.0) for name in names]


def decision_function(model, features):
    """Standardized linear predictor z = b + sum(w_i * (x_i - mean_i)/scale_i)."""
    values = _aligned(model, features)
    z = float(model.get('intercept', 0.0))
    for i, value in enumerate(values):
        scale = _num(model['scale'][i], 1.0) or 1.0
        mean = _num(model['mean'][i])
        z += _num(model['weights'][i]) * ((value - mean) / scale)
    return z


def probability(model, features):
    z = decision_function(model, features)
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    exp_z = math.exp(z)
    return exp_z / (1.0 + exp_z)


def score(model, features, scale=100.0):
    """Map the learned probability to a 0-100 risk score (not a probability)."""
    return round(probability(model, features) * scale, 4)
