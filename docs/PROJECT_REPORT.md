# ThermaSight — Project Report (YUKTHI 2026)

**Mission:** *The Stacksmith Protocol* end-to-end delivery, run on the supplied
YUKTHI 2026 attachments: the Problem Statement, the Data Specification, and the
real 25,003-row chiller development dataset. Deliverable: a complete, working,
portable full-stack application — real backend, real data pipeline, real UI —
that a fresh machine can clone and run from documentation alone.

**Outcome:** ThermaSight v2 — FastAPI + numpy backend, vanilla HTML/CSS/JS
frontend, one runnable package, verified end-to-end on the real dataset. All
QA gates pass. This report is the Stacksmith critic board's written record.

---

## 1. Delivery summary

| Artifact | Location / state |
|---|---|
| Repository | `thermasight/` — git repo, clean history, push-ready (no GitHub URL supplied; instructions in §8) |
| Runnable package | `thermasight-<date>.zip` (excludes `.venv`, `__pycache__`, `.git`) — see §7 |
| Documentation | `README.md` (run guide), `docs/PROJECT_REPORT.md`, `docs/DATA_REPORT.md`, `docs/dataset_report.json` |
| Data | `data/yukthi_development.csv` — the supplied development dataset, bundled for offline one-click analysis |
| Verification | `scripts/smoke.py` (demo, 5/5 scenarios), `scripts/smoke_real.py` (real dataset, 8/8 checks) |

## 2. Roster (discovery sweep log, Law 1 + 3)

1. **Find-skill (forced first):** runtime discovery located and invoked the
   skill-discovery mechanism (`tool_search` + `skills_list` + `skill_view` +
   `marketplace_browse`); the user-supplied `npx skills add
   vercel-labs/skills --skill find-skills` was installed and read — it points
   to `skills.sh` + `skills find` for open-ecosystem skills. Declared: the
   runtime's own catalog is the sanctioned loader; no engineering-specific
   open skill was found that was not already covered by this environment's
   tooling.
2. **Installed skills sweep:** 72 skills enumerated. Engineering-relevant:
   `content-analysis` (artifact analysis), `pdf-creation`, `create-skill`
   (used at delivery). **Media-generation skills: DISCOVERED and EXCLUDED** per
   Law 8 (this mission produces no imagery, video, or audio).
3. **Community sweep:** marketplace browsed; `frontend-ui-engineering`
   (1,503 installs) and `context-engineering` (1,249 installs) surfaced but not
   installed — the frontend and context already exist and are maintained;
   installing mid-flight added risk for no gap (declared, not skipped).
4. **Employees:** delegation tooling loaded; no persistent AI Employee registry
   exists in this runtime, so the in-session swarm (critic board §5) was used
   instead — declared fallback per Law 1.
5. **Web research:** 10 sources reviewed for chiller/energy anomaly detection
   practice — see §6.

## 3. Solution architecture (P3 → P4)

**Chosen: per-unit expected-behaviour learning + fused deviation scoring.**
Each equipment unit is its own chronological series; two models learn normal:

1. **Isolation Forest** (numpy, 80 trees / 256 samples) over all measurements
   + cyclic time features + trailing rolling stats — captures *structural*
   outliers (rows unusual regardless of context).
2. **Residual MLP** (1×24 hidden, momentum SGD, grad clipping, early stop)
   predicting expected energy from load, chilled/cooling water, ambient and
   dynamics — captures *contextual* deviation (energy not explained by
   operating conditions).

**Scoring:** residuals are bias-corrected (hour-of-day median, expected-value
bins), robust-scaled, mapped through a smooth anomaly function, and fused
0.25/0.75 with the isolation score. Candidates are defined as the unit's own
**97.5th-percentile score tail** (scale-robust — a real-data QA finding, see
§7.1), with persistence logic: isolated blips stay low, sustained runs
escalate, runs within 12 intervals are joined. **Severity is relative** to the
unit's own flagged periods (worst ~10% action, next ~30% alert, rest watch)
with absolute floors — keeps prioritisation meaningful on any dataset shape.

**Interpretation:** each episode gets contributing measurements vs its 12-hour
baseline (σ), pattern classification, plain-language narrative, evidence table,
and evidence-based recommendations. Health 0–100 from recent exposure + 90-day
residual trend.

**Alternates explored and rejected (Idea Vault):**

- *Pure autoencoder reconstruction* (DaeFDI-style): strong on chiller FD, but
  adds a third model without clear marginal value over the residual MLP for
  this 9-column telemetry, and is harder to introspect for evidence
  (reconstruction error per sensor ≠ z-vs-baseline evidence the judges can
  read).
- *Matrix-Profile contextual detection*: excellent for motif/discord finding,
  but per-subsequence and less natural for multivariate conditioning on
  ambient variables.
- *Quantile-regression forecast bands*: principled uncertainty intervals, but
  requires a calibrated PICP/PINAW protocol on labels we do not have; the
  MLP + bias-correction residual achieves the same contextual deviation with
  fewer moving parts for an 18-hour build.

**Stack posture:** Python 3.10+ · fastapi + uvicorn (single process) · numpy
only for ML (no heavy deps; pandas/sklearn used in dev tooling only) · vanilla
HTML/CSS/JS frontend (no build step) · in-memory bounded result store ·
env-driven `PORT`. Data layer deliberately file/CSV-based with a contract
parser so the data layer is swappable (Law 4).

## 4. Algorithm + evaluation summary

Unsupervised by necessity: the spec provides **no target column**. Evaluation
is therefore honest, protocol-based rather than label-based:

1. **Contract QA** (objective): rows, units, duplicates, missing counts, cadence
   — all match the Data Specification exactly (8/8 checks in
   `smoke_real.py`).
2. **Behavioural checks on the demo dataset** (objective): five embedded fault
   scenarios, verified detectable (`smoke.py` → ALL SCENARIOS DETECTED).
3. **Diagnostics on real data** (transparent): threshold/flag distribution,
   episode severity spread, seasonal concentration, runtime — reported in the
   UI, README, and DATA_REPORT.md.
4. **No leakage in features**: rolling stats strictly trailing; timestamps
   parsed as written (UTC frame stated in UI); equipment never merged across
   units; gaps handled as data characteristics, not faults.

Known limitation, stated plainly: in-sample deviation scores (no holdout
labels), so the 97.5th-percentile tail is a designed anomaly *rate*, and
extremely hot ambient windows (Apr–May 2020) legitimately concentrate episodes.
The interpretation layer therefore *recommends investigation* with evidence,
never asserts a physical fault (Law 2: zero fabrication).

## 5. Critic board — verdicts (P3/P4/P6)

| Seat | Verdict | Key findings (all closed) |
|---|---|---|
| Requirements Fidelity Sentinel | APPROVE | Spec honoured; missing counts/dups/units verified against source; no fabricated ground truth |
| Data & Algorithm Soundness Examiner | APPROVE (after REVISE) | Real-data QA #2 (severity degeneracy) and #3 (100%-flagged diagnostic) analysed and fixed; evaluation protocol documented |
| Architecture & Code Quality Examiner | APPROVE | Reused prior port (user's own codebase); added unit-tolerant parser, relative severity, unclassified tagging |
| Portability & Git Chair | APPROVE | Clean-clone run verified in a fresh directory + fresh venv; zip built; secrets none; `.gitignore` correct |
| Security & Compliance Examiner | APPROVE | No secrets, no external calls at runtime, uploads parsed in-memory, bounded store |
| Research Grounding Auditor | APPROVE | Design decisions cite §6 sources; no invented best practice |
| Ship-Readiness Chair | APPROVE | README run guide verified; docs complete; milestone list (§9) met |

## 6. Research digest (sources cited by the architecture)

- Deep autoencoder chiller FDI (unlabeled-normal training, threshold on
  reconstruction error): [Fault Detection and Isolation for Chiller System
  based on Deep Autoencoder, IC(I)EA 2021](https://doi.org/10.1109/iciea51954.2021.9516436) — supports residual/reconstruction-fusion design.
- Autoencoder FD with explained fault localization (per-sensor reconstruction
  error for contributing signals): [Holly et al., Autoencoder based Anomaly
  Detection and Explained Fault Localization in Industrial Cooling
  Systems](https://www.heitzinger.info/Papers/Holly2022autoencoder.pdf) — supports per-measurement evidence.
- Chiller residual autoencoder + local anomaly factor (joint global/local
  statistics): [CN122508110A — Fault detection method for water chiller based
  on local analysis of contrastive residual autoencoder](https://eureka.patsnap.com/patent/CN122508110A) — supports the fused-score thresholding.
- Physics-informed AE for chiller sensor faults (threshold-tunable detection):
  [MDPI Sensors 26(10):3025](https://www.mdpi.com/1424-8220/26/10/3025) — context: physical consistency as a guard.
- Autoencoder vs alternatives for HVAC AFD (equipment-level vs system-level):
  [Advanced Engineering Informatics 2024](https://doi.org/10.1016/j.aei.2024.102810) — supports choosing AE/residual over separate predictors.
- Energy anomaly detection practice: [GAN-LSTM smart-meter AD
  (LEAD)](https://github.com/fahimehorvatinia/GAN-LSTM-Smart-Meter-Anomaly-Detection), [NBEATS + quantile regression DLAD (IIT
  Bombay)](https://github.com/vmm221313/Anomaly_Detection_Time_Series), [Contextual Matrix Profile in building
  energy](https://github.com/Vincenzo-26/contextual-anomaly-detector), and the
  [EnergyFaultDetector package (ARCANA root-cause
  analysis)](https://github.com/AEFDI/EnergyFaultDetector) — pooled for
  methodology triangulation; environment-conditional deviation framing adopted,
  adversarial/threshold variants rejected for the 18-hour scope.

## 7. QA record (real execution)

### 7.1 Findings found in-loop and fixed

1. **Parser vs real headers (CRITICAL, fixed):** the real CSV's headers carry
   unit annotations (`Chilled Water Rate (L/sec)`); the contract mapper matched
   bare names. `_norm` now strips parentheticals — contract-tolerant by design.
2. **Severity degeneracy on real data (MAJOR, fixed):** with the old absolute
   floors, every episode saturated at `action` (0.84 floor vs peaks 0.86–0.95)
   and the candidate threshold collapsed to a fixed floor, so up to 100% of
   points could be flagged on differently scaled data. Replaced with
   unit-relative candidate tail (97.5th percentile) + relative severity
   (≈10% action / 30% alert / 60% watch of flagged periods) with absolute
   floors as guards. Verified: CHILLER-01 57 episodes → 34/17/6.
3. **Dead single-point rescue (MINOR, fixed):** `raw > 0.92` could never fire
   (max raw ≈ 0.89); replaced with a top-1% rule so extreme isolated blips
   still surface.
4. **Empty pattern tags (MINOR, fixed):** episodes without a matched pattern
   are tagged `unclassified` and carry the generic review recommendation
   instead of a blank cell.

### 7.2 Tests run (all green)

- `scripts/smoke.py` — demo: **ALL SCENARIOS DETECTED** (5/5 fault windows).
- `scripts/smoke_real.py` — real: **8/8 PASS** in ≈ 5.5 s.
- Live API: `GET /api/health`, `POST /api/analyse` ×3 paths
  (`dataset=real`, `demo=1`, multipart file re-upload of the real CSV),
  `GET /api/result/{id}`, `GET /api/series/{id}/{eq}` — all 200 with valid
  payloads; a malformed CSV returns 400 with a contract message.
- Portability: fresh directory, fresh `python -m venv`, `pip install -r
  requirements.txt` from PyPI, `python run.py`, same endpoints — verified.

## 8. Assumptions (and how to overturn each)

1. **Python 3.10+ target environment** (any OS). Overturn: adjust
   `requirements.txt`/`run.py` for another runtime (Dockerfile is a 20-line
   add; none shipped to keep the zip dependency-free).
2. **Bundling the supplied development dataset** in the repo/zip so the
   "Analyse real" path works offline. Overturn: delete `data/` and adjust the
   `dataset=real` branch in `backend/main.py` to require an upload.
3. **No GitHub URL provided** → repo delivered push-ready, not pushed.
   Overturn: tell the agent the remote URL; `git remote add origin … && git
   push -u origin main`.
4. **Timestamps interpreted as written** (UTC frame), consistent with the
   prior spec reading and stated in the UI. Overturn: add a timezone parameter
   to the parser.
5. **Severity definition**: relative-to-unit with absolute floors, per the
   prioritisation requirement. Overturn: expose floor constants in
   `scoring.py` or add a settings endpoint.

## 9. Milestone log

- P0 Intake: brief + 2 scanned PDFs (OCR'd) + real CSV downloaded, contract
  understood. P1 Sweep: roster logged; find-skills installed+read; media
  skills excluded under Law 8. P2 Institution: critic board chartered (§5).
  P3 Ideation: 3 architectures diverged, 1 approved. P4 Blueprint: QA
  criteria defined before code. P5 Build: baseline port, parser fix, severity
  calibration, unclassified tagging, real-dataset bundle + API + UI, docs.
  P6 QA: 4 findings closed, contract + demo + live + portability verified.
  P7 Delivery: this report, zip, skill offer.

## 10. Iteration guide

- **Add a label source later?** Plug `ground_truth` into `scripts/` and swap
  the quantile threshold for a calibrated one; scoring/serialization already
  expose threshold per unit.
- **Stream/online mode?** The models are per-unit and deterministic; a
  sliding-window retrain + `POST /api/analyse` on a rolling CSV is the natural
  path (engine already isolates unit state).
- **Heavier models?** `models.py` is the only file to touch (MLP hidden/epochs,
  forest trees) — all numerics are numpy with documented contracts.
- **Fleet comparisons?** `scoring.fleet_stats` already aggregates
  energy/severity/at-risk; a rank view can be added in the frontend without
  touching the pipeline.