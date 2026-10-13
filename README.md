# Radar Lab — IA2 working model

A local, interactive simulation of **real-time payment fraud and account/free-trial abuse detection**, inspired by the business problem addressed by Stripe Radar. This is an independent educational prototype, not Stripe software. It uses synthetic events and transparent rules, not a trained model.

## Start

macOS: double-click **Start Radar Lab.command**, or in a terminal in this folder run:

```sh
python3 server.py
```

Linux / Windows (PowerShell):

```sh
python server.py
# or: python3 server.py --port 8766
```

Then visit **http://127.0.0.1:8765**. Python 3.9+ required; no packages, API keys, internet, or database. Stop with Ctrl+C. Reset clears the session (exact window, Bloom filters, DGIM streams, history).

## Five-minute demonstration

1. Run **Everyday payments**: 8 independent events, all allowed. Bloom reports `definitely_absent` on first sightings (benign events are not inserted); DGIM bits stay 0; Apriori has few/no patterns.
2. Run **Card-testing attack**: repeated small failed payments on one fictional card. Card-velocity rules raise risk to block; DGIM estimate rises for that device/card; Bloom reports `possibly_present` after the first high-risk insert; Apriori surfaces `card_velocity + failed_payment + country_mismatch` (Bloom/DGIM markers are excluded from baskets so domain signals stay visible).
3. Run **Repeated free-trial abuse**: reused device cycles signup/trial/usage/cancel with disposable email. Device/trial rules plus corroborated Bloom/DGIM signals move later trials to block.
4. Run **Shared campus Wi-Fi**: 8 legitimate accounts share one IP with distinct devices. Shared IP alone (+10) stays allowed; Bloom positives are not treated as proof; DGIM stays 0. Benign identifiers are never inserted into the filters.
5. Run **Low-and-slow reuse**: one device, a new disposable-email account every ~90 simulated seconds. The exact 60s window forgets each event (score 15, allow), while the persistent Bloom filter and the count-based DGIM window lift later events to review (35). Toggle **shadow mode** to see the exact-rules-only baseline per event; the ablation bar reports how many decisions flip.
6. Reset and run **Mixed traffic**: attacks + legitimate travelers (FP) + stealth abuse (FN). Move the review threshold to compare precision/recall. Open the explorer's evaluation-only panel for `signal → label:abuse` rules with lift, plus the time-split check (mined on the first half, tested on the second).
7. Open **Algorithm explorer**: §A Apriori (support/confidence/lift), §B DGIM (estimate vs exact + buckets + 1000-element scale demo), §C Bloom (bit grids + FPP). Click **Advance 61s**: exact 60s window expires, Bloom persists (insert-only), DGIM is count-based (last 60 elements, not seconds).
7. Send custom events reusing a card/device across accounts. Export CSV for evidence.

Playback speed changes visual delay only. Each event advances the clock 1s by default. Scenario runs use fresh `rN-` prefixes so runs don't share identities.

## Architecture and event flow

```
synthetic event -> validate -> advance clock/expire exact window
  -> Bloom query (before insert) -> DGIM update+estimate
  -> exact window features -> weighted rules -> score/action (+ shadow baseline)
  -> Bloom insert if high-risk only -> store event -> state/CSV
```

Labels (`legitimate/abuse/unknown`) never enter scoring, Bloom, DGIM, or Apriori baskets; they are used only for evaluation and the offline label-as-item Apriori mode.

## Primary analytical algorithms (syllabus)

| Algorithm | Where | What it does | Relation to score | Limitation |
|---|---|---|---|---|
| Apriori | `engine.py:Detector.apriori` | Level-wise frequent itemsets over per-event signal baskets (only fired indicators, Bloom/DGIM markers excluded), subset pruning, support ≥12% default, size ≤3; plus confidence/lift rules. Offline `include_labels` mode adds `label:abuse/legitimate` as an item for `signal → label` rules with real lift, plus a time-split check (mine first half, test second) | None — offline analysis only | Correlation ≠ causation; baskets are rule-derived so patterns can be circular; high support ≠ fraud |
| DGIM | `algorithms/dgim.py`, per-device/card streams in `engine.py` | Classical buckets (powers of two, ≤2 per size, timestamps), expiry outside last 60 elements, estimate = sum − oldest/2; `/api/dgim_demo` runs a 1000-element stream plotting estimate error and bucket count vs exact storage | Modest +10 `dgim_device_burst` only when estimate ≥5 AND current event carries failed/disposable/mismatch; never blocks alone (max +20 < review 35) | Approximate by design (≤~50% bound); window is last N elements, ≈60s only under 1-event/s playback |
| Bloom Filter | `algorithms/bloom_filter.py`, `bloom_device`/`bloom_card` in `engine.py` | 512-bit, 3 keyed BLAKE2b hashes (`key=` per function); query-before-insert; `definitely_absent`/`possibly_present`; high-risk-only insertion (suspicious bit or review/block); tracks inserts/queries/occupancy/theoretical FPP `(1−e^{−kn/m})^k` | Modest +10 `bloom_*_seen` only when corroborated (reuse/trial/disposable/failure); never blocks alone | False positives possible; no deletion (reset clears); positive ≠ proof; feedback-loop risk (past flags raise future hits — bounded by the corroboration requirement) |

Exact 60s deque + reference counters remain the enforcement source so classroom thresholds stay deterministic. DGIM/Bloom are shown separately from exact counts in the inspector and explorer.

Suspicious-bit predicate for DGIM: `1` if `failed` or `disposable` or `country_mismatch`, else `0` (`engine.suspicious_bit`). Documented, label-free.

## Scoring policy

Defaults: review 35, block 65. Current event included in window features.

| Rule | Points | Applies to |
|---|---:|---|
| ≥4 attempts on the same card in 60 seconds | 45 | Payment |
| Synthetic issuer failure signal | 15 | Payment |
| Amount ≥$1,000 | 20 | Payment |
| Billing/device country mismatch | 20 | Payment |
| ≥3 distinct accounts on a device in 60 seconds | 30 | Signup, trial, usage |
| ≥2 trial starts on a device in 60 seconds | 30 | Signup, trial, usage |
| Temporary-email indicator | 15 | Signup, trial, usage |
| ≥4 distinct accounts on an IP in 60 seconds | 10 | Signup, trial, usage |
| ≥80% trial credits used | 20 | Usage |
| Trial after a recent cancellation on the same device | 20 | Trial |
| Device possibly seen before (Bloom, corroborated) | 10 | Signup, trial, usage |
| Card possibly seen before (Bloom, corroborated) | 10 | Payment |
| DGIM device burst (est. ≥5, corroborated) | 10 | Any except cancel |

Cancel events update context and score 0. Score is a heuristic, **not a probability**. Fictional aliases only. Blocked events still enter history for teaching; the model prevents nothing real.

Every event also records a **shadow baseline** (`base_score`, exact-rules-only) so the dashboard can show what the decision would be without Bloom/DGIM. Bursty attacks are already decided by exact rules (0 flips — report this honestly); the low-and-slow scenario is where the persistent structures add value (2 of 7 flip allow→review).

## Evaluation semantics

- Review **or** block = positive; allow = negative; unknown excluded; per-event.
- Precision/Recall/FPR replay stored scores under current thresholds; history decisions unchanged.
- Labels authored for demo; not evidence of real-world effectiveness. Latency = local engine compute only.

## Academic boundaries

Independent educational simulation, not Stripe software. No Stripe service is called. Algorithms, window lengths, weights, and thresholds are teaching choices, not claims about Stripe's proprietary implementation.

Does not implement neural networks, CURE, GSP, DTW, Isolation Forest, or Count-Min Sketch (removed; see migration note). The cancel-to-trial rule is a predicate, not sequence mining. Shared IP is weak evidence kept below review. Review is a generic verification suggestion, not Stripe payment-review semantics.

Migration note: versions before this change used Count-Min Sketch for lifetime device counts. It has been fully removed and replaced by DGIM + Bloom Filter per syllabus scope.

## Files and verification

- `engine.py`: detection, exact window, Bloom/DGIM integration, Apriori + rules, scenarios.
- `algorithms/bloom_filter.py`: standard Bloom filter.
- `algorithms/dgim.py`: classical DGIM.
- `server.py`: loopback HTTP, `/api/state|event|scenario|reset|advance|thresholds|config|inspect|dgim_demo`, CSV export.
- `static/`: dashboard + §A/B/C explorer, shadow toggle, DGIM scale demo, no third-party deps.
- `tests/test_engine.py`: 37 tests (engine, Apriori incl. aux-exclusion/label-eval/time-split, DGIM incl. 1000-element trade-off, Bloom incl. keyed hashing/conditional insert, low-and-slow shadow integration).

```sh
python3 -m unittest discover -s tests -v
```

Single shared in-memory session; refresh keeps server state but ends playback. Nothing written to disk except explicit CSV download.

## AI-use declaration

Codex assisted with implementation, dashboard design, documentation and tests. Verify by running the scenarios, understand each algorithm, and disclose this assistance in your submission.
