"""ThermaSight — preprocessing + feature engineering.
Missing values, time features, trailing rolling statistics, lags, robust
standardization. Everything is per equipment unit; rolling windows are strictly
trailing (no future leakage). Columns that arrive entirely missing (absent from
the file, or all-blank) are excluded from the feature vectors instead of
poisoning them.
"""

from __future__ import annotations

import math

import numpy as np

from .types import NUMERIC_COLS, TARGET_COL, Series

WARMUP = 96  # points excluded from scoring (rolling windows not yet meaningful)
ROLL_W = 48  # trailing window = 24h at 30-min cadence


def median(vals) -> float:
    arr = np.asarray(vals, dtype=float)
    if arr.size == 0:
        return float("nan")
    return float(np.median(arr))


def mad(vals, center: float | None = None) -> float:
    arr = np.asarray(vals, dtype=float)
    if arr.size == 0:
        return float("nan")
    c = center if center is not None else float(np.median(arr))
    return float(1.4826 * np.median(np.abs(arr - c)))


def robust_stats(vals) -> tuple[float, float]:
    m = median(vals)
    s = mad(vals, m)
    return m, s


def impute_series(s: Series, nominal_minutes: int) -> Series:
    """Fill missing values: linear interpolation across short gaps (<= 4x the
    nominal interval), last-value carry-forward across longer gaps."""
    max_interp_ms = nominal_minutes * 60_000 * 4
    out = Series(s.equipment_id)
    out.times = s.times
    out.hours = s.hours
    out.days = s.days
    for c in NUMERIC_COLS:
        v = list(s.data[c])
        n = len(v)
        i = 0
        while i < n:
            if math.isfinite(v[i]):
                i += 1
                continue
            j = i
            while j < n and not math.isfinite(v[j]):
                j += 1
            prev = v[i - 1] if i > 0 else None
            nextv = v[j] if j < n else None
            prev_t = s.times[i - 1] if i > 0 else None
            next_t = s.times[j] if j < n else None
            if (
                prev is not None
                and nextv is not None
                and prev_t is not None
                and next_t is not None
                and (next_t - prev_t) <= max_interp_ms
            ):
                for k in range(i, j):
                    f = (s.times[k] - prev_t) / (next_t - prev_t)
                    v[k] = prev + (nextv - prev) * f
            else:
                fill = prev if prev is not None else nextv
                fill = fill if fill is not None else float("nan")
                for k in range(i, j):
                    v[k] = fill
            i = j
        out.data[c] = v
        out.observed[c] = s.observed[c]
    return out


def _slug(c: str) -> str:
    return " ".join(c.split()).replace(" ", "_").lower()


def build_features(s: Series) -> dict:
    n = len(s.times)
    stats = {c: robust_stats(s.data[c]) for c in NUMERIC_COLS}
    # Columns that are entirely missing (no finite values, or no variation)
    # must not poison the model inputs — drop them from the vectors.
    usable = [
        c for c in NUMERIC_COLS
        if math.isfinite(stats[c][0]) and (stats[c][1] or 0) > 1e-9
    ]
    z = lambda c, i: max(-6.0, min(6.0, (s.data[c][i] - stats[c][0]) / (stats[c][1] or 1e-9)))
    energy_ok = TARGET_COL in usable
    load_ok = "Building Load" in usable

    en_roll_mean = np.zeros(n)
    en_roll_std = np.zeros(n)
    ld_roll_mean = np.zeros(n)
    en_sum = 0.0
    en_sum2 = 0.0
    ld_sum = 0.0
    for i in range(n):
        if energy_ok:
            ei = float(s.data[TARGET_COL][i]) if math.isfinite(s.data[TARGET_COL][i]) else 0.0
            en_sum += ei
            en_sum2 += ei * ei
        if load_ok:
            li = float(s.data["Building Load"][i]) if math.isfinite(s.data["Building Load"][i]) else 0.0
            ld_sum += li
        if i >= ROLL_W:
            if energy_ok:
                ej = float(s.data[TARGET_COL][i - ROLL_W]) if math.isfinite(s.data[TARGET_COL][i - ROLL_W]) else 0.0
                en_sum -= ej
                en_sum2 -= ej * ej
            if load_ok:
                lj = float(s.data["Building Load"][i - ROLL_W]) if math.isfinite(s.data["Building Load"][i - ROLL_W]) else 0.0
                ld_sum -= lj
        if i >= ROLL_W - 1:
            if energy_ok:
                mn = en_sum / ROLL_W
                en_roll_mean[i] = mn
                en_roll_std[i] = math.sqrt(max(0.0, en_sum2 / ROLL_W - mn * mn))
            if load_ok:
                ld_roll_mean[i] = ld_sum / ROLL_W
        else:
            if energy_ok:
                en_roll_mean[i] = float(s.data[TARGET_COL][i]) if math.isfinite(s.data[TARGET_COL][i]) else 0.0
                en_roll_std[i] = 0.0
            if load_ok:
                ld_roll_mean[i] = float(s.data["Building Load"][i]) if math.isfinite(s.data["Building Load"][i]) else 0.0

    if_x: list[list[float]] = []
    mlp_x: list[list[float]] = []
    y: list[float] = []
    col_z: dict[str, list[float]] = {c: [] for c in usable}

    if_names = [_slug(c) for c in usable] + ["hour_sin", "hour_cos", "dow_sin", "dow_cos"]
    if energy_ok:
        if_names += ["en_roll_mean", "en_roll_std", "en_lag1", "en_lag48"]
    if load_ok:
        if_names.append("ld_roll_mean")
    ctx = [c for c in usable if c != TARGET_COL]
    mlp_names = [_slug(c) for c in ctx] + ["hour_sin", "hour_cos", "dow_sin", "dow_cos",
                                           "doy_sin", "doy_cos"]
    if energy_ok:
        mlp_names += ["en_lag1", "en_lag48"]

    for i in range(n):
        hour = s.hours[i]
        day = s.days[i]
        doy_frac = (s.times[i] % 31_557_600_000) / 31_557_600_000.0
        h_sin = math.sin((hour / 24.0) * 2 * math.pi)
        h_cos = math.cos((hour / 24.0) * 2 * math.pi)
        d_sin = math.sin((day / 7.0) * 2 * math.pi)
        d_cos = math.cos((day / 7.0) * 2 * math.pi)
        doy_sin = math.sin(doy_frac * 2 * math.pi)
        doy_cos = math.cos(doy_frac * 2 * math.pi)
        lag1 = z(TARGET_COL, i - 1) if (energy_ok and i > 0) else 0.0
        lag48 = z(TARGET_COL, i - 48) if (energy_ok and i >= 48) else 0.0

        row_if = [z(c, i) for c in usable] + [h_sin, h_cos, d_sin, d_cos]
        if energy_ok:
            en_rm = (en_roll_mean[i] - stats[TARGET_COL][0]) / (stats[TARGET_COL][1] or 1e-9)
            en_rs = en_roll_std[i] / (stats[TARGET_COL][1] or 1e-9)
            row_if += [max(-6.0, min(6.0, en_rm)), max(-6.0, min(6.0, en_rs)), lag1, lag48]
        if load_ok:
            ld_rm = (ld_roll_mean[i] - stats["Building Load"][0]) / (stats["Building Load"][1] or 1e-9)
            row_if.append(max(-6.0, min(6.0, ld_rm)))
        if_x.append(row_if)

        row_mlp = [z(c, i) for c in ctx] + [h_sin, h_cos, d_sin, d_cos, doy_sin, doy_cos]
        if energy_ok:
            row_mlp += [lag1, lag48]
        mlp_x.append(row_mlp)

        y.append(z(TARGET_COL, i) if energy_ok else 0.0)
        for c in usable:
            col_z[c].append(z(c, i))

    return {
        "ifX": np.asarray(if_x, dtype=float),
        "mlpX": np.asarray(mlp_x, dtype=float),
        "y": np.asarray(y, dtype=float),
        "ifNames": if_names,
        "mlpNames": mlp_names,
        "stats": stats,
        "colZ": col_z,
        "usableCols": usable,
    }