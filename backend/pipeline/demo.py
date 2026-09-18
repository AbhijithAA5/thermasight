"""ThermaSight — synthetic demo dataset, physics-informed and deterministic.
Conforms to the YUKTHI 2026 data contract (same columns, units, 30-minute
cadence, ~28,600 rows, missing values at spec-like rates, occasional gaps,
plus five embedded fault scenarios). This is a faithful port of the reference
TypeScript generator: same seed, same random draw order, same data."""

from __future__ import annotations

import math

from .models import gauss, mulberry32

DEMO_FILE_NAME = "yukthi-demo-chillers.csv"

START_MS = 1_566_086_400_000  # 2019-08-18T00:00:00Z
SLOTS_PER_DAY = 48
DAYS = 290

HEADS = [
    "timestamp", "equipment_id",
    "Chilled Water Rate", "Cooling Water Temperature", "Building Load",
    "Chiller Energy Consumption",
    "Outside Temperature", "Dew Point", "Humidity", "Wind Speed", "Pressure",
]

UNITS = [
    {"id": "CHILLER-01", "dStart": 0, "dEnd": 128, "share": 0.42, "effJitter": 0.02},
    {"id": "CHILLER-02", "dStart": 0, "dEnd": 262, "share": 0.36, "effJitter": 0.015},
    {"id": "CHILLER-03", "dStart": 0, "dEnd": 208, "share": 0.22, "effJitter": 0.025},
]


def _env_at(rng, d: int, slot: int) -> dict:
    g = lambda: gauss(rng)
    outside_f = (
        52 + 26 * math.cos((2 * math.pi * d) / 365)
        + 7 * math.sin((2 * math.pi * (slot / SLOTS_PER_DAY - 0.42)) * math.pi)
        + g() * 1.4
    )
    dew_f = outside_f - (14 + 6 * math.sin((2 * math.pi * d) / 365)) + g() * 1.1
    t_c = (outside_f - 32) / 1.8
    d_c = (dew_f - 32) / 1.8
    e1 = math.exp((17.625 * d_c) / (243.04 + d_c))
    e2 = math.exp((17.625 * t_c) / (243.04 + t_c))
    humidity = max(22.0, min(96.0, 100 * e1 / e2 + g() * 3))
    wind = max(0.2, min(11.0, 3.2 + 2.6 * g() + 1.6 * math.sin((2 * math.pi * d) / 7)))
    pressure = max(29.2, min(30.5, 29.9 + 0.13 * math.sin((2 * math.pi * d) / 9) + 0.05 * g()))
    return {"outsideF": outside_f, "dewF": dew_f, "humidity": humidity, "wind": wind, "pressure": pressure}


def _smooth(x: float) -> float:
    t = max(0.0, min(1.0, x))
    return t * t * (3 - 2 * t)


def _ts(d: int, slot: int) -> str:
    ms = START_MS + d * 86_400_000 + slot * 1_800_000
    import datetime as dt

    x = dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc)
    return f"{x.year:04d}-{x.month:02d}-{x.day:02d}T{x.hour:02d}:{x.minute:02d}:00"


def generate_demo_csv() -> str:
    rng = mulberry32(20260818)
    lines: list[str] = [",".join(HEADS)]

    gaps: set[tuple[str, int, int]] = set()
    for _ in range(8):
        u = UNITS[int(rng() * len(UNITS))]
        d = u["dStart"] + int(rng() * (u["dEnd"] - u["dStart"] - 2))
        slot = int(rng() * SLOTS_PER_DAY)
        length = 4 + int(rng() * 15)
        for k in range(length):
            gaps.add((u["id"], d, slot + k))

    MISSING = [
        ("Chilled Water Rate", 40), ("Cooling Water Temperature", 5), ("Building Load", 19),
        ("Chiller Energy Consumption", 9), ("Humidity", 20), ("Wind Speed", 23), ("Pressure", 12),
    ]
    missing_set: set[tuple[str, int, int, str]] = set()
    for col, count in MISSING:
        placed = 0
        guard = 0
        while placed < count and guard < count * 50:
            guard += 1
            u = UNITS[int(rng() * len(UNITS))]
            d = u["dStart"] + int(rng() * (u["dEnd"] - u["dStart"]))
            slot = int(rng() * SLOTS_PER_DAY)
            if (u["id"], d, slot) in gaps:
                continue
            missing_set.add((u["id"], d, slot, col))
            placed += 1

    def is_missing(u: str, d: int, slot: int, col: str) -> bool:
        return (u, d, slot) in gaps or (u, d, slot, col) in missing_set

    def fmt(v: float, dp: int = 2) -> str:
        return f"{v:.{dp}f}"

    def in_window(d: int, slot: int, d0: float, d1: float, s0: int = 0, s1: int = SLOTS_PER_DAY) -> bool:
        t = d + slot / SLOTS_PER_DAY
        return d0 <= t < d1 and s0 <= slot < s1

    c3_frozen_val: float | None = None
    rows: list[list[str]] = []

    for u in UNITS:
        for d in range(u["dStart"], u["dEnd"]):
            import datetime as dt

            dow = dt.datetime.fromtimestamp((START_MS + d * 86_400_000) / 1000, tz=dt.timezone.utc).weekday()
            weekend = 0.86 if dow >= 5 else 1.0
            seasonal = 0.58 + 0.42 * math.cos((2 * math.pi * d) / 365)
            day_eff = 1 + u["effJitter"] * gauss(rng)
            for slot in range(SLOTS_PER_DAY):
                if (u["id"], d, slot) in gaps:
                    continue
                t = d + slot / SLOTS_PER_DAY
                env = _env_at(rng, d, slot)
                hour = (slot / SLOTS_PER_DAY) * 24
                diurnal = 0.78 + 0.22 * math.sin((2 * math.pi * (hour - 9)) / 24)
                load_rt = max(1.0, 260 * seasonal * diurnal * weekend * (1 + gauss(rng) * 0.025))
                load_share = u["share"] * load_rt

                cwr = 0.0215 * load_share + 0.12 + gauss(rng) * 0.06
                cwt = 26.3 + 0.011 * load_share + 0.09 * (env["outsideF"] - 75) + gauss(rng) * 0.35
                eff = day_eff

                if u["id"] == "CHILLER-01":
                    if in_window(d, slot, 88, 91.6):
                        cwt += 4.5
                        eff *= 1.22
                        cwr *= 1.08
                    if in_window(d, slot, 60, 60.5):
                        eff *= 1.85
                if u["id"] == "CHILLER-02" and t >= 172:
                    eff *= 1 + 0.17 * _smooth((t - 172) / 90)
                if u["id"] == "CHILLER-03":
                    if in_window(d, slot, 72, 73.5):
                        cwr *= 0.6
                        eff *= 0.82
                        cwt += 1.8
                    if in_window(d, slot, 85, 85.5):
                        if c3_frozen_val is None:
                            c3_frozen_val = cwr
                        cwr = c3_frozen_val
                        eff *= 0.86
                        cwt += 1.5

                outside_f = env["outsideF"]
                kw = max(0.6, 3 + 0.62 * load_share * eff + 0.05 * max(0.0, outside_f - 75))
                kwh = max(0.25, kw * 0.5 + gauss(rng) * 0.15)

                def v(col: str, val: float, dp: int) -> str:
                    return "" if is_missing(u["id"], d, slot, col) else fmt(val, dp)

                rows.append([
                    _ts(d, slot), u["id"],
                    v("Chilled Water Rate", cwr, 2),
                    v("Cooling Water Temperature", cwt, 2),
                    v("Building Load", load_share, 1),
                    v("Chiller Energy Consumption", kwh, 2),
                    v("Outside Temperature", outside_f, 1),
                    v("Dew Point", env["dewF"], 1),
                    v("Humidity", env["humidity"], 1),
                    v("Wind Speed", env["wind"], 2),
                    v("Pressure", env["pressure"], 2),
                ])

    rows.sort(key=lambda r: (r[0], r[1]))
    lines.extend(",".join(r) for r in rows)
    return "\n".join(lines) + "\n"