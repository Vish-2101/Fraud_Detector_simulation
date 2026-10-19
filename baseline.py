"""Label-free feature extraction and pure-Python scoring for the learned baseline.

The runtime server is standard-library only. `tools/train_baseline.py` may use
scikit-learn to fit the model, but it exports only JSON: feature names, scaler
(mean/scale), weights and intercept. This module reproduces the logistic
regression transform without any third-party package.

Labels are NEVER read here. Every feature is derived from the event payload and
the detector's label-free state (rule score, velocities, Bloom/DGIM flags).
"""
import json
import math
from pathlib import Path

MODEL_PATH = Path(__file__).parent / 'models' / 'baseline.json'

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


def _num(value, default=0.0):
    if isinstance(value, bool):
        return float(value)
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def extract_features(event):
    """Return an ordered dict of label-free features for one scored event."""
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
