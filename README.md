# Radar Lab

A local, dependency-free simulation of **real-time payment fraud and account / free-trial abuse detection**, inspired by the business problem addressed by Stripe Radar. This is an independent educational prototype, not Stripe software. Engines are transparent rules plus the syllabus algorithms (Bloom filter, DGIM, Apriori) and an optional learned baseline. No API keys, internet, or database are needed at runtime.

## Quick start

```sh
python server.py                  # http://127.0.0.1:8765
python server.py --port 8766
python server.py --dataset ieee_cis --sample 50000 --window-seconds 3600
```

The server uses only the Python standard library (3.9+). On boot the UI auto-loads the selected dataset into a playback queue; press **Play** or **Step**, or run any one-click demo. All data stays in memory; the only disk output is an explicit CSV export. Stop with Ctrl+C.

Run the tests:

```sh
python -m unittest discover -s tests -v
```

## What is in the box

| Piece | Purpose |
|---|---|
| `engine.py` | Event-time detector: exact window, rules, Bloom/DGIM integration, Apriori, baseline shadow, evidence metrics |
| `algorithms/bloom_filter.py` | Static and rotating Bloom filters, `for_capacity` sizing, measured FPP, false-positive finder |
| `algorithms/dgim.py` | Classical DGIM buckets, invariant checks, long-stream scale demo |
| `baseline.py` | Label-free feature extraction and pure-Python logistic-regression scoring |
| `evaluation.py` | Temporal split, confusion, precision/recall/FPR, PR curve and average precision, recall at FPR, per-rule ablation |
| `adapters/ieee_cis.py` | IEEE-CIS row → Radar Lab event mapping with documented proxies |
| `adapters/stripe_events.py` | Stripe charge/subscription event mapping; Radar risk fields kept for comparison only |
| `datasets.py` | Catalogue and loaders for synthetic, IEEE-CIS processed, IEEE-CIS fixture, Stripe fixture |
| `server.py` | Loopback HTTP API plus static dashboard |
| `static/` | Demo-first dashboard: Live, Bloom, DGIM, Apriori, Evidence tabs; presentation and dark modes |
| `tools/` | Optional offline tooling (Kaggle prep, baseline training, Stripe seeding/replay) |
| `tests/` | `test_engine.py` (37 tests) + `test_data_model.py` (21 tests) = 58 |

## Datasets

| Name | Source | Offline | Notes |
|---|---|---:|---|
| `synthetic` | `fixtures/synthetic_events.jsonl` | yes | Authored scenarios for classroom demonstrations; labels authored |
| `ieee_cis_fixture` | `fixtures/ieee_cis_sample.jsonl` | yes | 500-row synthetic stand-in with the IEEE-CIS event schema; **not real transactions** |
| `stripe_fixture` | `fixtures/stripe_events_sample.json` | yes | 40 hand-built Stripe-shaped events; **not real fraud** |
| `ieee_cis` | `data/processed/ieee_cis_events.jsonl` | no | Real Vesta transactions after local Kaggle preparation; never committed |

Raw and processed datasets are git-ignored (`data/raw/`, `data/processed/`). Only the tiny fixtures ship with the repository.

### Real IEEE-CIS replay

Download `train_transaction.csv` and `train_identity.csv` from Kaggle (`ieee-fraud-detection`) into `data/raw/ieee_cis/`, then:

```sh
pip install -r requirements-tools.txt
python tools/prepare_ieee_cis.py --raw data/raw/ieee_cis \
    --out data/processed/ieee_cis_events.jsonl --sample 50000
python server.py --dataset ieee_cis --sample 50000 --window-seconds 3600
```

`--sample` is always a **time-contiguous prefix**, never a random sample, so the local fraud rate and ordering are preserved.

### Stripe sandbox (optional)

The committed fixture works offline. To seed a Stripe test-mode sandbox and replay it (`pip install -r requirements-tools.txt`, set `STRIPE_SECRET_KEY`):

```sh
python tools/stripe_sandbox_seed.py
python tools/stripe_webhook_replay.py --source http://127.0.0.1:8765
```

Stripe test-mode data is deterministic and is **not** real fraud. Stripe Radar `risk_level` / `risk_score` are copied to `stripe_risk_level` / `stripe_risk_score` for side-by-side comparison only and are never scoring, Bloom, DGIM, or Apriori inputs.

## Replay, scenarios, and evaluation

- **Dataset replay** streams a time-contiguous prefix into `/api/event`; the event-time clock is the dataset timestamp, and the exact window and DGIM window follow the dataset (60 s synthetic, 3600 s IEEE-CIS).
- **Scenarios** (`scenarios.json`): `normal`, `card_testing`, `trial_abuse`, `shared_ip`, `mixed`, `low_slow`, `multi_account_device`, `low_slow_reuse`, `free_forwarding_email`, `shared_campus_wifi_fp_trap`. They are parameterized and documented in the file.
- **Evidence tab** reports a temporal 70/30 split (train on the first 70% of labeled time-ordered events, test on the rest) for three views: **exact rules**, **full engine**, and **learned baseline**. For each view it shows confusion, precision/recall/FPR, PR-AUC (average precision), recall at 1% and 5% FPR, and a per-rule ablation of decisions on the test split.

Example fixture run (values move with data and threshold; reproduce with the commands above):

| Dataset | View | PR-AUC | Recall @ 5% FPR |
|---|---|---:|---:|
| `ieee_cis_fixture` | exact rules + Bloom/DGIM | ~0.42 | ~0.40 |
| `ieee_cis_fixture` | learned baseline | ~0.97 | ~1.00 |

### Measured fixture results (example, committed model trained on synthetic)

| Dataset | View | PR-AUC | Recall @ 5% FPR | Confusion at review 35 |
|---|---|---:|---:|---|
| `synthetic` (500 rows) | exact / full / learned | 1.00 | 1.00 | tp 0, fn 8 (ranking perfect; fixed threshold under-flags the test split) |
| `ieee_cis_fixture` (500 rows) | exact / full | 0.42 | 0.40 | tp 0, fn 5 |
| `ieee_cis_fixture` (500 rows) | learned baseline | 0.9667 | 1.00 | tp 5, fp 1, fn 0 |
| `stripe_fixture` (40 events) | — | no labels | — | actions allow 23, review 13, block 4; Stripe risk shown side-by-side only |

On `ieee_cis_fixture` only `country_mismatch` fired (10 events); `card_velocity`, `large_amount`, `failed_payment`, and all lifecycle/Bloom-device rules stayed inactive for the payment-only mapping. These are fixture numbers, not real-world effectiveness.

The committed `models/baseline.json` is trained on the **synthetic** fixture. For an honest real-data report, retrain on the prepared dataset:

```sh
python tools/train_baseline.py --dataset ieee_cis --out models/baseline.json
```

## Proxy mapping and inactive rules (IEEE-CIS)

IEEE-CIS has no IP address, no issuer-failure flag, and no merchant-country field. The mapping is transparent about every proxy:

| Engine signal | IEEE-CIS source | Documented caveat |
|---|---|---|
| Disposable email | `P_emaildomain` in `DISPOSABLE_DOMAINS` | Dataset does not mark addresses as disposable; short documented list |
| Forwarding email | `P_emaildomain` in `FORWARDING_DOMAINS` | Separate proxy; not throwaway mailboxes |
| Country mismatch | `addr2` present AND `dist1 > 0` | Proximity proxy, **not** a verified country comparison |
| Device | `DeviceInfo`, `id_30`, `id_31`, `id_33` hashed | May be missing → `device = null`; device-reuse rule needs lifecycle events |
| Card | `card1..card5` hashed | — |
| IP | `addr1` hashed | Weak region alias; the dataset has no IP |
| Failed payment | none | Always `false`; issuer-failure rule never fires |

Because IEEE-CIS rows map to `payment` events, lifecycle rules are inactive there: `device_reuse`, `repeat_trial`, `heavy_usage`, `trial_after_cancel`, `shared_ip`, `temporary_email`, `free_forwarding_email`, and the corroborated `bloom_device_seen`. Scoring-active rules are `card_velocity`, `large_amount`, `country_mismatch`, and (comparison-only) `bloom_card_seen` / `dgim_device_burst`. `adapters/ieee_cis.py:active_rules()` returns this list for any loaded dataset and is a documentation aid, not a scoring input.

## Scoring policy

Defaults: review **35**, block **65**. The current event is included in window features. Score is a heuristic, **not** a probability.

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

`cancel` events update context and score 0. Every scored event also records a **shadow baseline** (`base_score`, exact rules only) and, when a model is present, a **learned baseline** view. Labels never enter scoring, Bloom, DGIM, or Apriori baskets; they are evaluation-only (or a subset selector for the offline `include_labels` Apriori mode).

## Algorithm notes

- **Bloom filter**: keyed BLAKE2b hashes, query-before-insert, insert only high-risk identifiers, `for_capacity(n, fpp)` sizing, theoretical and measured FPP, optional rotating generations. A positive is **not** proof and never blocks alone (max +10, corroborated).
- **DGIM**: powers-of-two buckets with ≤2 per size and timestamps, expiry outside the configured window, estimate = sum − oldest/2. It is approximate by design and only adds a small corroborated signal. Streams are LRU-bounded per device/card.
- **Apriori**: level-wise frequent itemsets over fired-signal baskets (Bloom/DGIM markers excluded so domain signals stay visible), subset pruning, ≤3 items, configurable support/confidence. An offline `include_labels` mode adds `label:abuse/legitimate` as a basket item for evaluation-only `signal → label` rules with lift and a train/test time split. Correlation is not causation, and baskets are rule-derived.

## API (loopback only)

`GET /api/state` (`?light=1`, `?entity=`, `?min_support=`, `?min_confidence=`) · `GET /api/datasets` · `GET /api/entity` · `GET /api/dgim_demo` · `GET /api/export`
`POST /api/event` · `/api/scenario` · `/api/scenario_run` · `/api/replay` · `/api/reset` · `/api/advance` · `/api/thresholds` · `/api/config` · `/api/apriori` · `/api/inspect` · `/api/bloom/config` · `/api/bloom/find_false_positive` · `/api/stripe_webhook`

Cross-origin POSTs are rejected; responses are JSON (`/api/export` returns CSV with formula-injection defence).

## Limitations and academic boundaries

- Independent educational simulation; not Stripe software. No Stripe service is called. Algorithms, windows, weights, and thresholds are teaching choices, not claims about Stripe's implementation.
- Stripe sandbox/fixture data is not real fraud and cannot demonstrate real-world effectiveness. IEEE-CIS fixture rows are synthetic; real results require the prepared Kaggle data and retraining the baseline.
- IEEE-CIS proxies (disposable email, country mismatch, IP region) are documented approximations. Report inactive rules honestly rather than implying coverage.
- Review is a generic verification suggestion, not Stripe payment-review semantics. Latency is local engine compute only.
- Not implemented: neural networks, CURE, GSP, DTW, Isolation Forest, Count-Min Sketch. The former Count-Min Sketch was removed and replaced by Bloom + DGIM.

## What to report

For each of `synthetic`, `ieee_cis_fixture`, and `ieee_cis` (if prepared), record for the **exact**, **full**, and **learned** views: PR-AUC, recall @ 5% FPR, confusion at the review threshold, and the rules that fired vs stayed inactive. Retrain the baseline on the dataset you report. State the dataset, sample size, window, and whether you used the committed fixture or real Kaggle data.

## AI-use declaration

AI coding assistance was used for implementation, dashboard design, documentation, and tests. Verify by running the scenarios and tests, understand each algorithm, and disclose this assistance in your submission.
