"""ThermaSight — interpretation layer: contributing factors, pattern
classification, plain-language narratives with concrete numbers."""

from __future__ import annotations

import math

from .features import median
from .types import COLUMN_UNITS, NUMERIC_COLS, TARGET_COL, Series

TRAIL = 24  # trailing baseline window (12h at 30-min cadence)

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def time_label(ms: int) -> str:
    import datetime as dt

    d = dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc)
    return f"{d.day:02d} {MONTHS[d.month - 1]} {d.year}, {d.hour:02d}:{d.minute:02d}"


def analyze_contributors(s: Series, idx: int, global_stats: dict) -> list[dict]:
    out: list[dict] = []
    start = max(0, idx - TRAIL)
    for c in NUMERIC_COLS:
        v = s.data[c][idx]
        if not math.isfinite(v):
            continue
        window = s.data[c][start:idx]
        base = median(window)
        devs = [abs(x - base) for x in window]
        sdev = 1.4826 * median(devs)
        scale = sdev if sdev > 1e-9 else (global_stats[c][1] or 1.0)
        z = (v - base) / scale
        if not math.isfinite(z):
            continue  # degenerate window (e.g. entirely missing) — not evidence
        out.append({
            "column": c,
            "z": round(z * 100) / 100,
            "value": round(v * 100) / 100,
            "baselineMedian": round(base * 100) / 100,
            "unit": COLUMN_UNITS[c],
            "direction": "high" if z > 0 else "low",
            "weight": 0.0,
        })
    max_abs = max([abs(o["z"]) for o in out] or [1e-9])
    for o in out:
        o["weight"] = round((abs(o["z"]) / max_abs) * 100) / 100
    out.sort(key=lambda o: -abs(o["z"]))
    return out[:4]


def _z_of(contributors: list[dict], c: str):
    for f in contributors:
        if f["column"] == c:
            return f["z"]
    return None


def detect_patterns(s: Series, idx: int, run_length: int, residual_z: float,
                    contributors: list[dict], trend: float, global_stats: dict) -> list[str]:
    tags: set[str] = set()
    en = _z_of(contributors, TARGET_COL)
    ld = _z_of(contributors, "Building Load")
    cwr = _z_of(contributors, "Chilled Water Rate")
    cwt = _z_of(contributors, "Cooling Water Temperature")
    hour = s.hours[idx]

    # Sensor stuck: any channel frozen over >= 12 trailing points while the
    # channel normally varies.
    for c in NUMERIC_COLS:
        v = s.data[c][idx]
        if not math.isfinite(v):
            continue
        run = 1
        k = idx - 1
        while k >= 0 and k >= idx - 48:
            if abs(s.data[c][k] - v) < 1e-9:
                run += 1
            else:
                break
            k -= 1
        if run >= 12 and global_stats[c][1] > 1e-9:
            tags.add("sensor_stuck")

    if en is not None and ld is not None and en > 2.2 and abs(ld) < 1.2:
        tags.add("high_consumption_low_load")
    if cwt is not None and cwt > 1.8 and residual_z > 1.8 and run_length >= 8:
        tags.add("cooling_water_drift")
    if cwr is not None and abs(cwr) > 1.8 and (ld is None or abs(ld) < 1.5):
        tags.add("flow_imbalance")
    if run_length <= 2 and en is not None and en > 3.5:
        tags.add("transient_spike")
    if (hour >= 22 or hour < 6) and en is not None and en > 1.6 and (ld is None or ld < 0.4):
        tags.add("offhours_standby")
    if trend > 0.35 and idx > len(s.times) * 0.4:
        tags.add("degradation_trend")
    if residual_z > 2 and (
        (ld is not None and abs(ld) > 2)
        or (cwr is not None and abs(cwr) > 2)
        or (cwt is not None and abs(cwt) > 2)
    ):
        tags.add("concurrent_context_shift")
    if en is not None and en > 2.2 and "high_consumption_low_load" not in tags:
        tags.add("contextual_energy_spike")
    return sorted(tags)


def _fmt2(v: float) -> str:
    return str(round(v)) if abs(v) >= 100 else f"{v:.1f}"


def build_narrative(equipment_id: str, time_label_str: str, contributors: list[dict],
                    patterns: list[str]) -> str:
    en = next((c for c in contributors if c["column"] == TARGET_COL), None)
    parts: list[str] = []
    if en is not None:
        direction = "above" if en["z"] > 0 else "below"
        sign = "+" if en["z"] > 0 else "\u2212"
        parts.append(
            f"At {time_label_str}, {equipment_id} consumed {_fmt2(en['value'])} {en['unit']} per interval, "
            f"{direction} its 12-hour baseline of {_fmt2(en['baselineMedian'])} {en['unit']} "
            f"({sign}{_fmt2(abs(en['z']))} \u03c3)."
        )
    else:
        parts.append(f"At {time_label_str}, {equipment_id} deviated from its expected operating pattern.")
    other = [c for c in contributors if c["column"] != TARGET_COL and abs(c["z"]) >= 1.2][:2]
    if other:
        desc = ", ".join(
            f"{c['column']} sits {c['direction']} at {_fmt2(abs(c['z']))} \u03c3 from its recent baseline"
            for c in other
        )
        parts.append(f"Supporting evidence: {desc}.")
    if "degradation_trend" in patterns:
        parts.append(
            "The residual trend across recent months is rising, consistent with gradual efficiency loss "
            "rather than a single event."
        )
    if "transient_spike" in patterns and "degradation_trend" not in patterns:
        parts.append(
            "The deviation is transient (2 or fewer intervals), so a passing operational event is more "
            "likely than a persistent fault."
        )
    if "sensor_stuck" in patterns:
        parts.append(
            "One or more channels are frozen at a constant value for hours, which is a sensor or "
            "transmission signature."
        )
    return " ".join(parts)