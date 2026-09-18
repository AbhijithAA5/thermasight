"""ThermaSight — data contract types (YUKTHI 2026 Intelligent Energy & Equipment Monitoring)."""
from __future__ import annotations

from typing import Any

TIMESTAMP_COL = "timestamp"
EQUIPMENT_COL = "equipment_id"

NUMERIC_COLS = [
    "Chilled Water Rate",
    "Cooling Water Temperature",
    "Building Load",
    "Chiller Energy Consumption",
    "Outside Temperature",
    "Dew Point",
    "Humidity",
    "Wind Speed",
    "Pressure",
]

TARGET_COL = "Chiller Energy Consumption"

CONTEXT_COLS = [
    "Building Load",
    "Chilled Water Rate",
    "Cooling Water Temperature",
    "Outside Temperature",
    "Dew Point",
    "Humidity",
    "Wind Speed",
    "Pressure",
]

COLUMN_UNITS = {
    "Chilled Water Rate": "l/s",
    "Cooling Water Temperature": "\u00b0C",
    "Building Load": "RT",
    "Chiller Energy Consumption": "kWh",
    "Outside Temperature": "\u00b0F",
    "Dew Point": "\u00b0F",
    "Humidity": "%",
    "Wind Speed": "mph",
    "Pressure": "inHg",
}

SEVERITIES = ("normal", "watch", "alert", "action")
SEVERITY_RANK = {"normal": 0, "watch": 1, "alert": 2, "action": 3}


class Series:
    """One equipment unit's chronological series (sorted, deduped by timestamp)."""

    __slots__ = ("equipment_id", "times", "hours", "days", "data", "observed")

    def __init__(self, equipment_id: str) -> None:
        self.equipment_id = equipment_id
        self.times: list[int] = []
        self.hours: list[float] = []
        self.days: list[int] = []
        self.data: dict[str, list[float]] = {c: [] for c in NUMERIC_COLS}
        self.observed: dict[str, list[int]] = {c: [] for c in NUMERIC_COLS}

    def __len__(self) -> int:
        return len(self.times)


def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def to_json_ready(obj: Any) -> Any:
    """Recursively convert numpy types to plain JSON types."""
    import builtins

    if isinstance(obj, (builtins.int, builtins.float, builtins.str, builtins.bool)) or obj is None:
        return obj
    if isinstance(obj, (list, tuple)):
        return [to_json_ready(x) for x in obj]
    if isinstance(obj, dict):
        return {k: to_json_ready(v) for k, v in obj.items()}
    if hasattr(obj, "item"):  # numpy scalar
        return obj.item()
    if hasattr(obj, "tolist"):  # numpy array
        return to_json_ready(obj.tolist())
    if hasattr(obj, "__dict__"):
        return to_json_ready(vars(obj))
    return str(obj)