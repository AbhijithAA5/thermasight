"""Smoke test against the bundled YUKTHI 2026 development dataset.

Checks the pipeline against the real data contract (row count, units,
duplicate count, per-column missing-value totals as stated in the Data
Specification) and verifies the analysis output is coherent (episodes carry
severity, patterns, narrative, evidence and recommendations).

    python scripts/smoke_real.py
"""
from __future__ import annotations

import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from backend.pipeline.engine import run_pipeline  # noqa: E402
from backend.pipeline.interpretation import time_label  # noqa: E402

SPEC = {
    "rows": 25_003,
    "units": ["CHILLER-01", "CHILLER-02", "CHILLER-03"],
    "duplicates": 0,
    "missing": {
        "Chilled Water Rate": 40,
        "Cooling Water Temperature": 5,
        "Building Load": 19,
        "Chiller Energy Consumption": 9,
        "Humidity": 20,
        "Wind Speed": 23,
        "Pressure": 12,
    },
}


def main() -> int:
    csv_path = os.path.join(ROOT, "data", "yukthi_development.csv")
    with open(csv_path, "r", encoding="utf-8") as fh:
        csv_text = fh.read()

    t0 = time.time()
    result = run_pipeline(csv_text, "yukthi-development-dataset.csv")
    runtime = time.time() - t0
    q = result["quality"]

    ok = True
    def check(name: str, cond: bool, detail: str = "") -> None:
        nonlocal ok
        ok = ok and bool(cond)
        print(f"{'PASS' if cond else 'FAIL'} {name:<42} {detail}")

    check("row count == spec", q["rowCount"] == SPEC["rows"], f"{q['rowCount']} rows")
    check("equipment ids == spec", sorted(q["equipmentIds"]) == SPEC["units"], ", ".join(q["equipmentIds"]))
    check("duplicate pairs == 0", q["duplicatePairs"] == 0, f"dups={q['duplicatePairs']}")
    missing_ok = all(q["missingByColumn"].get(c, 0) == n for c, n in SPEC["missing"].items())
    check("missing counts == spec", missing_ok, json.dumps(q["missingByColumn"]))
    check("gaps reported (data characteristic)", q["gapCount"] >= 1, f"gaps={q['gapCount']}, max={q['maxGapHours']}h")
    check("episodes produced", len(result["episodes"]) > 0, f"{len(result['episodes'])} episodes")
    check("runtime sane (< 120s)", runtime < 120, f"{runtime:.1f}s")

    ep_ok = True
    for e in result["episodes"]:
        ok_tags = bool(e.get("patternTags"))
        ok_narr = bool(e.get("narrative", "").strip())
        ok_evid = len(e.get("evidence", [])) >= 1
        ok_recs = len(e.get("recommendations", [])) >= 1
        ok_sev = e.get("severity") in ("watch", "alert", "action")
        if not (ok_tags and ok_narr and ok_evid and ok_recs and ok_sev):
            ep_ok = False
            print("   bad episode:", e.get("id"), e.get("patternTags"), e.get("severity"))
    check("every episode has tags+narrative+evidence+recs", ep_ok)

    print(f"\nruntime: {runtime:.1f}s, nominal interval: {result['nominalIntervalMinutes']} min")
    for eq, rep in result["equipment"].items():
        sev = {"watch": 0, "alert": 0, "action": 0}
        for e in rep["episodes"]:
            sev[e["severity"]] += 1
        print(f"  {eq}: health={rep['health']} threshold={rep['threshold']:.3f} "
              f"episodes={len(rep['episodes'])} {sev}")
    print("\nRESULT:", "REAL DATASET OK" if ok else "REAL DATASET FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())