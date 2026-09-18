# ThermaSight — Intelligent Energy & Equipment Monitoring

Submission for **YUKTHI 2026** (National-Level Hackathon, *Intelligent Energy &
Equipment Monitoring* track).

A full-stack, machine-learning-driven application that learns normal chiller
behaviour from historical telemetry, detects meaningful contextual
abnormalities, and turns them into evidence-backed, actionable insights.

**Stack: Python (FastAPI + numpy) backend · vanilla HTML/CSS/JS frontend.**
One process, no build step, no external services, no accounts, no API keys.
The uploaded CSV never leaves the machine.

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

Open http://localhost:8000, click **Analyse demo dataset**, or drop the
YUKTHI participant CSV into the **Data** tab. Internet is only needed for the
one-time `pip install`; afterwards it runs fully offline.

## What it does

**Pipeline (end-to-end, `backend/pipeline/`):**

| Stage | Files | What happens |
|---|---|---|
| Ingest | `parser.py` | Contract-driven CSV parser: columns located by name, records keyed by `(equipment_id, timestamp)`, each unit its own chronological series, duplicates counted. |
| Quality | `parser.py` | Missing values per column, irregular gaps (never treated as faults per spec), period/cadence — surfaced in the Data view. |
| Features | `features.py` | Missing values interpolated (short gaps) / carried forward (long gaps); cyclic time features; strictly-trailing 24h rolling stats + lags; robust z-scoring. |
| Learn | `models.py` | Per unit: an **Isolation Forest** (multi-variable structural outliers) and a small **residual MLP** (predicts expected energy from load, water temperatures, ambient, dynamics). Momentum SGD, gradient clipping, early stopping, deterministic seeds. |
| Detect | `scoring.py`, `engine.py` | Residuals bias-corrected (hour-of-day median, expected-value bins) and robust-scaled; isolation + residual fused (0.25 / 0.75), per-unit 97.5th-percentile threshold, persistence logic (isolated blips stay low, sustained runs escalate; runs joined within 12 intervals). |
| Interpret | `interpretation.py`, `recommendations.py` | Each episode: contributing measurements vs the unit's own 12h baseline (σ), pattern classification (energy-at-steady-load, condenser drift, flow imbalance, frozen sensor, transient, degradation trend, off-hours draw, mode shift), plain-language narrative, evidence table, evidence-based recommendations. |
| Assess | `scoring.py` | Health 0–100 (recent exposure + 90-day residual trend), severity Watch/Alert/Action, fleet priority queue. |

**Demo dataset** (`demo.py`) — a deterministic, physics-informed synthetic
dataset (~28,600 rows, same columns/units/cadence/missing-value rates as the
spec) embedding five fault scenarios, all verified detectable by `scripts/smoke.py`:
CHILLER-01 transient spike + condenser fault, CHILLER-02 progressive
degradation, CHILLER-03 flow imbalance + frozen sensor.

## API

| Endpoint | Purpose |
|---|---|
| `POST /api/analyse` | Multipart `file` (a conforming CSV) or `demo=1`; runs the pipeline, returns `{id, quality, episodeCount, model, ...}` |
| `GET /api/result/{id}` | Full analysis: per-equipment summaries (health, threshold, trend, episodes with evidence + recommendations) and the fleet episode list |
| `GET /api/series/{id}/{equipment}?variable=...` | Per-point arrays for the chart (times, values, scores, severity codes) |
| `GET /api/demo.csv` | Download the synthetic demo dataset |
| `GET /api/health` | Liveness |

In-memory result store, bounded to the 12 most recent analyses.

## Verification

```bash
python scripts/smoke.py
```

Runs the full pipeline on the demo dataset and checks that all five embedded
fault scenarios are detected (prints HIT/MISS per window). The same check was
used to validate the port against the reference TypeScript implementation.

## Honest scope

- No fault labels exist in the development set, so detection is unsupervised
  deviation-from-learned-normal. The interpretation layer names evidence and
  pattern class and recommends investigation; it does not assert a specific
  physical fault without data support.
- Slow degradation can be partially absorbed by the learned baseline (a known
  property of unsupervised reconstruction); the trend component and rolling
  health mitigate this.
- Timestamps are interpreted as written (UTC frame), stated in the chart
  header.
- All ML is numpy, deterministic per seed; the numeric details are documented
  in `backend/pipeline/*.py`.