# ThermaSight — What the ML does, step by step (Phase 2 explainer)

This document explains exactly what happens in the software when an analysis
runs — the data journey, the machine learning, and how every screen number
relates to the model. Use it to defend the solution against the Phase 2 rubric:

- ML Intelligence & Technical Performance — 40
- Solution Effectiveness & Application — 20
- Actionability & Explainability — 10
- Engineering Quality & Robustness — 10

---

## One-paragraph answer

> ThermaSight treats every chiller unit as its own person. For each unit it
> builds two models of "what normal looks like for THIS unit": an **Isolation
> Forest** that learns which combinations of measurements are structurally
> unusual, and a **residual neural network** that learns to predict the energy
> the unit SHOULD be consuming given its load, water temperatures and ambient
> conditions. Anything that disagrees with both — an unusual structure AND
> energy above or below what the context predicts — becomes a deviation that
> is scored, compared against the unit's own history, turned into an episode
> with severity, and explained with the evidence (which measurements, how far
> from baseline, in σ). No labels, no fixed thresholds: "normal" is learned
> from the data itself, per unit.

---

## Step 1 — Ingest: the contract parser
The CSV is read once, columns are matched **by name** (tolerant of units in
headers like `(kWh)`), and records are grouped by `(equipment_id, timestamp)`.
Each unit becomes its own chronological series. The parser counts duplicates,
skipped rows, gaps and missing values — the Data tab's quality report is this
step's honest output. **Why it matters (Engineering 10):** any conforming
dataset runs without code changes; nothing is hard-coded about rows or
timestamps.

## Step 2 — Cleaning & features
- Missing values are **linearly interpolated across short gaps** (≤ 4× the
  30-min interval) and **carried forward across long gaps**; gaps are treated
  as a data characteristic, never as faults (per the spec).
- Time is encoded so the models can learn seasonality: hour-of-day and
  day-of-week sine/cosine cycles, day-of-year cycles.
- **Strictly trailing** features only: 24-hour rolling mean/std of energy and
  load, and 1-step/24-hour lags — no future information ever leaks.
- All columns are **robustly standardised** (median / MAD, not mean/std, so
  real outliers cannot distort the scale).

## Step 3 — Two models per unit (the ML)

### 3a. Isolation Forest (structural)
A forest of 80 random trees, each trained on a random 256-sample subset of the
unit's own feature vectors. Trees isolate points by random feature splits;
**the fewer splits needed to isolate a point, the more unusual it is** (an
anomaly is "easy to isolate"). The score maps path length to a 0–1 anomaly
probability. It learns the *shape* of the data — unusual combinations,
regardless of context.

### 3b. Residual MLP (contextual)
A small 1-hidden-layer network (24 units, momentum SGD with gradient
clipping + early stopping, deterministic seed) trained on **the same unit's
own history** to predict robust-z energy from: load, chilled-water rate,
cooling-water temperature, ambient temperature/dew point/humidity/wind/
pressure, time cycles, and dynamics (lags, rolling states). After training,
the model predicts the **expected** energy for every timestamp. The difference
`actual − expected` is the **residual** — "is this energy level normal FOR
THESE CONDITIONS?" This is what makes a hot-summer peak NOT an anomaly while
energy rising at constant load IS.

## Step 4 — Score calibration (making it honest)
Raw residuals are **bias-corrected twice** before scoring:
1. hour-of-day median removed (a consistent diurnal pattern is normal), and
2. expected-value bins removed (systematic model bias at extremes).
Then scaled by robust MAD so residuals become σ-like units. The isolation
score and the residual score are **fused 0.25 / 0.75** — structure 25%,
context 75% — because the context model carries the semantic meaning.

## Step 5 — Detection: the unit's own tail
Each unit's fused scores define its own **97.5th-percentile threshold** (no
absolute floor — the flagged set cannot degenerate). Then **persistence
logic** (the "isolated deviation is not an anomaly" rule from the problem
statement):
- a single flagged point that is not extreme stays below the radar;
- only a **sustained run** (≥ 2 intervals, joins within 6 hours) or an extreme
  top-1% point becomes an **episode**;
- longer runs are boosted so stubborn deviations escalate; severity is
  **relative to the unit's own episodes** (worst ~10% → action, next ~30% →
  alert, rest → watch) with absolute floors as guards.

## Step 6 — Interpretation (the Explainability marks)
For every episode, the peak timestamp is explained:
- **Contributing measurements**: each variable vs its own trailing 12-hour
  baseline, in σ, ranked, with direction and weight.
- **Pattern tags**: rule-classified signatures (energy up at steady load,
  condenser drift, flow imbalance, frozen sensor, transient, degradation,
  off-hours draw, mode shift, unclassified) — each mapped to evidence-based
  **recommendations** with priorities (never a claim of a specific physical
  fault the data can't support).
- **Narrative**: a sentence-graph of the evidence, in plain language.
- **Health 0–100**: recent 30-day exposure to alerts + a 90-day residual
  trend (rank correlation) — "is this unit getting better or worse?"

## Step 7 — What you see is exactly the model
Fleet KPI band = model summaries. Unit cards = health (this unit's exposure +
trend), mean draw, anomaly count, trend, sparkline of the actual series with
severity flags. Explorer chart = the real series with **anomaly bands from the
model's episodes**, crosshair with "Δ vs 24 h baseline" (the same baseline the
evidence uses). Priority queue = episodes sorted by severity and peak score.
Data tab = the ingestion quality contract. Nothing on screen is a fixed
threshold — every number traces back to a learned model.

---

## Mapping to the rubric

| Criterion (marks) | Where it is satisfied |
|---|---|
| **ML Intelligence (40)** | Unsupervised expected-behaviour learning per unit (Isolation Forest + residual MLP); contextual not absolute detection; fused scoring; honest thresholding (unit's own 97.5% tail); persistence logic. Non-trivial ML that materially drives every result. |
| **Effectiveness (20)** | Verified on the real 25,003-row dataset: 173 episodes, sensible severity spread, Apr–May 2020 concentration matches the seasonal context story; contract verified 8/8; demo 5/5 fault scenarios; edge cases 17/17 (missing data/malformed inputs degrade gracefully). |
| **Actionability & Explainability (10)** | Every episode: evidence table (σ vs baseline), pattern tag, plain-language narrative, evidence-based recommendations; health + trend; crosshair baseline deltas; no unexplained result anywhere. |
| **Engineering (10)** | One runnable package; contract-driven reusable pipeline (no hard-coded rows/columns); deterministic seeds; tests (smoke, real-data, edge battery); docs + this explainer; Git history; working app on GitHub Pages and as a local server. |

## The honest limits (say them before the judges ask)
- No labels exist → detection is **deviation from learned normal**, not
  classification; we never claim a physical fault without data support.
- Slow degradation can be partially absorbed by the learned baseline; the
  trend component and health mitigate this.
- The 97.5% tail is a designed anomaly *rate*, not a calibrated precision —
  honest, defensible, stated in the docs.