# ThermaSight — the ML model & scripts, detailed

Deep-dive for the Phase 2 rubric (ML Intelligence 40 · Effectiveness 20 ·
Actionability 10 · Engineering 10). All numbers below are the actual outputs
of running the pipeline on the supplied 25,003-row development dataset.

---

## 1 · Problem formulation

Unsupervised, per-unit, contextual expected-behaviour learning.

For each unit *u* we learn an expectation function
`f̂_u(context) ≈ E[energy | load, temperatures, weather, time]`
from that unit's own history, then measure deviation:

- **structural deviation** — how unusual a row of all measurements is (Isolation Forest);
- **contextual deviation** — how far actual energy is from what the operating
  context predicts (residual network).

A point is an anomaly only if it deviates from the learned expectation *for
its conditions* — never from a fixed threshold.

## 2 · Data contract honoured

- 25,003 rows, 11 fields, 3 units, 30-min cadence; 0 duplicate
  `(equipment_id, timestamp)` pairs.
- Missing values per the spec (Chilled Water Rate 40, Cooling Water Temp 5,
  Building Load 19, Energy 9, Humidity 20, Wind 23, Pressure 12); none in
  timestamp/equipment_id/Outside Temp/Dew Point.
- Gaps are a data characteristic, never faults; long gaps (up to 73 days)
  handled by carry-forward, counted, and shown in the quality report.

## 3 · Features (what the models see)

| Group | Features | Used by |
|---|---|---|
| Measurements (robust z, median/MAD) | 9 columns: energy, load, chilled-water rate, cooling-water temp, outside temp, dew point, humidity, wind, pressure | IF (all 9) · MLP (8 context, energy is target) |
| Time cycles | hour-of-day sin/cos, day-of-week sin/cos, day-of-year sin/cos | both |
| Dynamics (strictly trailing) | 24-h rolling mean/std of energy & load (48-point), 1-step & 24-h lags | both |
| Total | **18 IF features, 16 MLP features** (as reported in the model metadata) | — |

Rules: rolling windows are strictly trailing (no leakage); columns that are
entirely absent/all-blank are excluded and reported ("Absent columns"); the
target (energy) being unusable marks the unit `insufficientData` with reason.

## 4 · Model 1 — Isolation Forest (structure)

- 80 trees; each tree trains on a random 256-row subsample of the unit's rows.
- Every split: random feature, random cut between its observed min/max,
  depth-capped at `⌈log2 n⌉ + 2`.
- Score: `2^(−E(path length)/c(n))` → 0–1; easy-to-isolate rows score high.
- Captures multivariate outliers: the row that is unusual *as a combination*,
  even when no single column looks extreme.

## 5 · Model 2 — Residual MLP (context)

- Architecture: 1 hidden layer × 24 units, ReLU, linear output. 16 inputs.
- Target: the unit's own energy in robust-z units (so it learns relative
  behaviour, not absolute kWh).
- Training loop (deterministic, `models.py`):
  - 90/10 train/validation split per unit;
  - batches of 48; **momentum SGD** (lr 0.02, momentum 0.9);
  - **gradient clipping** (scale ≤ 40 in a bounded norm);
  - up to 150 epochs with **early stopping** (patience 22) on the held-out
    validation MSE — the model must predict rows it never trained on;
  - best weights restored; seeded RNG → identical results every run.
- Output: predicted expected energy per timestamp; `actual − expected` =
  residual (in z-units) = "unexpectedness given these conditions".

## 6 · Calibration & detection (self-calibrating, no fixed thresholds)

1. **Bias corrections:** hour-of-day median removed (diurnal rhythm is normal
   by design); 8 expected-value bins removed (systematic bias at extremes).
2. **Robust scaling:** residual rescaled by its own MAD → σ-like z.
3. **Fusion:** score = 0.25·IF + 0.75·residual anomaly kernel.
4. **Threshold:** each unit's own 97.5th percentile of fused scores — the
   flagged tail is per-unit and cannot degenerate.
5. **Persistence (the "isolated ≠ anomaly" rule):** runs need ≥ 2 intervals
   (joins ≤ 12 steps) or an extreme top-1% point; run boost escalates
   sustained deviations.
6. **Relative severity:** worst ~10% of the unit's episodes → action, next
   ~30% → alert, rest → watch (absolute floors 0.80/0.70 as guards).

## 7 · Interpretation & explainability

- Contributors: each measurement vs its own trailing 12-h median, in σ,
  ranked with direction and weight (the drawer's Evidence table).
- Pattern tags: high_consumption_low_load, cooling_water_drift, flow_imbalance,
  sensor_stuck, transient_spike, degradation_trend, offhours_standby,
  concurrent_context_shift, unclassified — each mapped to evidence-based
  recommendations (no claim of a specific physical fault without data
  support).
- Narrative: deterministic template filled with the computed numbers (no LLM).
- Health 0–100: last-30-day alert exposure + 90-day residual trend
  (Spearman); degradation trend = rank correlation of residual z over ~90 d.

## 8 · Model ledger (actual run on the development dataset)

| unit | rows | op. days | threshold (97.5%) | health | episodes (w/a/act) | energy mean | trend | features (IF/MLP) |
|---|---|---|---|---|---|---|---|---|
| CHILLER-01 | 8,333 | ~288 | 0.842 | 94 | 57 (34/17/6) | 127.8 kWh | +0.027 | 18/16 |
| CHILLER-02 | 8,340 | ~247 | 0.856 | 95 | 62 (37/18/7) | 131.8 kWh | −0.012 | 18/16 |
| CHILLER-03 | 8,330 | ~288 | 0.831 | 94 | 54 (32/16/6) | 132.5 kWh | −0.060 | 18/16 |

Fleet: 25,003 rows analysed in ≈ 5.5 s; 173 episodes (6 action / 51 alert /
116 watch). Cluster check: April–May 2020 concentrates episodes — the seasonal
context shift, not a dashboard artifact.

## 9 · Why this is not a threshold dashboard (proof, from the real data)

- Diurnal: 02:00 mean 107.8 kWh vs 15:00 mean 159.7 kWh (spread +48%). A flat
  alarm at 160 kWh would scream every hot afternoon and never see a night
  event.
- Learned-normal verdicts (same class of reading, opposite verdicts):
  - 03:00 point at **146.9 kWh** → residual-z **+11.1 σ** → flagged
    (night draw explained by nothing → unusual);
  - 15:00 point at **199.5 kWh** → residual-z **−0.7 σ** → normal
    (afternoon high is expected).
- Raw-correlation caveats are handled by conditional learning: humidity
  correlates −0.53 with energy *in the raw data* (night/monsoon confound) but
  the model conditions on time and the full context, so it never turns a raw
  correlation into a rule.

## 10 · The scripts (reproducibility evidence)

| Script | Purpose | Result |
|---|---|---|
| `run.py` | single-command server (FastAPI + static UI) | — |
| `scripts/smoke.py` | demo dataset with 5 embedded faults | **5/5 detected** |
| `scripts/smoke_real.py` | contract + coherence on the real data | **8/8 PASS**, ≈5.5 s |
| `scripts/edge_cases.py` | 17 adversarial inputs (missing columns, garbage, tiny units…) | **17/17 handled** |
| `scripts/explore_dataset.py` | contract verification + dataset report | report JSON |
| `scripts/time_of_day_demo.py` | proof the diurnal relation is learned | table above |
| `backend/pipeline/*.py` | parser → features → models → scoring → interpretation → recommendations → engine | the pipeline itself |

## 11 · Rubric coverage

- **ML Intelligence (40):** unsupervised per-unit learning; two-model fusion;
  self-calibrating threshold; persistence semantics; conditional (not
  threshold) detection — the ML drives every screen number.
- **Effectiveness (20):** real-data verification (episode counts, severity
  spread, seasonal clustering), 5/5, 8/8, 17/17.
- **Actionability & Explainability (10):** σ evidence, tags, narratives,
  recommendations, health/trend — deterministic, no black box.
- **Engineering (10):** portable single package (Python server or browser),
  contract-driven, deterministic seeds, three test suites, docs.

## 12 · Honest limits

No labels exist → we measure deviation from learned normal, not classify
faults; slow degradation can be partially absorbed by the learned baseline
(trend component mitigates); the 97.5% tail is a designed anomaly rate, not a
calibrated precision — stated rather than hidden.