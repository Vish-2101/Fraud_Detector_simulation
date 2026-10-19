"""Dataset catalog and loaders (standard library only).

Committed fixtures keep the demo offline. Raw/processed Kaggle data is never
committed; `data/raw/` and `data/processed/` are git-ignored.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / 'fixtures'
PROCESSED = ROOT / 'data' / 'processed'

PREPARE_IEEE = ('python tools/prepare_ieee_cis.py --raw data/raw/ieee_cis '
                '--out data/processed/ieee_cis_events.jsonl --sample 50000')
PREPARE_STRIPE = 'python tools/stripe_sandbox_seed.py  # optional; fixture works offline'

DATASETS = {
    'synthetic': {
        'label': 'Synthetic rehearsal',
        'path': FIXTURES / 'synthetic_events.jsonl',
        'prepare': 'Committed fixture; no preparation needed.',
        'window_seconds': 60,
        'note': 'Authored scenarios for classroom demonstrations. Labels are authored.',
    },
    'ieee_cis': {
        'label': 'IEEE-CIS replay (processed)',
        'path': PROCESSED / 'ieee_cis_events.jsonl',
        'prepare': PREPARE_IEEE,
        'window_seconds': 3600,
        'note': 'Real Vesta transactions; labels used only for temporal evaluation.',
    },
    'ieee_cis_fixture': {
        'label': 'IEEE-CIS sample fixture',
        'path': FIXTURES / 'ieee_cis_sample.jsonl',
        'prepare': 'Committed 500-row synthetic fixture with the IEEE-CIS event schema.',
        'window_seconds': 3600,
        'note': 'Offline stand-in, not real transactions. Same schema as processed data.',
    },
    'stripe_fixture': {
        'label': 'Stripe sample events',
        'path': FIXTURES / 'stripe_events_sample.json',
        'prepare': 'Committed hand-built Stripe JSON. Sandbox seeding is optional.',
        'window_seconds': 3600,
        'note': 'Stripe JSON shape, deterministic, not real fraud.',
    },
}


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
        count = _count_jsonl(meta['path']) if available and meta['path'].suffix == '.jsonl' else None
        rows.append({
            'name': name,
            'label': meta['label'],
            'available': available,
            'rows': count,
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


def load_dataset(name, limit=None):
    """Load a dataset as a time-contiguous prefix.

    `limit` never random-samples: it takes the first N rows after the file's
    documented time ordering, preserving ordering and local fraud rate.
    Returns (events, meta).
    """
    if name not in DATASETS:
        raise ValueError(f"Unknown dataset '{name}'. Choose one of: {', '.join(DATASETS)}.")
    meta = dict(DATASETS[name])
    path = meta['path']
    if not path.exists():
        raise FileNotFoundError(
            f"Dataset '{name}' is not available. Prepare it with:\n{meta['prepare']}")
    if name == 'stripe_fixture':
        from adapters.stripe_events import map_events
        with path.open('r', encoding='utf-8') as fh:
            raw = json.load(fh)
        if isinstance(raw, dict):
            raw = raw.get('events', raw.get('data', []))
        events = map_events(raw)
    else:
        events = _load_jsonl(path, limit=limit)
    if limit is not None:
        events = events[:int(limit)]
    if not events:
        raise ValueError(f"Dataset '{name}' contains no usable events.")
    if any(e.get('ts') is not None for e in events):
        events.sort(key=lambda e: (e.get('ts', 0), str(e.get('source_id') or e.get('id') or '')))
    meta.update(name=name, rows=len(events), path=str(path.relative_to(ROOT)).replace('\\', '/'))
    return events, meta
