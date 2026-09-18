"""Smoke test: run the full pipeline on the synthetic demo dataset and verify
the five embedded fault scenarios are detected.

    python scripts/smoke.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.pipeline.demo import generate_demo_csv  # noqa: E402
from backend.pipeline.engine import run_pipeline  # noqa: E402
from backend.pipeline.interpretation import time_label  # noqa: E402

D0 = 1_566_086_400_000  # 2019-08-18T00:00:00Z

WINDOWS = [
    ("c1 transient spike (12h)", "CHILLER-01", 60, 60.6),
    ("c1 condenser fault (3.5d)", "CHILLER-01", 88, 91.7),
    ("c2 degradation ramp (from Feb)", "CHILLER-02", 172, 262),
    ("c3 flow imbalance (24h)", "CHILLER-03", 72, 73.5),
    ("c3 frozen sensor (12h)", "CHILLER-03", 85, 85.6),
]


def d_of(ms: int) -> float:
    return (ms - D0) / 86_400_000.0


def main() -> None:
    import time

    t0 = time.time()
    csv = generate_demo_csv()
    rows = csv.count("\n")
    print(f"demo rows: {rows}")
    result = run_pipeline(csv, "yukthi-demo-chillers.csv")
    print(f"pipeline runtime: {time.time() - t0:.1f}s")
    q = result["quality"]
    print(f"quality: {q['rowCount']} rows, {q['equipmentCount']} units, "
          f"gaps={q['gapCount']}, dups={q['duplicatePairs']}, missing={q['missingByColumn']}")

    all_ok = True
    for name, eq, d0, d1 in WINDOWS:
        hits = [
            e for e in result["episodes"]
            if e["equipmentId"] == eq and d_of(e["startTime"]) < d1 and d_of(e["endTime"]) > d0
        ]
        sev = ",".join(h["severity"] for h in hits)
        ok = len(hits) > 0
        all_ok = all_ok and ok
        print(f"{'HIT ' if ok else 'MISS'} {name:<28} {('FOUND (' + sev + ')') if hits else ''}")

    print("\n--- per-unit episodes (top 6) ---")
    for eq in q["equipmentIds"]:
        rep = result["equipment"][eq]
        print(f"\n== {eq}: health={rep['health']}, trend={rep['degradationTrend']}, "
              f"episodes={len(rep['episodes'])}, threshold={rep['threshold']:.3f}")
        for ep in rep["episodes"][:6]:
            print(f"  {ep['severity']:<6} {time_label(ep['startTime'])}  "
                  f"dur={ep['durationHours']}h peak={ep['peakScore']}  [{', '.join(ep['patternTags'])}]")

    by_severity: dict[str, int] = {}
    for e in result["episodes"]:
        by_severity[e["severity"]] = by_severity.get(e["severity"], 0) + 1
    print(f"\nepisodes total: {len(result['episodes'])} by severity: {by_severity}")
    print("\nRESULT:", "ALL SCENARIOS DETECTED" if all_ok else "SOME SCENARIOS MISSED")


if __name__ == "__main__":
    main()