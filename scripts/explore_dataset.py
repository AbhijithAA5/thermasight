"""ThermaSight — exploratory dataset analysis.

Verifies the supplied YUKTHI 2026 development CSV against the Data
Specification contract (rows, units, duplicates, missing-value counts,
cadence), and produces a compact report file.

Run:  python scripts/explore_dataset.py <path-to-csv> [--out report.json]
"""
from __future__ import annotations

import argparse
import json
import sys

import numpy as np
import pandas as pd

EXPECTED = {
    "rows": 25_003,
    "fields": 11,
    "units": 3,
    "duplicates": 0,
    "missing": {
        "Chilled Water Rate (L/sec)": 40,
        "Cooling Water Temperature (C)": 5,
        "Building Load (RT)": 19,
        "Chiller Energy Consumption (kWh)": 9,
        "Humidity (%)": 20,
        "Wind Speed (mph)": 23,
        "Pressure (in)": 12,
    },
    "no_missing": ["timestamp", "equipment_id", "Outside Temperature (F)", "Dew Point (F)"],
    "nominal_interval_min": 30,
}

NUMERIC_COLS = [
    "Chilled Water Rate (L/sec)",
    "Cooling Water Temperature (C)",
    "Building Load (RT)",
    "Chiller Energy Consumption (kWh)",
    "Outside Temperature (F)",
    "Dew Point (F)",
    "Humidity (%)",
    "Wind Speed (mph)",
    "Pressure (in)",
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("csv", help="path to the development CSV")
    ap.add_argument("--out", default="dataset_report.json", help="output JSON path")
    args = ap.parse_args()

    df = pd.read_csv(args.csv)
    report: dict = {"source": args.csv}

    # --- shape & identity ---
    report["rows"] = int(len(df))
    report["columns"] = [str(c) for c in df.columns]
    report["field_count"] = int(df.shape[1])
    report["rows_match_spec"] = len(df) == EXPECTED["rows"]

    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    n_bad_ts = int(df["timestamp"].isna().sum())
    report["unparseable_timestamps"] = n_bad_ts
    report["nominal_interval_minutes"] = EXPECTED["nominal_interval_min"]

    units = df["equipment_id"].astype(str)
    report["equipment_ids"] = sorted(units.unique().tolist())
    report["equipment_count"] = int(units.nunique())

    dups = int(df.duplicated(subset=["equipment_id", "timestamp"]).sum())
    report["duplicate_pairs"] = dups
    report["duplicates_match_spec"] = dups == EXPECTED["duplicates"]

    # --- per unit ---
    per_unit = {}
    for eq, g in df.groupby("equipment_id", sort=True):
        ts = g["timestamp"].sort_values()
        diffs = ts.diff().dropna().dt.total_seconds() / 60.0
        per_unit[eq] = {
            "rows": int(len(g)),
            "time_start": str(g["timestamp"].min()),
            "time_end": str(g["timestamp"].max()),
            "span_days": float((g["timestamp"].max() - g["timestamp"].min()).total_seconds() / 86400.0),
            "median_interval_min": float(diffs.median()),
            "gap_over_60min_count": int((diffs > 60).sum()),
            "max_gap_minutes": float(diffs.max()) if len(diffs) else None,
        }
    report["per_unit"] = per_unit

    # --- missing values ---
    missing = {str(c): {"count": int(v), "pct": float(v / len(df) * 100)} for c, v in df.isna().sum().items() if v > 0}
    report["missing_values"] = missing
    missing_ok = all(missing.get(c, {}).get("count", 0) == n for c, n in EXPECTED["missing"].items())
    report["missing_match_spec"] = missing_ok
    report["missing_spec"] = EXPECTED["missing"]

    # --- numeric summaries ---
    stats = {}
    for c in [c for c in NUMERIC_COLS if c in df.columns]:
        s = pd.to_numeric(df[c], errors="coerce")
        stats[c] = {
            "min": None if s.isna().all() else float(s.min()),
            "p25": None if s.isna().all() else float(s.quantile(0.25)),
            "median": None if s.isna().all() else float(s.median()),
            "mean": None if s.isna().all() else float(s.mean()),
            "p75": None if s.isna().all() else float(s.quantile(0.75)),
            "max": None if s.isna().all() else float(s.max()),
            "std": None if s.isna().all() else float(s.std()),
        }
    report["numeric_summaries"] = stats

    # --- per-unit energy/load behaviour ---
    load_col, e_col = "Building Load (RT)", "Chiller Energy Consumption (kWh)"
    if load_col in df.columns and e_col in df.columns:
        unit_behaviour = {}
        for eq, g in df.groupby("equipment_id", sort=True):
            unit_behaviour[eq] = {
                "load_mean_rt": float(g[load_col].mean()),
                "energy_mean_kwh": float(g[e_col].mean()),
                "energy_total_kwh": float(g[e_col].sum()),
                "corr_load_energy": float(g[[load_col, e_col]].corr().iloc[0, 1]),
            }
        report["unit_behaviour"] = unit_behaviour
        # whole-fleet correlation
        report["corr_load_energy_fleet"] = float(df[[load_col, e_col]].corr().iloc[0, 1])

    report["expected"] = EXPECTED

    with open(args.out, "w") as fh:
        json.dump(report, fh, indent=2, default=str)

    # --- console digest ---
    print(f"rows: {report['rows']} (spec {EXPECTED['rows']}) match={report['rows_match_spec']}")
    print(f"fields: {report['field_count']} | units: {report['equipment_ids']}")
    print(f"dup pairs: {report['duplicate_pairs']} | bad timestamps: {report['unparseable_timestamps']}")
    print("missing values:")
    for c, m in missing.items():
        print(f"  {c:40s} {m['count']:6d}  {m['pct']:.3f}%")
    print(f"missing_match_spec={report.get('missing_match_spec')}")
    print("per unit:")
    for eq, p in per_unit.items():
        print(f"  {eq}: rows={p['rows']} span={p['span_days']:.1f}d median interval={p['median_interval_min']:.0f}min "
              f"gaps>60m={p['gap_over_60min_count']} maxgap={p['max_gap_minutes']:.0f}m")
    print(f"report written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())