# ThermaSight — Intelligent Energy & Equipment Monitoring

[![CI](https://github.com/AbhijithAA5/thermasight/actions/workflows/ci.yml/badge.svg)](https://github.com/AbhijithAA5/thermasight/actions/workflows/ci.yml)

Submission for **YUKTHI 2026** (National-Level Hackathon, *Intelligent Energy &
Equipment Monitoring* track). Built and verified against the supplied
development dataset and the official Data Specification.

A full-stack, machine-learning-driven application that learns normal chiller
behaviour from historical telemetry, detects meaningful contextual
abnormalities, and turns them into evidence-backed, actionable insights —
**DETECT. UNDERSTAND. ASSESS. ACT.**

**Stack: Python (FastAPI + numpy) backend · vanilla HTML/CSS/JS frontend.**
One process, no build step, no external services, no accounts, no API keys.
The uploaded CSV never leaves the machine. Runs offline after `pip install`.

---

## Run it (any OS, needs Python 3.10+)

```bash
# 1. create an environment
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate

# 2. install dependencies
pip install -r requirements.txt

# 3. run
python run.py                        # -> http://localhost:8000
PORT=9000 python run.py              # custom port
```

Open http://localhost:8000 and pick a dataset:

| Button | What it runs |
|---|---|
| **Analyse real YUKTHI dataset** | The bundled 25,003-row development dataset (3 chillers, Aug 2019 → Jun 2020, 30-min cadence) |
| **Or try the demo** | A deterministic synthetic dataset (~28,600 rows, same contract) embedding five fault scenarios |
| **Upload CSV** (Data tab) | Any CSV conforming to the YUKTHI 2026 data specification |

## What it does

**Pipeline (end-to-end, `backend/pipeline/`):**

| Stage | Files | What happens |
|---|---|---|
| Ingest | `parser.py` | Contract-driven CSV parser: columns located by name (unit annotations like `(L/sec)` tolerated), records keyed by `(equipment_id, timestamp)`, each unit its own chronological series, duplicates counted. |
| Quality | `parser.py` | Missing values per column, irregular gaps (never treated as faults per spec), period/cadence — surfaced in the Data view. |
| Features | `features.py` | Missing values interpolated (short gaps) / carried forward (long gaps); cyclic time features; strictly-trailing 24h rolling stats + lags; robust z-scoring. |
| Learn | `models.py` | Per unit: an **Isolation Forest** (multi-variable structural outliers) and a **residual MLP** (predicts expected energy from load, water temperatures, ambient, dynamics). Momentum SGD, gradient clipping, early stopping, deterministic seeds. |
| Detect | `scoring.py`, `engine.py` | Residuals bias-corrected (hour-of-day median, expected-value bins) and robust-scaled; isolation + residual fused (0.25 / 0.75); **candidates are the unit's own 97.5th-percentile score tail**; persistence logic (isolated blips stay low, sustained runs escalate; runs joined within 12 intervals). |
| Prioritise | `scoring.py` | Severity is **relative to the unit's own flagged periods**, with absolute floors as guards: worst ~10% of episodes → `action`, next ~30% → `alert`, rest of the tail → `watch`. This keeps prioritisation meaningful across differently scaled datasets. |
| Interpret | `interpretation.py`, `recommendations.py` | Each episode: contributing measurements vs the unit's own 12h baseline (σ), pattern classification (energy-at-steady-load, condenser drift, flow imbalance, frozen sensor, transient, degradation trend, off-hours draw, mode shift, unclassified), plain-language narrative, evidence table, evidence-based recommendations. |
| Assess | `scoring.py` | Health 0–100 (recent exposure + 90-day residual trend), severity Watch/Alert/Action, fleet priority queue. |

## Results on the supplied development dataset

Verified against the Data Specification (see `scripts/smoke_real.py` — all
checks pass, runtime ≈ 5.5 s):

- 25,003 rows · 3 units (`CHILLER-01/02/03`) · **0** duplicate `(equipment_id, timestamp)` pairs.
- Missing-value totals match the spec exactly: Chilled Water Rate 40, Cooling
  Water Temperature 5, Building Load 19, Energy 9, Humidity 20, Wind Speed 23,
  Pressure 12; none in timestamp / equipment_id / Outside Temperature / Dew Point.
- Nominal 30-min cadence with 51 gaps longer than 60 min (max ≈ 73.6 days) —
  handled as a data characteristic, never treated as a fault.
- **173 episodes** flagged across the fleet (~5% of points):
  CHILLER-01 57 (34 watch / 17 alert / 6 action), CHILLER-02 62
  (37 / 18 / 7), CHILLER-03 54 (32 / 16 / 6), health 94–95/100.
- Episodes concentrate in **Apr–May 2020** — the summer peak when load and
  ambient conditions move beyond the learned envelope; the interpretation layer
  names the contributing measurements and recommends investigation rather than
  asserting a specific physical fault.

Full numbers: `docs/dataset_report.json` and `docs/DATA_REPORT.md`.

## API

| Endpoint | Purpose |
|---|---|
| `POST /api/analyse` | Multipart `file` (a conforming CSV), `dataset=real` (bundled development data) or `demo=1` / `dataset=demo`; returns `{id, source, quality, episodeCount, model, ...}` |
| `GET /api/result/{id}` | Full analysis: per-equipment summaries (health, threshold, trend, episodes with evidence + recommendations) and the fleet episode list |
| `GET /api/series/{id}/{equipment}?variable=...` | Per-point arrays for the chart (times, values, scores, severity codes) |
| `GET /api/demo.csv` | Download the synthetic demo dataset |
| `GET /api/health` | Liveness |

In-memory result store, bounded to the 12 most recent analyses.

## Verification

```bash
python scripts/smoke.py        # demo: all five embedded fault scenarios detected
python scripts/smoke_real.py   # real dataset: contract + output coherence, all checks pass
python scripts/explore_dataset.py data/yukthi_development.csv --out /tmp/report.json
```

## Honest scope

- No fault labels exist in the development set, so detection is unsupervised
  deviation-from-learned-normal. The interpretation layer names evidence and
  pattern class and recommends investigation; it does **not** assert a specific
  physical fault without data support.
- Slow degradation can be partially absorbed by the learned baseline (a known
  property of unsupervised reconstruction); the trend component and rolling
  health mitigate this.
- With no holdout labels, the anomaly threshold is defined as the unit's own
  97.5th-percentile score tail rather than a calibrated false-positive rate.
- Timestamps are interpreted as written (UTC frame), stated in the chart header.
- All ML is numpy, deterministic per seed; the numeric details are documented
  in `backend/pipeline/*.py` and `docs/PROJECT_REPORT.md`.

## Project layout

```
run.py                     # entry point (uvicorn)
requirements.txt           # fastapi, uvicorn, numpy, python-multipart
backend/main.py            # FastAPI app: REST API + static frontend
backend/pipeline/          # parser, features, models, scoring, interpretation, recommendations, engine
data/yukthi_development.csv# bundled YUKTHI 2026 development dataset
frontend/                  # vanilla HTML/CSS/JS single-page app
scripts/                   # smoke.py, smoke_real.py, explore_dataset.py
docs/                      # PROJECT_REPORT.md, DATA_REPORT.md, dataset_report.json
```