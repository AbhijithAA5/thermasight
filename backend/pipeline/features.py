"""ThermaSight — preprocessing + feature engineering.
Missing values, time features, trailing rolling statistics, lags, robust
standardization. Everything is per equipment unit; rolling windows are strictly
trailing (no future leakage)."""

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


def build_features(s: Series) -> dict:
    n = len(s.times)
    stats = {c: robust_stats(s.data[c]) for c in NUMERIC_COLS}
    z = lambda c, i: max(-6.0, min(6.0, (s.data[c][i] - stats[c][0]) / (stats[c][1] or 1e-9)))

    en = np.asarray(s.data[TARGET_COL], dtype=float)
    ld = np.asarray(s.data["Building Load"], dtype=float)
    en_roll_mean = np.zeros(n)
    en_roll_std = np.zeros(n)
    ld_roll_mean = np.zeros(n)
    en_sum = 0.0
    en_sum2 = 0.0
    ld_sum = 0.0
    for i in range(n):
        ei = float(en[i]) if math.isfinite(en[i]) else 0.0
        li = float(ld[i]) if math.isfinite(ld[i]) else 0.0
        en_sum += ei
        en_sum2 += ei * ei
        ld_sum += li
        if i >= ROLL_W:
            ej = float(en[i - ROLL_W]) if math.isfinite(en[i - ROLL_W]) else 0.0
            lj = float(ld[i - ROLL_W]) if math.isfinite(ld[i - ROLL_W]) else 0.0
            en_sum -= ej
            en_sum2 -= ej * ej
            ld_sum -= lj
        if i >= ROLL_W - 1:
            mn = en_sum / ROLL_W
            en_roll_mean[i] = mn
            en_roll_std[i] = math.sqrt(max(0.0, en_sum2 / ROLL_W - mn * mn))
            ld_roll_mean[i] = ld_sum / ROLL_W
        else:
            en_roll_mean[i] = ei
            en_roll_std[i] = 0.0
            ld_roll_mean[i] = li

    if_x: list[list[float]] = []
    mlp_x: list[list[float]] = []
    y: list[float] = []
    col_z: dict[str, list[float]] = {c: [] for c in NUMERIC_COLS}

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
        lag1 = z(TARGET_COL, i - 1) if i > 0 else 0.0
        lag48 = z(TARGET_COL, i - 48) if i >= 48 else 0.0

        en_rm = (en_roll_mean[i] - stats[TARGET_COL][0]) / (stats[TARGET_COL][1] or 1e-9)
        en_rs = en_roll_std[i] / (stats[TARGET_COL][1] or 1e-9)
        ld_rm = (ld_roll_mean[i] - stats["Building Load"][0]) / (stats["Building Load"][1] or 1e-9)

        if_x.append([
            z(TARGET_COL, i), z("Building Load", i), z("Chilled Water Rate", i),
            z("Cooling Water Temperature", i), z("Outside Temperature", i), z("Dew Point", i),
            z("Humidity", i), z("Wind Speed", i), z("Pressure", i),
            h_sin, h_cos, d_sin, d_cos,
            max(-6.0, min(6.0, en_rm)), max(-6.0, min(6.0, en_rs)), max(-6.0, min(6.0, ld_rm)),
            lag1, lag48,
        ])
        mlp_x.append([
            z("Building Load", i), z("Chilled Water Rate", i), z("Cooling Water Temperature", i),
            z("Outside Temperature", i), z("Dew Point", i), z("Humidity", i),
            z("Wind Speed", i), z("Pressure", i),
            h_sin, h_cos, d_sin, d_cos, doy_sin, doy_cos, lag1, lag48,
        ])
        y.append(z(TARGET_COL, i))
        for c in NUMERIC_COLS:
            col_z[c].append(z(c, i))

    return {
        "ifX": np.asarray(if_x, dtype=float),
        "mlpX": np.asarray(mlp_x, dtype=float),
        "y": np.asarray(y, dtype=float),
        "ifNames": ["energy", "load", "chilled_water_rate", "cooling_water_temp", "outside_temp",
                    "dew_point", "humidity", "wind", "pressure", "hour_sin", "hour_cos",
                    "dow_sin", "dow_cos", "en_roll_mean", "en_roll_std", "ld_roll_mean",
                    "en_lag1", "en_lag48"],
        "mlpNames": ["load", "chilled_water_rate", "cooling_water_temp", "outside_temp",
                     "dew_point", "humidity", "wind", "pressure", "hour_sin", "hour_cos",
                     "dow_sin", "dow_cos", "doy_sin", "doy_cos", "en_lag1", "en_lag48"],
        "stats": stats,
        "colZ": col_z,
    }