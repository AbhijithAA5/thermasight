"""Edge-case battery: the pipeline must degrade gracefully — never crash — on
the missing-data and malformed-input classes the YUKTHI 2026 documents
anticipate ("missing values may occur", "gaps are a data characteristic",
"reusable pipeline", "no hard-coded rows").

Each case asserts a *specific* graceful outcome. Run:

    python scripts/edge_cases.py
"""
from __future__ import annotations

import io
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from backend.pipeline.parser import DatasetError  # noqa: E402
from backend.pipeline.engine import run_pipeline  # noqa: E402

DATA = os.path.join(ROOT, "data", "yukthi_development.csv")

ok_all = True


def check(name: str, cond: bool, detail: str = "") -> None:
    global ok_all
    ok_all = ok_all and bool(cond)
    print(f"{'PASS' if cond else 'FAIL'} {name:<46} {detail}")


def real_csv() -> str:
    with open(DATA, "r", encoding="utf-8") as fh:
        return fh.read()


def rows_of(text: str) -> list[list[str]]:
    return [r for r in io.StringIO(text) if r.strip()]


def build(headers: list[str], body: list[list[str]]) -> str:
    return "\n".join([",".join(headers)] + [",".join(r) for r in body]) + "\n"


HDR = ["timestamp", "equipment_id", "Chilled Water Rate (L/sec)",
       "Cooling Water Temperature (C)", "Building Load (RT)",
       "Chiller Energy Consumption (kWh)", "Outside Temperature (F)",
       "Dew Point (F)", "Humidity (%)", "Wind Speed (mph)", "Pressure (in)"]


def main() -> int:
    real = real_csv()
    parsed_rows = rows_of(real)
    body = [r.strip().split(",") for r in parsed_rows[1:] if r.strip()]

    t0 = time.time()

    # 1. Control: the real dataset still analyses end-to-end.
    res = run_pipeline(real, "control.csv")
    check("control: real dataset analyses", len(res["episodes"]) > 100,
          f"{len(res['episodes'])} episodes, runtime {res['model']['runtimeMs']}ms")
    check("control: no absent measurement columns", res["quality"]["missingColumns"] == [],
          str(res["quality"]["missingColumns"]))

    # 2. Several measurement columns entirely absent -> degrade, report, analyse.
    drop = {"Dew Point (F)", "Humidity (%)", "Wind Speed (mph)", "Pressure (in)"}
    keep_idx = [i for i, h in enumerate(HDR) if h not in drop]
    hdr2 = [HDR[i] for i in keep_idx]
    body2 = [[r[i] for i in keep_idx] for r in body]
    res = run_pipeline(build(hdr2, body2), "missing-4-cols.csv")
    expected_absent = ["Dew Point", "Humidity", "Wind Speed", "Pressure"]
    check("missing columns: 4 columns absent", sorted(res["quality"]["missingColumns"]) == sorted(expected_absent),
          str(res["quality"]["missingColumns"]))
    check("missing columns: still analyses", len(res["episodes"]) > 0, f"{len(res['episodes'])} episodes")

    # 3. Skinny CSV — only identity + load + energy.
    keep_idx = [0, 1, 4, 5]
    hdr3 = [HDR[i] for i in keep_idx]
    body3 = [[r[i] for i in keep_idx] for r in body]
    res = run_pipeline(build(hdr3, body3), "skinny.csv")
    check("skinny: load+energy only, works", len(res["episodes"]) > 0,
          f"{len(res['equipment'])} units, {len(res['episodes'])} episodes")
    check("skinny: 7 absent columns listed", len(res["quality"]["missingColumns"]) == 7,
          str(res["quality"]["missingColumns"]))

    # 4. One tiny unit + one full unit -> tiny unit skipped with a reason, full unit analysed.
    full = [r for r in body if r[1] == "CHILLER-02"]
    tiny = [r for r in body if r[1] == "CHILLER-03"][:30]
    res = run_pipeline(build(HDR, full + tiny), "tiny-plus-full.csv")
    rep = res["equipment"].get("CHILLER-03", {})
    check("tiny unit: marked insufficient, not crashed",
          rep.get("insufficientData") is True, f"reason: {rep.get('insufficientReason')}")
    check("tiny unit: full unit still analysed",
          res["equipment"]["CHILLER-02"].get("insufficientData") is False,
          f"episodes: {len(res['equipment']['CHILLER-02']['episodes'])}")

    # 5. All data insufficient (30 rows, single unit) -> clear DatasetError.
    short = tiny[:30]
    try:
        run_pipeline(build(HDR, short), "too-short.csv")
        check("too short: raises cleanly", False, "no error raised")
    except DatasetError as e:
        check("too short: raises cleanly", "observations" in str(e), str(e)[:90])

    # 6. Garbage timestamps mixed in -> dropped rows counted, analysis proceeds.
    body6 = [list(r) for r in body[:2000]]
    for i in range(0, 100):
        body6[i][0] = "not-a-date"
    res = run_pipeline(build(HDR, body6), "garbage-ts.csv")
    check("garbage timestamps: badRows counted", res["quality"]["badRows"] >= 100,
          f"badRows={res['quality']['badRows']}")
    check("garbage timestamps: still analyses", len(res["episodes"]) > 0, "")

    # 7. Non-numeric cells -> treated as missing, analysis proceeds.
    body7 = [list(r) for r in body[:2000]]
    for i in range(100, 300):
        body7[i][2] = "abc"
    res = run_pipeline(build(HDR, body7), "non-numeric.csv")
    miss = res["quality"]["missingByColumn"].get("Chilled Water Rate", 0)
    check("non-numeric cells: counted as missing", miss >= 200, f"missing CWR={miss}")

    # 8. Duplicate rows -> counted, not fatal.
    res = run_pipeline(build(HDR, body[:1500] + body[:500]), "dups.csv")
    check("duplicates: counted", res["quality"]["duplicatePairs"] == 500,
          f"dups={res['quality']['duplicatePairs']}")

    # 9-11. Structural failures -> clear DatasetError messages, never a 500-style crash.
    for name, text, needle in [
        ("empty file", "", "empty"),
        ("CSV with commas only", ",,,,\n,,,,\n", "empty"),
        ("header only", build(HDR, []), "header row but no observations"),
        ("no energy column", build([h for h in HDR if h != "Chiller Energy Consumption (kWh)"],
                                   [[r[i] for i in range(len(HDR)) if HDR[i] != "Chiller Energy Consumption (kWh)"] for r in body[:100]]),
         "Chiller Energy Consumption"),
    ]:
        try:
            run_pipeline(text, name)
            check(f"structure: {name} raises", False, "no error")
        except DatasetError as e:
            check(f"structure: {name} raises", needle.lower() in str(e).lower(), str(e)[:70])

    print(f"\nbattery runtime: {time.time() - t0:.1f}s")
    print("RESULT:", "ALL EDGE CASES HANDLED" if ok_all else "EDGE CASES FAILED")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())