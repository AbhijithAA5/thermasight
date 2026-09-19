"""ThermaSight — pipeline orchestrator. Data -> quality -> imputation ->
features -> models (Isolation Forest + contextual residual MLP) -> fusion
scoring -> episodes -> interpretation. Pure numpy, deterministic seeds."""

from __future__ import annotations

import math
import time

import numpy as np

from .features import WARMUP, build_features, impute_series, median
from .interpretation import analyze_contributors, build_narrative, detect_patterns, time_label
from .models import IsolationForest, MLPRegressor, quantile_sorted
from .parser import DatasetError, build_dataset
from .recommendations import recommendations_for_patterns
from .scoring import (compute_health, degradation_trend, episode_shell, equipment_stats,
                      score_series)
from .types import SEVERITY_RANK, NUMERIC_COLS, TARGET_COL, Series

SEVERITY_CODE = {"normal": 0, "watch": 1, "alert": 2, "action": 3}
CODE_SEVERITY = {0: "normal", 1: "watch", 2: "alert", 3: "action"}

MIN_DATA = WARMUP + 24  # trailing baseline window (96) + a minimum training set


def _insufficient_report(s: Series, reason: str) -> dict:
    n = len(s.times)
    stats = equipment_stats(s)
    return {
        "equipmentId": s.equipment_id,
        "insufficientData": True,
        "insufficientReason": reason,
        "score": [0.0] * n,
        "severityCodes": [0] * n,
        "threshold": 0.0,
        "health": None,
        "degradationTrend": 0.0,
        "episodes": [],
        "energyTotalKwh": stats["energyTotalKwh"],
        "energyMeanKwh": stats["energyMeanKwh"],
        "count": n,
        "times": list(s.times),
        "values": {c: [None] * n for c in NUMERIC_COLS},
        "maintenance": None,
        "daily": {"days": [], "energy": [], "seasonal": [], "residZ": []},
        "modelFeatures": {"if": 0, "mlp": 0},
    }


def median_interval(times: list[int]) -> int:
    if len(times) < 2:
        return 1_800_000
    ints = sorted(times[i] - times[i - 1] for i in range(1, len(times)))
    return ints[len(ints) // 2]


def _run_equipment(s: Series, nominal_minutes: int) -> dict:
    n = len(s.times)
    if n < MIN_DATA:
        return _insufficient_report(
            s, f"only {n} observations (minimum {MIN_DATA} needed for the trailing "
            "baseline and model training)"
        )
    imputed = impute_series(s, nominal_minutes)
    feats = build_features(imputed)
    if TARGET_COL not in feats["usableCols"]:
        return _insufficient_report(s, "no usable 'Chiller Energy Consumption' values in the file")
    train_from = min(WARMUP, n)

    forest = IsolationForest()
    forest.fit(feats["ifX"][train_from:], 80, 256, 7 + len(s.equipment_id))
    if_score = forest.score(feats["ifX"])

    mlp = MLPRegressor(hidden=24)
    mlp.fit(feats["mlpX"][train_from:], feats["y"][train_from:],
            epochs=150, seed=11 + len(s.equipment_id))
    pred = mlp.predict(feats["mlpX"])

    # Residual with bias corrections (hour-of-day median, expected-value bins).
    resid = feats["y"] - pred
    hour_samples: list[list[float]] = [[] for _ in range(24)]
    for i in range(train_from, n):
        hour_samples[min(23, int(s.hours[i]))].append(float(resid[i]))
    hour_med = [median(vals) for vals in hour_samples]
    resid_hour_adj = np.array([
        resid[i] - hour_med[min(23, int(s.hours[i]))] if i >= train_from else resid[i]
        for i in range(n)
    ])
    bins = 8
    pred_lo = float(pred[train_from:].min())
    pred_hi = float(pred[train_from:].max())
    bin_of = lambda v: min(bins - 1, max(0, int(((v - pred_lo) / (pred_hi - pred_lo or 1.0)) * bins)))
    bin_samples: list[list[float]] = [[] for _ in range(bins)]
    for i in range(train_from, n):
        bin_samples[bin_of(float(pred[i]))].append(float(resid_hour_adj[i]))
    bin_med = [median(vals) for vals in bin_samples]
    resid_adj = np.array([
        resid_hour_adj[i] - bin_med[bin_of(float(pred[i]))] if i >= train_from else resid_hour_adj[i]
        for i in range(n)
    ])
    abs_devs = np.sort(np.abs(resid_adj[train_from:]))
    resid_scale = 1.4826 * (float(abs_devs[len(abs_devs) // 2]) if abs_devs.size else 1.0)
    residual_z = resid_adj / (resid_scale or 1.0)

    scored = score_series(if_score, residual_z)
    trend = degradation_trend(residual_z, n)
    health = compute_health(s.times, scored["score"], trend)
    stats = equipment_stats(s)

    # ---- seasonal degradation + predictive maintenance summary ----
    # Per calendar day: mean actual energy, mean seasonally-adjusted residual
    # z, and a 45-day-centred seasonal baseline of the unit's own energy.
    day_index: dict[int, list[int]] = {}
    for i in range(len(s.times)):
        day_index.setdefault(int(s.times[i] // 86_400_000), []).append(i)
    day_ms = sorted(day_index)
    d_energy: list[float] = []
    d_resid: list[float] = []
    raw_en = imputed.data[TARGET_COL]
    for d in day_ms:
        idx = day_index[d]
        d_energy.append(float(np.mean([raw_en[i] for i in idx])))
        d_resid.append(float(np.mean([residual_z[i] for i in idx])))
    n_d = len(day_ms)
    d_season: list[float] = []
    for k in range(n_d):
        lo = max(0, k - 22)
        hi = min(n_d, k + 23)
        d_season.append(float(np.median(d_energy[lo:hi])))
    # Last-90-day slope of the daily residual z (seasonally adjusted).
    recent = max(0, n_d - 90)
    xs = list(range(recent, n_d))
    ys = d_resid[recent:]
    slope = 0.0
    if len(xs) >= 14:
        mx = float(np.mean(xs))
        my = float(np.mean(ys))
        denom = sum((x - mx) ** 2 for x in xs) or 1.0
        slope = float(sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom)
    drift = float(np.mean(ys[-30:])) if ys else 0.0  # last-30-day residual level
    if slope > 0:
        horizon_days = max(7, min(365, int(1.5 / slope)))
    else:
        horizon_days = 365
    if health < 70 or drift > 1.2 or slope > 0.015:
        maint_status = "due"
    elif drift > 0.7 or slope > 0.008:
        maint_status = "recommended"
    elif slope > 0.003 or drift > 0.3:
        maint_status = "plan"
    else:
        maint_status = "ok"
    maintenance = {
        "status": maint_status,
        "horizonDays": horizon_days,
        "slopePerDay": round(slope * 1000) / 1000,
        "recentDrift": round(drift * 100) / 100,
    }
    daily = {
        "days": day_ms,
        "energy": [round(x, 1) for x in d_energy],
        "seasonal": [round(x, 1) for x in d_season],
        "residZ": [round(x, 3) for x in d_resid],
    }

    # Episodes with interpretation.
    episodes: list[dict] = []
    for run in scored["runs"]:
        shell = episode_shell(s.equipment_id, run, s, scored["score"], scored["severity"])
        peak = shell["peakIndex"]
        run_len = run[1] - run[0] + 1
        contributors = analyze_contributors(imputed, peak, feats["stats"])
        patterns = detect_patterns(
            imputed, peak, run_len, float(residual_z[peak]), contributors, trend, feats["stats"]
        ) or ["unclassified"]
        narrative = build_narrative(s.equipment_id, time_label(s.times[peak]), contributors, patterns)
        evidence = [
            {"column": c["column"], "observed": c["value"], "expected": c["baselineMedian"],
             "unit": c["unit"], "z": c["z"]}
            for c in contributors[:3]
        ]
        episodes.append({
            **shell,
            "patternTags": patterns,
            "narrative": narrative,
            "contributors": contributors,
            "evidence": evidence,
            "recommendations": recommendations_for_patterns(patterns, shell["severity"]),
        })

    severity_codes = [SEVERITY_CODE[sev] for sev in scored["severity"]]
    values_out: dict[str, list] = {}
    for c in NUMERIC_COLS:
        values_out[c] = [None if not math.isfinite(v) else round(v, 3) for v in imputed.data[c]]
    return {
        "equipmentId": s.equipment_id,
        "insufficientData": False,
        "score": [round(float(x), 4) for x in scored["score"]],
        "severityCodes": severity_codes,
        "threshold": round(float(scored["threshold"]), 4),
        "health": health,
        "degradationTrend": round(trend * 1000) / 1000,
        "episodes": episodes,
        "energyTotalKwh": stats["energyTotalKwh"],
        "energyMeanKwh": stats["energyMeanKwh"],
        "count": n,
        "times": s.times,
        "values": values_out,
        "maintenance": maintenance,
        "daily": daily,
        "modelFeatures": {"if": len(feats["ifNames"]), "mlp": len(feats["mlpNames"])},
    }


def run_pipeline(csv_text: str, file_name: str) -> dict:
    t0 = time.time()
    parsed = build_dataset(csv_text, file_name)
    quality = parsed["quality"]
    series = parsed["series"]
    equipment_ids = sorted(series.keys())
    nominal_ms = median_interval(series[equipment_ids[0]].times)
    nominal_minutes = max(1, round(nominal_ms / 60_000))

    equipment: dict[str, dict] = {}
    analyzed_ids: list[str] = []
    for eq in equipment_ids:
        equipment[eq] = _run_equipment(series[eq], nominal_minutes)
        if not equipment[eq].get("insufficientData"):
            analyzed_ids.append(eq)

    if not analyzed_ids:
        reasons = "; ".join(f"{eq} -> {equipment[eq].get('insufficientReason', 'unknown')}" for eq in equipment_ids)
        raise DatasetError(
            "No equipment unit could be analysed: " + reasons
            + ". Upload a CSV with at least " + str(MIN_DATA)
            + " observations per unit (including a usable 'Chiller Energy Consumption' column)."
        )

    episodes: list[dict] = []
    for eq in analyzed_ids:
        episodes.extend(equipment[eq]["episodes"])
    episodes.sort(key=lambda e: (SEVERITY_RANK[e["severity"]], e["peakScore"]), reverse=True)

    if_features = max(equipment[eq]["modelFeatures"]["if"] for eq in analyzed_ids)
    mlp_features = max(equipment[eq]["modelFeatures"]["mlp"] for eq in analyzed_ids)
    model = {
        "isolationForest": {"trees": 80, "maxSamples": 256, "features": if_features},
        "residualModel": {"hiddenUnits": 24, "epochs": 150, "features": mlp_features},
        "fusion": {"ifWeight": 0.25, "residualWeight": 0.75,
                   "thresholdQuantile": 0.975, "joinWindow": 12},
        "runtimeMs": round((time.time() - t0) * 1000),
    }

    return {
        "quality": quality,
        "equipment": equipment,
        "episodes": episodes,
        "model": model,
        "nominalIntervalMinutes": nominal_minutes,
        "generatedAt": int(time.time() * 1000),
    }


def series_payload(result: dict, equipment_id: str, variable: str) -> dict:
    """Per-unit arrays for the chart, fetched on demand."""
    rep = result["equipment"][equipment_id]
    return {
        "equipmentId": equipment_id,
        "variable": variable,
        "times": rep["times"],
        "values": rep.get("values", {}).get(variable, []),
        "score": rep["score"],
        "severityCodes": rep["severityCodes"],
    }