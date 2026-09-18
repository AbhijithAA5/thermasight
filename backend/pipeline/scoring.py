"""ThermaSight — fusion scoring, episode extraction, health assessment."""

from __future__ import annotations

import math

import numpy as np

from .features import WARMUP, median
from .models import quantile_sorted, spearman
from .types import SEVERITY_RANK, clamp, Series

IF_WEIGHT = 0.25
RESIDUAL_WEIGHT = 0.75
THRESHOLD_QUANTILE = 0.975
JOIN_WINDOW = 12
MIN_THRESHOLD = 0.35  # candidates are defined relative to the unit's own score tail
ALERT_FLOOR = 0.70
ACTION_FLOOR = 0.80


def _q_lin(sorted_arr, q: float) -> float:
    """Linear-interpolated quantile over a sorted array (numpy-style)."""
    n = len(sorted_arr)
    if n == 0:
        return 0.0
    if n == 1:
        return float(sorted_arr[0])
    pos = q * (n - 1)
    lo_i = int(math.floor(pos))
    hi_i = int(math.ceil(pos))
    if lo_i == hi_i:
        return float(sorted_arr[lo_i])
    frac = pos - lo_i
    return float(sorted_arr[lo_i] + (sorted_arr[hi_i] - sorted_arr[lo_i]) * frac)


def score_series(if_score: np.ndarray, residual_z: np.ndarray) -> dict:
    n = len(if_score)
    res_anom = 1.0 - np.exp(-0.5 * np.power(residual_z / 1.8, 2.0))
    raw = IF_WEIGHT * np.minimum(if_score, 0.55) + RESIDUAL_WEIGHT * res_anom
    raw = np.clip(raw, 0.0, 1.0)
    active = raw[WARMUP:][raw[WARMUP:] > 1e-9]
    # Candidates are the unit's own 97.5th-percentile tail (no absolute floor,
    # so the flagged set cannot degenerate to "everything" on differently
    # scaled data).
    threshold = max(MIN_THRESHOLD, quantile_sorted(active, THRESHOLD_QUANTILE)) if active.size else 0.6
    cand = np.zeros(n, dtype=bool)
    cand[WARMUP:] = raw[WARMUP:] > threshold

    # Runs, allowing joins of up to JOIN_WINDOW steps.
    runs: list[tuple[int, int]] = []
    run_start = -1
    last_true = -1
    for i in range(n + 1):
        is_true = i < n and bool(cand[i])
        if is_true:
            if run_start < 0:
                run_start = i
            last_true = i
        elif run_start >= 0 and (i - last_true > JOIN_WINDOW or i == n):
            length = last_true - run_start + 1
            single_rescue = quantile_sorted(active, 0.99) if active.size else 0.9
            if length >= 2 or raw[run_start] > single_rescue:
                runs.append((run_start, last_true))
            run_start = -1

    in_run = np.zeros(n, dtype=bool)
    run_len = np.zeros(n, dtype=int)
    for (s, e) in runs:
        in_run[s : e + 1] = True
        run_len[s : e + 1] = e - s + 1

    score = raw.copy()
    # Keep the run boost modest so peak scores do not saturate at the top.
    for i in range(n):
        if in_run[i]:
            boost = 0.03 * min(1.0, run_len[i] / 10.0)
            score[i] = clamp(score[i] + boost, 0.0, 1.0)

    # Severity is relative to the unit's own set of flagged periods, with
    # absolute floors as guards: the worst ~10% of episodes are "action",
    # the next ~30% "alert", the rest of the tail "watch".
    if runs:
        peaks = sorted(float(max(score[s : e + 1])) for (s, e) in runs)
        alert_floor = max(ALERT_FLOOR, _q_lin(peaks, 0.60))
        action_floor = max(ACTION_FLOOR, _q_lin(peaks, 0.90))
    else:
        alert_floor, action_floor = ALERT_FLOOR, ACTION_FLOOR

    run_sev: dict[int, str] = {}
    for (s, e) in runs:
        pk = s
        for i in range(s + 1, e + 1):
            if score[i] > score[pk]:
                pk = i
        pv = float(score[pk])
        run_sev.update(
            {i: ("action" if pv >= action_floor else "alert" if pv >= alert_floor else "watch")
             for i in range(s, e + 1)}
        )

    flagged = np.zeros(n, dtype=bool)
    severity = np.array(["normal"] * n, dtype=object)
    for i in range(n):
        if i in run_sev:
            flagged[i] = True
            severity[i] = run_sev[i]
    score[:WARMUP] = 0.0
    flagged[:WARMUP] = False
    severity[:WARMUP] = "normal"

    return {"score": score, "flagged": flagged, "severity": severity, "threshold": threshold, "runs": runs}


def degradation_trend(residual_z: np.ndarray, n: int) -> float:
    win = min(n, 90 * 48)
    if win < 240:
        return 0.0
    idx = list(range(n - win, n, 4))
    xs = [i for i in idx]
    ys = [float(residual_z[i]) for i in idx]
    return spearman(xs, ys)


def compute_health(times: list[int], score: np.ndarray, trend: float) -> int:
    n = len(times)
    if n < WARMUP:
        return 100
    cutoff = times[-1] - 30 * 86_400_000
    cnt = 0
    bad = 0
    for i in range(n - 1, -1, -1):
        if times[i] < cutoff:
            break
        cnt += 1
        if score[i] >= ALERT_FLOOR:
            bad += 1
    exposure = bad / cnt if cnt else 0.0
    penalty = exposure * 55 + max(0.0, trend) * 30
    return clamp(round(100 - penalty), 5, 100)


def episode_shell(equipment_id: str, run: tuple[int, int], series: Series, score: np.ndarray,
                  severity: np.ndarray) -> dict:
    s, e = run
    peak = s
    for i in range(s + 1, e + 1):
        if score[i] > score[peak]:
            peak = i
    sev = severity[peak]
    if sev not in ("watch", "alert", "action"):
        sev = "watch"
    start_time = series.times[s]
    end_time = series.times[e]
    return {
        "id": f"{equipment_id}-{start_time}",
        "equipmentId": equipment_id,
        "startIndex": s,
        "endIndex": e,
        "startTime": start_time,
        "endTime": end_time,
        "durationHours": round((end_time - start_time) / 3_600_000.0 * 10) / 10,
        "peakIndex": peak,
        "peakScore": round(float(score[peak]) * 1000) / 1000,
        "severity": sev,
    }


def equipment_stats(s: Series) -> dict:
    en = s.data["Chiller Energy Consumption"]
    finite = [v for v in en if math.isfinite(v)]
    total = sum(finite)
    return {
        "energyTotalKwh": round(total),
        "energyMeanKwh": round(total / len(finite) * 10) / 10 if finite else 0,
    }


def fleet_stats(equipment: dict, series: dict) -> dict:
    energy = 0
    for eq, s in series.items():
        energy += equipment_stats(s)["energyTotalKwh"]
    by_severity = {"watch": 0, "alert": 0, "action": 0}
    at_risk: list[str] = []
    for eq, rep in equipment.items():
        for ep in rep["episodes"]:
            by_severity[ep["severity"]] += 1
        if rep["health"] < 60:
            at_risk.append(eq)
    return {"energyTotalKwh": energy, "bySeverity": by_severity, "atRisk": at_risk}