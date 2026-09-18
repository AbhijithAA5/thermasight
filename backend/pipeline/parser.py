"""ThermaSight — ingestion. Parses any CSV conforming to the YUKTHI 2026 data
contract: columns identified by name (case/whitespace/unit-annotation
tolerant), record identity is (equipment_id, timestamp), no hard-coded rows or
timestamps.

Robustness contract (per the challenge documents): `timestamp`, `equipment_id`
and `Chiller Energy Consumption` are required (the third is the analysis
target). Every other measurement column is OPTIONAL — a missing column is
treated as entirely-missing data, reported in the quality report, and excluded
from modeling instead of failing the upload.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from .types import EQUIPMENT_COL, NUMERIC_COLS, TARGET_COL, TIMESTAMP_COL, Series


class DatasetError(Exception):
    pass


def _norm(s: str) -> str:
    s = re.sub(r"\(.*?\)", "", s)  # strip unit annotations e.g. (L/sec), (C), (F)
    return re.sub(r"[\s_-]+", "", s.strip().lower())


def _parse_ts(v: str):
    """Parse a timestamp literal as written (hour/dow from the literal fields,
    so diurnal patterns are not shifted by timezone conversion)."""
    s = v.strip()
    if not s:
        return None
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})[T ](\d{1,2}):(\d{2})(?::(\d{2}))?(?:\.(\d{1,3}))?", s)
    if not m:
        return None
    Y, Mo, D, H, Mi = (int(x) for x in m.group(1, 2, 3, 4, 5))
    Se = int(m.group(6) or 0)
    try:
        dt = datetime(Y, Mo, D, H, Mi, Se, tzinfo=timezone.utc)
    except ValueError:
        return None
    if m.group(7):
        H += float(m.group(7)) / 1000.0 / 3600.0
    return {
        "ms": int(dt.timestamp() * 1000),
        "hour": H + Mi / 60.0,
        "dow": dt.weekday(),
    }


def _split_csv(text: str):
    """Minimal RFC-4180-ish splitter: quoted fields, escaped quotes, CRLF/LF."""
    rows: list[list[str]] = []
    row: list[str] = []
    cur = ""
    in_q = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if in_q:
            if ch == '"':
                if i + 1 < n and text[i + 1] == '"':
                    cur += '"'
                    i += 1
                else:
                    in_q = False
            else:
                cur += ch
        elif ch == '"':
            in_q = True
        elif ch == ",":
            row.append(cur)
            cur = ""
        elif ch == "\n":
            row.append(cur)
            cur = ""
            rows.append(row)
            row = []
        elif ch == "\r":
            pass
        else:
            cur += ch
        i += 1
    if cur or row:
        row.append(cur)
        rows.append(row)
    while rows and all(c.strip() == "" for c in rows[-1]):
        rows.pop()
    return rows


def _map_columns(headers: list[str]) -> tuple[dict[str, int], list[str]]:
    """Map headers to the contract and report which measurement columns are
    entirely absent (they degrade to all-missing instead of failing)."""
    idx = [_norm(h) for h in headers]

    def head_names() -> str:
        return ", ".join(headers) if headers else "(none — the file appears to have no header row)"

    mapping: dict[str, int] = {}
    for w in [TIMESTAMP_COL, EQUIPMENT_COL] + NUMERIC_COLS:
        key = _norm(w)
        if key in idx:
            mapping[w] = idx.index(key)

    required_missing = [w for w in (TIMESTAMP_COL, EQUIPMENT_COL, TARGET_COL) if w not in mapping]
    if required_missing:
        raise DatasetError(
            "Missing required column(s): "
            + ", ".join(required_missing)
            + f". Found columns in your file: {head_names()}. "
            "Per the YUKTHI 2026 data specification, timestamp, equipment_id "
            "and a 'Chiller Energy Consumption' column are required; the other "
            "measurement columns may be provided as available, but blank "
            "values are also accepted."
        )
    missing_measurements = [w for w in NUMERIC_COLS if w not in mapping]
    return mapping, missing_measurements


def build_dataset(text: str, file_name: str) -> dict:
    rows = _split_csv(text)
    if not rows:
        raise DatasetError("The file appears to be empty. Upload a CSV with a header row and at least one observation.")
    if len(rows) == 1:
        raise DatasetError("The file has a header row but no observations.")
    headers = [h.strip() for h in rows[0]]
    body = rows[1:]
    col_idx, missing_measurements = _map_columns(headers)
    t_i = col_idx[TIMESTAMP_COL]
    e_i = col_idx[EQUIPMENT_COL]

    groups: dict[str, Series] = {}
    seen: set[tuple[str, int]] = set()
    bad_rows = 0
    duplicate_pairs = 0

    for r in body:
        parsed = _parse_ts(r[t_i] if t_i < len(r) else "")
        if parsed is None:
            bad_rows += 1
            continue
        eq = (r[e_i] if e_i < len(r) else "").strip()
        if not eq:
            bad_rows += 1
            continue
        key = (eq, parsed["ms"])
        if key in seen:
            duplicate_pairs += 1
            continue
        seen.add(key)
        g = groups.get(eq)
        if g is None:
            g = Series(eq)
            groups[eq] = g
        g.times.append(parsed["ms"])
        g.hours.append(parsed["hour"])
        g.days.append(parsed["dow"])
        for c in NUMERIC_COLS:
            ci = col_idx.get(c)
            raw = (r[ci] if ci is not None and ci < len(r) else "").strip()
            if raw == "":
                g.data[c].append(float("nan"))
                g.observed[c].append(0)
            else:
                try:
                    g.data[c].append(float(raw))
                    g.observed[c].append(1)
                except ValueError:
                    g.data[c].append(float("nan"))
                    g.observed[c].append(0)

    if not groups:
        raise DatasetError(
            "No valid observations found in the file: every row failed timestamp "
            "or equipment_id parsing. Timestamps must look like '2019-08-18 00:00:00'."
            if bad_rows
            else "The file does not contain any (equipment_id, timestamp) rows."
        )

    # Sort each unit chronologically and dedupe identical timestamps.
    for g in groups.values():
        order = sorted(range(len(g.times)), key=lambda k: g.times[k])
        g.times = [g.times[k] for k in order]
        g.hours = [g.hours[k] for k in order]
        g.days = [g.days[k] for k in order]
        for c in NUMERIC_COLS:
            g.data[c] = [g.data[c][k] for k in order]
            g.observed[c] = [g.observed[c][k] for k in order]

    equipment_ids = sorted(groups.keys())
    total_rows = sum(len(groups[e]) for e in equipment_ids)

    missing_by_column = {}
    for c in NUMERIC_COLS:
        if c in missing_measurements:
            continue  # absent columns are reported separately in missingColumns
        miss = sum(1 for e in equipment_ids for o in groups[e].observed[c] if not o)
        if miss:
            missing_by_column[c] = miss

    all_intervals: list[int] = []
    for e in equipment_ids:
        t = groups[e].times
        all_intervals.extend(t[i] - t[i - 1] for i in range(1, len(t)))
    all_intervals.sort()
    med = all_intervals[len(all_intervals) // 2] if all_intervals else 1_800_000

    gap_count = 0
    max_gap_hours = 0.0
    for e in equipment_ids:
        t = groups[e].times
        for i in range(1, len(t)):
            d = t[i] - t[i - 1]
            if d > max(med * 1.5, 3_600_000):
                gap_count += 1
                max_gap_hours = max(max_gap_hours, d / 3_600_000.0)

    period_start = min(groups[e].times[0] for e in equipment_ids)
    period_end = max(groups[e].times[-1] for e in equipment_ids)

    quality = {
        "fileName": file_name,
        "rowCount": total_rows,
        "equipmentCount": len(equipment_ids),
        "equipmentIds": equipment_ids,
        "periodStart": period_start,
        "periodEnd": period_end,
        "missingByColumn": missing_by_column,
        "missingColumns": missing_measurements,  # entirely absent measurement columns
        "duplicatePairs": duplicate_pairs,
        "gapCount": gap_count,
        "maxGapHours": round(max_gap_hours * 10) / 10,
        "columnsFound": headers,
        "badRows": bad_rows,
    }
    return {"quality": quality, "series": groups}