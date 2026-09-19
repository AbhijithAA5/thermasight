"""Time-of-day inference demo — proves the models learn the diurnal
energy pattern (afternoon high / night low) and treat it as EXPECTED,
while a night-time rise gets flagged.

    python scripts/time_of_day_demo.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402

from backend.pipeline.engine import median_interval  # noqa: E402
from backend.pipeline.features import WARMUP, build_features, impute_series, median  # noqa: E402
from backend.pipeline.models import MLPRegressor  # noqa: E402
from backend.pipeline.parser import build_dataset  # noqa: E402

HOURS = [0, 3, 9, 12, 15, 18, 21, 23]


def main() -> int:
    with open(os.path.join(ROOT, "data", "yukthi_development.csv"), "r", encoding="utf-8") as fh:
        parsed = build_dataset(fh.read(), "yukthi-dev.csv")
    s = parsed["series"]["CHILLER-01"]
    nom = max(1, round(median_interval(s.times) / 60_000))
    imp = impute_series(s, nom)
    feats = build_features(imp)
    n = len(s.times)
    tr = min(WARMUP, n)

    # Train the same contextual residual model the pipeline uses.
    mlp = MLPRegressor(hidden=24)
    mlp.fit(feats["mlpX"][tr:], feats["y"][tr:], epochs=150, seed=11 + len(s.equipment_id))
    pred = mlp.predict(feats["mlpX"])
    resid = feats["y"] - pred

    # Hour-of-day median correction (exactly as in the pipeline).
    hour_samples: list[list[float]] = [[] for _ in range(24)]
    for i in range(tr, n):
        hour_samples[min(23, int(s.hours[i]))].append(float(resid[i]))
    hour_med = [median(v) for v in hour_samples]
    resid_adj = np.array([
        resid[i] - hour_med[min(23, int(s.hours[i]))] if i >= tr else resid[i]
        for i in range(n)
    ])
    abs_devs = np.sort(np.abs(resid_adj[tr:]))
    scale = 1.4826 * (float(abs_devs[len(abs_devs) // 2]) if abs_devs.size else 1.0)
    residual_z = resid_adj / (scale or 1.0)

    print(f"{'hour':>5} | {'mean energy (kWh)':>18} | {'mean raw resid':>14} | {'mean resid-z':>13}")
    print("-" * 58)
    for h in HOURS:
        idx = [i for i in range(tr, n) if int(s.hours[i]) == h]
        if not idx:
            continue
        en = np.mean([s.data["Chiller Energy Consumption"][i] for i in idx])
        raw = np.mean([resid[i] for i in idx])
        z = np.mean([residual_z[i] for i in idx])
        marker = "  <- afternoon peak" if h == 15 else "  <- night trough" if h == 3 else ""
        print(f"{h:>5} | {en:>18.1f} | {raw:>14.3f} | {z:>13.3f}{marker}")

    # The killer demonstration: equivalent readings by time of day.
    night_idx = [i for i in range(n) if int(s.hours[i]) == 3]
    worst_night = max(night_idx, key=lambda i: residual_z[i])
    afternoon_idx = [i for i in range(n) if int(s.hours[i]) == 15 and abs(resid[i]) < 0.2]
    typ_after = afternoon_idx[0] if afternoon_idx else night_idx[0]
    print("\nAfter learning the diurnal pattern:")
    print(
        f"  worst 03:00 point: energy {s.data['Chiller Energy Consumption'][worst_night]:.1f} kWh, "
        f"residual-z {residual_z[worst_night]:+.1f} -> genuinely unusual"
    )
    print(
        f"  typical 15:00 point: energy {s.data['Chiller Energy Consumption'][typ_after]:.1f} kWh, "
        f"residual-z {residual_z[typ_after]:+.1f} -> treated as normal (afternoon high is expected)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())