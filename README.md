# Radar Lab — IA2 working model

A local, interactive simulation of **real-time payment fraud and account/free-trial abuse detection**, aligned with the supplied Stripe Radar report. This is an independent educational prototype, not Stripe software. It uses synthetic events and transparent rules, not a trained model.

## Start

On macOS, double-click **Start Radar Lab.command**. Leave its Terminal window open while presenting. If macOS does not open the launcher, open Terminal in this folder and run:

```sh
python3 server.py
```

Then visit **http://127.0.0.1:8765**. Python 3.9 or newer is required; no package installation, API key, internet connection or database is needed. Stop with Control+C. Restarting or resetting clears the session. If the port is busy, stop the old server or use `python3 server.py --port 8766` and visit that port.

## Five-minute demonstration

1. Run **Everyday payments**: all eight events are allowed.
2. Run **Card-testing attack**: repeated attempts on one fictional card lead to blocks; select an event to inspect its score.
3. Run **Repeated free-trial abuse**: multiple accounts on a reused device cycle through signup, trial, usage and cancellation. Later trial attempts accumulate sufficient evidence to block.
4. Run **Shared campus Wi-Fi**: eight legitimate accounts share one IP but use different devices. They remain allowed under the default policy.
5. Reset and run **Mixed traffic**: includes obvious attacks, ambiguous legitimate travelers, and stealth abuse. Discuss false positives and false negatives. Move the review threshold to compare precision and recall.
6. Open **Algorithm explorer**: examine exact counts, Count-Min Sketch estimates and Apriori signal patterns. Click **Advance 61s** to expire recent counts while preserving session-wide sketch counts and history.
7. Send custom events using the same card or device and different accounts. Export the session as CSV for evidence.

Playback speed changes the visual delay, not the event timestamps. Each event advances the simulated clock one second. Run scenarios consecutively to combine them; use Reset for an isolated experiment. Scenario runs use fresh identifiers so separate runs do not accidentally share identities. Pause/resume preserves the remaining queue.

## Implementation and syllabus mapping

| Component | Working implementation | Purpose |
|---|---|---|
| Data stream | Sequential synthetic events consumed by Python | Continuous arrivals |
| Sliding window | Deque plus keyed reference counters over `(now − 60, now]` | Exact recent attempts and identity reuse |
| Risk filtering | Explicit weighted predicates; score capped at 100 | Explainable allow/review/block |
| Count-Min Sketch | 4 seeded BLAKE2b hash rows × 64 counters | Approximate lifetime device-event frequency |
| Apriori | Level-wise candidates, subset pruning, support ≥12%, max size 3 | Offline signal co-occurrence |
| Evaluation | Confusion matrix, precision and recall from separate synthetic labels | Threshold trade-offs |

The stream engine uses expected O(1) keyed updates and amortized O(1) expiry per event; a single arrival after a time jump may expire many entries. Window storage scales with active events and keys. Sketch update/query costs O(depth); there are 256 counters, represented by Python integers (not a claim of a 1 KB Python memory footprint). Exact lifetime counts are kept for classroom comparison. Apriori and dashboard snapshots scan session history and are **not** constant-time streaming operations. Session history is capped at 2,000 events; the table shows the latest 150 and the chart the latest 40 occupied 10-second buckets. Export includes the whole session.

## Scoring policy

Default review threshold: 35. Default block threshold: 65. The current event is included in its window features.

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

Cancellation events update context and have zero risk score; their demo truth is unknown. The score is a heuristic, **not a probability**. Identifiers are fictional aliases; do not enter real card numbers. The pre-supplied failure signal represents a hypothetical known issuer response, not a prediction of authorization failure. Decisions are simulated recommendations: blocked events still enter the attempt history, and future authored events are still played to illustrate detection. The model does not actually prevent payments or stop resource usage.

## Evaluation semantics

- Truth labels belong to the scenario and never influence the scoring function.
- Review **or** block counts as a positive detection; allow counts as negative.
- Precision = TP/(TP+FP). Recall = TP/(TP+FN). Undefined denominators display a dash.
- Unknown labels are excluded. All metrics are per-event, not per-account.
- Changing thresholds re-evaluates stored scores for the evaluation panel. Historical event decisions, chart and counters remain as recorded. New events use the new thresholds.
- These labels and patterns are authored, not independently validated. Performance is not evidence of real-world effectiveness. Processing time measures only local engine computation, excluding network, rendering, history serialization and offline analytics.

## Academic boundaries

The supplied report distinguishes publicly documented Stripe capabilities from academic candidates. This model preserves that distinction. The window length, weights, synthetic data, sketch and Apriori configuration are our teaching choices. There is no claim Stripe uses these algorithms or settings. No Stripe service is called.

This model does not implement a neural network, CURE, GSP, exponential histogram or DTW. The cancellation-to-trial rule is a simple recent-history predicate, not sequence mining. Shared IP is weak evidence; current defaults keep it below review. The campus example illustrates one false-positive safeguard, not a fairness guarantee.

Review is a generic review/verification recommendation, not a re-creation of Stripe’s payment-review action semantics. The demonstration does not capture funds, invoke authentication, implement issuer authorization or enforce trial restrictions. Future improvements could include validated models trained on independent data, outcome feedback, device-identity reliability checks and drift monitoring.

## Files and verification

- `engine.py`: detection, counters, sketch, Apriori and scenario generation.
- `server.py`: loopback-only HTTP service with JSON endpoints and CSV export.
- `static/`: dashboard with no third-party dependencies.
- `tests/test_engine.py`: meaningful engine tests, including expiry boundaries, false positives, label independence, collisions and Apriori.

Run the tests from this folder:

```sh
python3 -m unittest discover -s tests -v
```

The service binds to the local computer only. It is a single shared in-memory classroom session, not a production multi-user service. Refreshing the page retains server state but ends browser-driven playback. No data is written to disk except a CSV you explicitly download.

## AI-use declaration

Codex assisted with implementation, dashboard design, documentation and tests. Students should verify the behavior by running the scenarios, understand each algorithm and disclose this assistance in their submission. The supplied assignment and report were treated as reference material; no report was submitted or modified.
