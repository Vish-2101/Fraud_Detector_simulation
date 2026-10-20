# Radar Lab

A local, dependency-free simulation of **real-time payment fraud and account / free-trial abuse detection**, inspired by the business problem addressed by Stripe Radar. This is an independent educational prototype, not Stripe software. The demo uses one committed **synthetic** event log and transparent rules plus the syllabus algorithms (Bloom filter, DGIM, Apriori) and optional learned baselines. No API keys, internet, external datasets, or database are needed.

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

The UI helper tests run with Node's built-in test runner (Node 18+), invoked by
`tests/test_apriori_view.py` and `tests/test_dgim_tab.py`. They need no npm
packages.

## The dataset

| Name | File | Content |
|---|---|---|
| `synthetic` | `fixtures/synthetic_events.jsonl` | 2,519 authored events over 8.5 hours, 16% abuse, 3% unknown |

This is the **only** registered dataset. It interleaves 16 scenario families across the timeline: everyday payments, campus signups, travelers, free forwarding, large purchases, card-testing bursts, repeated trials, multi-account abuse, stealth abuse, low-and-slow device/card reuse, mixed streams, limited-memory forgetting, recidivist returns, DGIM negatives, and Bloom collision controls. `fixtures/synthetic_manifest.json` records scenario instances, event ranges, entities, expected algorithm contributions, held-out coverage, and the deliberate collision positions.

Labels (`abuse`, `legitimate`, `unknown`) are carried only for temporal evaluation; they never enter scoring, Bloom, DGIM, or Apriori baskets.

Rebuild both deterministic files with:

```sh
python tools/make_fixtures.py
```

The generator uses a fixed seed and emits the same bytes on every run. `tests/test_synthetic_fixture.py` replays the fixture and checks the manifest, temporal coverage, algorithm attribution, ablation behavior, collision controls, Apriori combinations, label isolation, and byte-identical regeneration.

## What is in the box

| Piece | Purpose |
|---|---|
| `engine.py` | Event-time detector: exact window, rules, Bloom/DGIM integration, Apriori, baseline shadow, evidence metrics |
| `algorithms/bloom_filter.py` | Static and rotating Bloom filters, `for_capacity` sizing, measured FPP, false-positive finder |
| `algorithms/dgim.py` | Classical DGIM buckets, invariant checks, long-stream scale demo |
| `baseline.py` | Label-free feature sets (`raw`, `raw+BD`, `all`) and pure-Python logistic-regression scoring |
| `models/baseline.json` | Original baseline trained on the synthetic fixture; 13 label-free features |
| `models/ladder_*.json` | Three Evidence-ladder models with scaler, metadata, and fixture hash |
| `evaluation.py` | Temporal split, ladder rows, paired bootstrap intervals, confusion, PR curve/AP, recall at FPR, per-rule ablation |
| `datasets.py` | One-entry catalogue and synthetic JSONL loader |
| `server.py` | Loopback HTTP API plus static dashboard |
| `static/` | Demo-first dashboard: Live, Bloom, DGIM, Apriori, Evidence tabs; large-type layout and dark mode |
| `scenarios.json` | Parameters for the in-memory one-click scenarios |
| `tools/` | Fixture generation plus optional baseline and Evidence-ladder retraining |
| `tests/` | Engine/data/Evidence tests plus Python wrappers for the Node UI-helper tests |
| `docs/EVIDENCE_LADDER.md` | Ladder definitions, results, confidence intervals, and presenting script |

## Live tab

- **Dataset replay** streams the committed synthetic fixture in event-time order.
- **Decision alerts**: the Allowed, Review, and Blocked cards flash green, amber, or red for each new event, with matching beeps that can be muted via **Sound on/off**.
- **Scenarios**: `normal`, `card_testing`, `trial_abuse`, `multi_account_device`, `mixed`.
- **Shadow view** switches the stream/inspector between two lenses:
  2. **Learned baseline** — logistic regression over label-free features, exported as plain JSON.
  3. **Bloom/DGIM** — rules plus small, corroborated probabilistic signals.
- **Decision inspector** shows every reason with points, window counts, Bloom result, DGIM estimate vs exact, and the exact-rules delta.
- **Entity timeline** opens from any device/card/account in the inspector.
- **Custom event** form evaluates one event and advances one simulated second.
- **Threshold sliders** re-decide stored scores without rewriting history. The review and block handles each slide the full 0–100 range independently and may cross.
- **Export CSV** writes the current session with formula-injection defence.

## Event pipeline

```
synthetic event -> validate -> advance event-time clock / expire exact window
  -> Bloom query (before insert) -> DGIM update + estimate
  -> exact-window features -> weighted rules -> score/action
  -> Bloom insert if high-risk -> store event -> evaluation state
```

The exact window is 60 seconds by default. Bloom is insert-only; DGIM is count-based over the last N stream elements, so Bloom persists after exact-window expiry while DGIM does not expire by seconds.

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

### What the learned baseline is and why it is compared

The learned baseline is a logistic-regression model over 13 **label-free** features: amount, amount ≥ $1,000, temporary email, country mismatch, failed payment, device/account and card velocities, device trial count, IP-account count, the DGIM device estimate, the Bloom device/card lookups, and the current rule score. It is trained on the first 70% of labeled events by event time and evaluated on the held-out last 30%; it is exported to `models/baseline.json` and scored in pure Python at runtime.

It is deliberately a **shadow comparator**, not the production decision maker. Every event keeps its rules-based action, while the baseline records its own 0–100 score and action alongside it using the same review/block thresholds. Comparing the two answers the questions the report needs: does a learned model add ranking value over transparent rules, and how much do the Bloom/DGIM signals change decisions? The Bloom/DGIM and learned-baseline views are evaluated on the same temporal holdout with PR-AUC, recall at 1%/5% FPR, precision/recall/FPR, confusion counts and per-rule ablation. Labels are used only to fit the baseline and to score the held-out evaluation; they never enter features, scoring, Bloom, DGIM or Apriori baskets.

The Evidence ladder extends this into a controlled comparison. `baseline.py` defines three label-free feature sets — `raw`, `raw+BD`, and `all` — and `tools/train_ladder.py` exports one model for each. The raw set excludes the rule score, Bloom, DGIM, and reason flags, so LR-raw is an independent learned view. LR-all is explicitly a reference because it consumes the engine's own outputs. See [`docs/EVIDENCE_LADDER.md`](docs/EVIDENCE_LADDER.md).

### How the score is determined

1. Start from 0 and add every row triggered in the rule table above. Counts use the current event-time window and include the current event. The exact-rules component is capped at 100.
2. Add corroborated auxiliary points: Bloom can add at most +10 and DGIM at most +10 for the same event. They never flag an event on their own.
3. The displayed score is `min(100, exact-rules points + Bloom points + DGIM points)`.
4. The action is `allow` below the review threshold, `review` at or above it, and `block` at or above the block threshold. Defaults are review **35** and block **65**; both are adjustable. `cancel` events update context but score 0.
5. The learned baseline uses its own model score with the same review/block thresholds; it does not change the live action.

### Flagging conditions by algorithm

- **Exact rules:** an event is flagged when its summed rule score reaches the review threshold; it is blocked when the score reaches the block threshold. The conditions and points are the table above.
- **Bloom filter:** query-before-insert returns `possibly_present` or `absent`. A positive becomes +10 only when corroborated: lifecycle events need a seen device plus at least two device accounts, a trial, or a temporary email; payments need a seen card plus at least two card attempts or a failure. Otherwise it contributes nothing and never flags alone. High-risk events (suspicious bit or a non-allow action) are inserted after the query.
- **DGIM:** each device/card stream stores the `suspicious_bit` (failed, temporary email, country mismatch, or free-forwarding email). A burst exists when the estimate is at least the threshold (default 5). It adds +10 only for a non-cancel suspicious event with a corroborating suspicious attribute; it never flags alone.
- **Apriori:** it does not flag individual events or change their score. Offline, it flags frequent itemsets whose support is at least the selected minimum (auto-suggested or slider) and association rules whose confidence is at least the minimum. Bloom/DGIM markers are excluded from baskets; the optional label-as-item mode is evaluation-only.
- **Learned baseline:** `p = sigmoid(intercept + Σ weight_i × (feature_i − mean_i) / scale_i)`, with a 0–100 score of `100 × p`. It flags as review at 35 and block at 65, but remains a shadow comparison only.

## Algorithm tabs

### Bloom filter
- Query-only checker with fixed `m=512`, `k=3`, keyed BLAKE2b; inserts and queries share one normalisation and position function.
- Every check returns exactly one result: `definitely_absent`, `true_positive`, or `false_positive (hash collision)`.
- Device/card bit grids highlight the current k positions and label each one lit or dark. Checks never set bits, insert, or change scoring.
- A demo-only exact side-set maps identifiers to their first inserting event and each bit to its source identifiers. It never reads labels and is never used for scoring; a real Bloom filter cannot distinguish true from false positives.
- Wrong-kind or account-ID queries add a hint and one-click checks for the event's device and card.
- Query-before-insert, high-risk-only insertion, and the corroborated +10 contribution are unchanged.

### DGIM
- Powers-of-two buckets with timestamps, ≤2 per size, expiry outside the configured window.
- Estimate = sum(sizes) − oldest(size)/2, with the classical ~50% relative-error bound.
- Ranked device/card list with a BURST / WATCH / NORMAL verdict, latest-event DGIM result, one stream cell per event with bucket overlays, and a history chart that pins the first +10.
- Window and burst-threshold changes re-evaluate the verdict immediately without rewriting stored event scores.
- **1000-element scale demo** compares bucket storage with exact storage for random and worst-case streams.
- Per-entity streams are LRU-capped so long sessions stay bounded.
- Demo script: [`docs/DGIM_TAB_WALKTHROUGH.md`](docs/DGIM_TAB_WALKTHROUGH.md).

### Apriori
- Plain-language workflow: choose **All events** or the abuse-labelled comparison subset, then pick **Broad** (6% / 10%), **Balanced** (12% / 26%, default) or **Strict** (20% / 45%). Advanced sliders and suggested support remain available.
- Level-wise frequent-itemset mining over fired-signal baskets (Bloom/DGIM markers excluded), subset pruning, ≤3 items.
- Patterns are shown as sentences with support, confidence, lift and a Strong / Moderate / No real link badge; selecting one opens its matching events with highlighted signal chips. An optional level stepper shows candidates, frequent sets and pruning.
- **Time-split check**: mine on the first half, test confidence on the second, and label each rule Holds up / Does not hold up / Not enough data.
- On the 2,519-event fixture, support is measured over all replayed events, so the Balanced 12% floor needs about 303 co-occurrences and admits no itemset. Lower the support to about 1% (roughly 26 events) to surface the seeded combinations `card_velocity + failed_payment + country_mismatch` and `device_reuse + repeat_trial + temporary_email`; `failed_payment` and `temporary_email` also appear individually.
- Optional **offline label-as-item mode** adds `label:abuse/legitimate` for evaluation-only `signal → label` rules. Never used for scoring, and label rules are hidden from the normal pattern and time-split lists.
- Guided demo script: [`docs/APRIORI_TAB_WALKTHROUGH.md`](docs/APRIORI_TAB_WALKTHROUGH.md).

## Evidence tab

- **Temporal 70/30 split** by event time: first 70% for tuning/training, last 30% held out. No random shuffling.
- **Seven-row comparison ladder** on the same holdout: allow-all, amount ≥ $1,000, exact rules, full engine, LR-raw, LR-raw+BD, and LR-all (reference only).
- Columns show whether each row is trained, whether it uses the rule score, whether it uses Bloom/DGIM, PR-AUC with a 95% bootstrap interval, recall @ 1%/5% FPR, and precision/recall/FPR at the review threshold.
- **Paired bootstrap intervals** (500 resamples, fixed seed) run on demand and are cached; they do not run on every live event.
- **Overlaid PR curves** for rows 3–7 plus per-model cards with `C`, training counts, time range, and top weights.
- **Stale-model warning**: if the fixture SHA-256 no longer matches a model, the tab says “model is stale, retrain”.
- **Per-rule ablation**: decisions that flip when each rule is removed (zeros reported honestly).
- Full definitions, results, caveats, and a presenting script: [`docs/EVIDENCE_LADDER.md`](docs/EVIDENCE_LADDER.md).
- Explainability note: labels are authored for teaching; the fixture is compact, so results demonstrate mechanics rather than real-world fraud rates.

## Optional model retraining

The committed models are already trained on the synthetic fixture. To retrain the original baseline:

```sh
pip install -r requirements-tools.txt
python tools/train_baseline.py
```

To retrain all three Evidence-ladder models:

```sh
pip install -r requirements-tools.txt
python tools/train_ladder.py
```

Both tools use scikit-learn when available and fall back to deterministic gradient descent. The server only reads exported JSON, so the runtime stays standard-library-only.

## API (loopback only)

`GET /api/state` (`?light=1`, `?entity=`, `?entity_kind=`, `?min_support=`, `?min_confidence=`) · `GET /api/evidence` (`?bootstrap=0|1`) · `GET /api/datasets` · `GET /api/entity` · `GET /api/dgim_demo` · `GET /api/export`
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
