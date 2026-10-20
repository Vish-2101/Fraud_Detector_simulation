# Evidence ladder walkthrough

The Evidence tab answers a narrower question than “which model is best”: **on the same held-out events, what does each added ingredient buy?** It compares seven rows without changing any live decision.

## Split and label policy

- First **70% of labeled events by event time** train and tune; the last **30%** is held out.
- Inside the training part, the **last 20% by time** chooses the regularization strength `C`.
- The test split is never used for scaling, `C`, thresholds, or any other choice.
- Labels only build `y`. Features, scoring, Bloom, DGIM, and Apriori never read them.
- The committed fixture yields **1,710 training events (233 abuse)** and **733 test events (171 abuse)** under the engine's event-time split.

## The seven rows

| # | Row | Trained? | Uses rule score? | Uses Bloom/DGIM? | What it isolates |
|---|---|---|---|---|---|
| 1 | Allow everything (reference) | No | No | No | Reference: no decisions at all |
| 2 | Single rule: `amount >= $1,000` | No | No | No | One hand-written rule in isolation |
| 3 | Exact rules only (existing shadow) | No | Yes | No | Transparent rules before auxiliary signals |
| 4 | Full engine: rules + Bloom/DGIM | No | Yes | Yes | What Bloom/DGIM add to the rules |
| 5 | LR-raw | Yes | No | No | A learned model on raw event signals |
| 6 | LR-raw+BD | Yes | No | Yes | What Bloom/DGIM add to a learned model |
| 7 | LR-all | Yes | Yes | Yes | Reference only: learned model on the engine's own outputs |

Row 7 is deliberately **not** an independent baseline. It sees `rule_score`, Bloom lookups, and the DGIM estimate, so it cannot tell you whether those signals are fair or useful. It is a ceiling/reference check.

## Feature sets

Defined once in `baseline.py`:

**raw** — `amount`, `log1p(amount)`, `amount >= 1000`, temporary email, forwarding email, country mismatch, failed payment, a one-hot event type, device-account count, card-attempt count, device-trial count, and IP-account count.

**raw+BD** — `raw` plus the Bloom device lookup, Bloom card lookup, and DGIM device estimate.

**all** — the existing 13 features: `amount_log`, `amount_ge_1000`, `disposable`, `country_mismatch`, `failed`, device/card/trial/IP velocities, `dgim_device_est`, the two Bloom flags, and `rule_score`.

The raw set intentionally excludes `rule_score`, Bloom, DGIM, and reason flags. `tests/test_evidence_ladder.py` enforces that separation.

Every replayed event records all three vectors in `event['model_features']`. Training and the Evidence report read those stored vectors rather than re-deriving features with different logic.

## Training protocol

- Strict time split: 70% train / 30% test.
- Validation is the last 20% of the training window.
- `C` is chosen from `[0.01, 0.1, 1, 10]` by validation average precision.
- The scaler is fit on training rows only; final models refit on all 70% training rows with the chosen `C`.
- `class_weight="balanced"`, fixed seed, deterministic export.
- scikit-learn is used when installed; otherwise a deterministic full-batch gradient-descent fallback runs with L2 regularization, a fixed iteration cap, and a convergence tolerance.
- Exports: `models/ladder_raw.json`, `models/ladder_raw_bd.json`, `models/ladder_all.json`. Each records feature order, mean, scale, weights, intercept, `C`, training counts, training time range, and the fixture SHA-256.
- If the fixture hash changes, the server shows **“model is stale, retrain”**.
- The runtime scorer is pure Python: `p = sigmoid(intercept + Σ w_i × (x_i − mean_i) / scale_i)`, then `score = 100 × p`. Learned rows remain shadow views and never change the live action.

## Results from the committed fixture

Review threshold 35. Full engine and learned rows use the same threshold. Intervals are 95% percentile intervals from **500 paired bootstrap resamples** of the 733 test events (fixed seed `20261020`).

| Row | PR-AUC (95% CI) | Recall @ 1% FPR | Recall @ 5% FPR (95% CI) | Precision / Recall / FPR @ review |
|---|---|---|---|---|
| Allow everything (reference) | 0.233 (0.205–0.263) | 0.0% | 0.0% (0.0–0.0) | — / 0.0% / 0.0% |
| Amount ≥ $1,000 | 0.233 (0.205–0.263) | 0.0% | 0.0% (0.0–0.0) | 0.0% / 0.0% / 1.8% |
| Exact rules | 0.821 (0.767–0.869) | 37.4% | 37.4% (30.9–87.6) | 92.8% / 37.4% / 0.9% |
| Full engine | 0.907 (0.867–0.943) | 59.6% | 82.5% (76.7–89.4) | 95.2% / 57.3% / 0.9% |
| LR-raw | 0.961 (0.937–0.982) | 91.8% | 94.2% (89.3–97.3) | 87.5% / 94.2% / 4.1% |
| LR-raw+BD | 0.960 (0.934–0.981) | 94.2% | 94.2% (90.5–97.5) | 95.3% / 94.2% / 1.4% |
| LR-all (reference) | 0.959 (0.933–0.980) | 93.0% | 94.2% (90.5–97.5) | 87.0% / 94.2% / 4.3% |

### Honest result worth saying aloud

The single amount rule finds **0 true positives** on this split: all 20 test events with `amount >= $1,000` are labeled legitimate. Its PR-AUC therefore equals the all-allow reference. That is a fixture property, not a coding mistake, and it is a useful reminder that a plausible rule can be useless on a small authored sample.

The learned rows sit well above the engine rows, and LR-raw+BD has the best precision at the review threshold. But the PR-AUC confidence intervals for LR-raw, LR-raw+BD, and LR-all overlap heavily. Do **not** claim one learned variant is definitively best. Bloom/DGIM do not lift LR-raw's PR-AUC here, though they improve review-threshold precision and recall at 1% FPR. Report that as a mixed result.

## Reading guide (also shown in the UI)

- **Row 5 vs row 4:** does a learned model on raw signals match the hand-built engine?
- **Row 6 vs row 5:** what Bloom and DGIM add to a learned model.
- **Row 7** uses the engine's own outputs, so it is a reference, not an independent test.

**Caution:** labels and scenarios are authored. Differences within the confidence intervals are not meaningful. This shows mechanics, not real-world performance.

## Presenting script (about three minutes)

1. “Everything is evaluated on the same last 30% of events by time. No shuffling, and the test split was untouched during training.”
2. “Row 1 is the do-nothing reference. Row 2 is one appealing rule; it fires on 10 legitimate test events and catches no abuse, so it looks no better than allow-all.”
3. “Row 3 is the exact rules. Row 4 adds Bloom and DGIM. That lifts PR-AUC from 0.82 to 0.91 and recall at 5% FPR from 37% to 82%.”
4. “Rows 5 to 7 are learned logistic regressions. Raw features do most of the work: PR-AUC around 0.96. Adding Bloom/DGIM does not improve PR-AUC within the interval, but it improves precision at the review cutoff from 88% to 95%.”
5. “Row 7 is not independent: it reads the engine's own rule score, Bloom lookups, and DGIM estimate. It is a reference, not evidence that those signals are fair.”
6. “The intervals overlap, so the honest conclusion is that the learned models rank well on this authored fixture, and Bloom/DGIM help precision more than ranking. This is a mechanics demo, not a production claim.”

## Reproduce

```sh
pip install -r requirements-tools.txt   # optional; scikit-learn path
python tools/train_ladder.py
python server.py
```

Then open **Evidence** and wait for the bootstrap indicator to finish. The bootstrap runs on demand, is cached, and is not recomputed on every live event.

Example wall-clock timings from a local scikit-learn run:

- load fixture: 0.03 s
- replay and record features: 0.72 s
- train LR-raw: 0.45 s total (validation 0.38 s, final fit 0.05 s)
- train LR-raw+BD: 0.24 s total
- train LR-all: 0.23 s total
- export each model: about 0.001 s
- first 500-resample bootstrap report: about 3.1 s
