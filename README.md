# Radar Lab

A local, dependency-free simulation of **real-time payment fraud and account / free-trial abuse detection**, inspired by the business problem addressed by Stripe Radar. This is an independent educational prototype, not Stripe software. The demo uses one committed **synthetic** event log and transparent rules plus the syllabus algorithms (Bloom filter, DGIM, Apriori) and an optional learned baseline. No API keys, internet, external datasets, or database are needed.

## Quick start

```sh
python server.py                  # http://127.0.0.1:8765
python server.py --port 8766
python server.py --sample 500 --window-seconds 60
```

The server uses only the Python standard library (3.9+). On boot the UI loads the synthetic fixture into a playback queue; press **Play**, or run any one-click scenario. All data stays in memory; the only disk output is an explicit CSV export. Stop with Ctrl+C.

Run the tests:

```sh
python -m unittest discover -s tests -v
```

## The dataset

| Name | File | Content |
|---|---|---|
| `synthetic` | `fixtures/synthetic_events.jsonl` | 500 authored events, ~12% abuse, authored labels |

This is the **only** registered dataset. It contains everyday payments, campus signups, travelers, card-testing bursts, repeated trial cycles, low-and-slow reuse, and forwarding-email abuse. Labels (`abuse`, `legitimate`, `unknown`) are carried only for temporal evaluation; they never enter scoring, Bloom, DGIM, or Apriori baskets.

Rebuild the deterministic fixture with:

```sh
python tools/make_fixtures.py
```

## What is in the box

| Piece | Purpose |
|---|---|
| `engine.py` | Event-time detector: exact window, rules, Bloom/DGIM integration, Apriori, baseline shadow, evidence metrics |
| `algorithms/bloom_filter.py` | Static and rotating Bloom filters, `for_capacity` sizing, measured FPP, false-positive finder |
| `algorithms/dgim.py` | Classical DGIM buckets, invariant checks, long-stream scale demo |
| `baseline.py` | Label-free feature extraction and pure-Python logistic-regression scoring |
| `models/baseline.json` | Baseline trained on the synthetic fixture; 13 label-free features |
| `evaluation.py` | Temporal split, confusion, precision/recall/FPR, PR curve and average precision, recall at FPR, per-rule ablation |
| `datasets.py` | One-entry catalogue and synthetic JSONL loader |
| `server.py` | Loopback HTTP API plus static dashboard |
| `static/` | Demo-first dashboard: Live, Bloom, DGIM, Apriori, Evidence tabs; large-type layout and dark mode |
| `scenarios.json` | Parameters for the in-memory one-click scenarios |
| `tools/` | Fixture generation and optional baseline retraining |
| `tests/` | `test_engine.py` + `test_data_model.py` |

## Live tab

- **Dataset replay** streams the committed synthetic fixture in event-time order.
- **Scenarios**: `normal`, `card_testing`, `trial_abuse`, `multi_account_device`, `mixed`.
- **Shadow view** switches the stream/inspector between three lenses:
  1. **Exact rules** — event-time window counters and domain rules only.
  2. **Learned baseline** — logistic regression over label-free features, exported as plain JSON.
  3. **Rules + Bloom/DGIM** — full engine with small, corroborated probabilistic signals.
- **Decision inspector** shows every reason with points, window counts, Bloom result, DGIM estimate vs exact, and the exact-rules delta.
- **Entity timeline** opens from any device/card/account in the inspector.
- **Custom event** form evaluates one event and advances one simulated second.
- **Threshold sliders** re-decide stored scores without rewriting history.
- **Export CSV** writes the current session with formula-injection defence.

## Event pipeline

```
synthetic event -> validate -> advance event-time clock / expire exact window
  -> Bloom query (before insert) -> DGIM update + estimate
  -> exact-window features -> weighted rules -> score/action
  -> Bloom insert if high-risk -> store event -> evaluation state
```

The exact window is 60 seconds by default. Bloom is insert-only; DGIM is count-based over the last N stream elements. An explicit **+ window** control advances past the exact window to show that Bloom persists and DGIM does not expire by seconds.

## Scoring policy

Defaults: review **35**, block **65**. The current event is included in window features. Score is a capped 0–100 heuristic, **not** a probability.

| Rule | Points | Applies to |
|---|---:|---|
| ≥4 attempts on the same card in the window | 45 | Payment |
| Issuer failure signal | 15 | Payment |
| Amount ≥ $1,000 | 20 | Payment |
| Billing/device country mismatch | 20 | Payment |
| ≥3 distinct accounts on a device | 30 | Signup, trial, usage |
| ≥2 trial starts on a device | 30 | Signup, trial, usage |
| Temporary-email indicator (proxy) | 15 | Signup, trial, usage |
| Free-forwarding email domain (proxy) | 10 | Signup, trial, usage |
| ≥4 distinct accounts on an IP | 10 | Signup, trial, usage |
| ≥80% trial credits used | 20 | Usage |
| Trial after a recent cancellation on the same device | 20 | Trial |
| Device possibly seen before (Bloom, corroborated) | 10 | Signup, trial, usage |
| Card possibly seen before (Bloom, corroborated) | 10 | Payment |
| DGIM device burst (est. ≥ threshold, corroborated) | 10 | Any except cancel |

`cancel` events update context and score 0. Every scored event records a **shadow baseline** (exact rules only) and, when a model is present, a **learned baseline**.

## Algorithm tabs

### Bloom filter
- Fixed `m` bits, `k` hash functions (default 512/3), keyed BLAKE2b.
- Query-before-insert, high-risk-only insertion.
- Theoretical vs measured FPP, occupancy, device/card bit grids, current query positions.
- **Find a false positive** shrinks the filter until an unseen key collides, showing that “possibly present” is not proof.
- Optional rotating generations let the filter forget; scoring contribution stays +10 and corroborated.

### DGIM
- Powers-of-two buckets with timestamps, ≤2 per size, expiry outside the configured window.
- Estimate = sum(sizes) − oldest(size)/2, with the classical ~50% relative-error bound.
- Live buckets for a selected device/card, window and burst-threshold sliders.
- **1000-element scale demo** compares bucket storage with exact storage for random and worst-case streams.
- Per-entity streams are LRU-capped so long sessions stay bounded.

### Apriori
- Level-wise frequent-itemset mining over fired-signal baskets (Bloom/DGIM markers excluded), subset pruning, ≤3 items.
- Support/confidence/lift, auto-suggested support, fraud-only subset comparison.
- **Time-split check**: mine on the first half, test confidence on the second.
- Optional **offline label-as-item mode** adds `label:abuse/legitimate` for evaluation-only `signal → label` rules. Never used for scoring.

## Evidence tab

- **Temporal 70/30 split** by event time: first 70% for tuning/training, last 30% held out. No random shuffling.
- Confusion matrix, precision/recall/FPR for each view.
- PR curve and **PR-AUC** (average precision).
- **Recall @ 1% and 5% FPR**.
- **Per-rule ablation**: decisions that flip when each rule is removed (zeros reported honestly).
- Explainability note: labels are authored for teaching; the fixture is small, so results demonstrate mechanics rather than real-world fraud rates.

## Optional baseline retraining

The committed model is already trained on the synthetic fixture. To retrain:

```sh
pip install -r requirements-tools.txt
python tools/train_baseline.py
```

`tools/train_baseline.py` uses scikit-learn when available and falls back to deterministic gradient descent. The server only reads the exported JSON, so the runtime stays standard-library-only.

## API (loopback only)

`GET /api/state` (`?light=1`, `?entity=`, `?min_support=`, `?min_confidence=`) · `GET /api/datasets` · `GET /api/entity` · `GET /api/dgim_demo` · `GET /api/export`
`POST /api/event` · `/api/scenario` · `/api/scenario_run` · `/api/replay` · `/api/reset` · `/api/advance` · `/api/thresholds` · `/api/config` · `/api/apriori` · `/api/inspect` · `/api/bloom/config` · `/api/bloom/find_false_positive`

Cross-origin POSTs are rejected; responses are JSON (`/api/export` returns CSV).

## Limitations and academic boundaries

- Independent educational simulation; not Stripe software. No Stripe service or real payment data is used.
- Labels are authored for the synthetic fixture. Results demonstrate algorithm mechanics and evaluation discipline, not production fraud rates.
- Bloom positives are not proof; DGIM is approximate; Apriori co-occurrence is not causation.
- Review is a generic verification suggestion. Latency is local engine compute only.
- Not implemented: neural networks, CURE, GSP, DTW, Isolation Forest, Count-Min Sketch.

## AI-use declaration

AI coding assistance was used for implementation, dashboard design, documentation, and tests. Verify by running the scenarios and tests, understand each algorithm, and disclose this assistance in your submission.
