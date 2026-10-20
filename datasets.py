"""Dataset catalogue and loader (standard library only).

The offline demo ships one committed dataset: a small synthetic event log.
No external or raw datasets are read.
"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / 'fixtures'

DATASETS = {
    'synthetic': {
        'label': 'Synthetic demo',
        'path': FIXTURES / 'synthetic_events.jsonl',
        'prepare': 'Committed fixture; no preparation needed.',
        'window_seconds': 60,
        'note': 'Authored scenarios for classroom demonstrations. Labels are authored.',
    },
}


def fixture_sha256(name='synthetic'):
    """Return the SHA-256 of a dataset fixture, or None when unavailable."""
    meta = DATASETS.get(name)
    if not meta or not meta['path'].exists():
        return None
    digest = hashlib.sha256()
    with meta['path'].open('rb') as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _count_jsonl(path):
    if not path.exists():
        return None
    try:
        with path.open('r', encoding='utf-8') as fh:
            return sum(1 for line in fh if line.strip())
    except OSError:
        return None


def catalog():
    rows = []
    for name, meta in DATASETS.items():
        available = meta['path'].exists()
        rows.append({
            'name': name,
            'label': meta['label'],
            'available': available,
            'rows': _count_jsonl(meta['path']) if available else None,
            'path': str(meta['path'].relative_to(ROOT)).replace('\\', '/'),
            'prepare': meta['prepare'],
            'window_seconds': meta['window_seconds'],
            'note': meta['note'],
        })
    return rows


def _load_jsonl(path, limit=None):
    events = []
    with path.open('r', encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            events.append(json.loads(line))
            if limit is not None and len(events) >= limit:
                break
    return events


def load_dataset(name='synthetic', limit=None):
    """Load the synthetic demo as a time-contiguous prefix.

    `limit` never random-samples: it takes the first N rows, preserving ordering.
    Returns (events, meta).
    """
    if name not in DATASETS:
        raise ValueError(f"Unknown dataset '{name}'. Only 'synthetic' is available.")
    meta = dict(DATASETS[name])
    path = meta['path']
    if not path.exists():
        raise FileNotFoundError(
            f"Dataset '{name}' is not available. Expected {meta['path']}.")
    events = _load_jsonl(path, limit=limit)
    if limit is not None:
        events = events[:int(limit)]
    if not events:
        raise ValueError(f"Dataset '{name}' contains no usable events.")
    events.sort(key=lambda e: (e.get('ts', 0), str(e.get('source_id') or e.get('id') or '')))
    meta.update(name=name, rows=len(events), path=str(path.relative_to(ROOT)).replace('\\', '/'))
    return events, meta
