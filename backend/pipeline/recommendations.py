"""ThermaSight — evidence-based operational recommendations."""

from __future__ import annotations

PATTERN_RECS: dict[str, list[dict]] = {
    "high_consumption_low_load": [
        {"action": "Investigate energy use at constant load",
         "rationale": ("Energy rose sharply while building load stayed normal. Check compressor staging, "
                       "refrigerant charge, and whether a second compressor is running unnecessarily."),
         "priority": "high"},
        {"action": "Verify energy meter / transducer calibration",
         "rationale": ("A load-consistent energy jump can also come from the metering path itself. Compare "
                       "against the unit's own panel meter before scheduling mechanical work."),
         "priority": "medium"},
    ],
    "cooling_water_drift": [
        {"action": "Inspect condenser water loop",
         "rationale": ("Cooling water temperature drifted high over hours while demand was stable. Check "
                       "cooling tower fans, condenser fouling, and water flow/valve position."),
         "priority": "high"},
        {"action": "Check cooling tower setpoint and bypass",
         "rationale": "Sustained condenser temperature rise changes chiller lift and efficiency even when load is unchanged.",
         "priority": "medium"},
    ],
    "flow_imbalance": [
        {"action": "Check chilled-water pump and bypass valves",
         "rationale": ("Chilled-water flow deviated while load was normal, which points to the hydronic side "
                       "(pump speed, bypass, or a closed valve) rather than the compressor."),
         "priority": "high"},
        {"action": "Verify flow meter reading",
         "rationale": "Confirm the flow signal against the plant BMS trend before mechanical intervention.",
         "priority": "low"},
    ],
    "sensor_stuck": [
        {"action": "Replace or recalibrate the frozen channel",
         "rationale": ("A measurement held constant for hours while related channels vary is a sensor or "
                       "transmission signature, not plant behaviour."),
         "priority": "high"},
        {"action": "Check wiring and data path for that channel",
         "rationale": "Stuck values often originate at the transmitter or the gateway, not the equipment.",
         "priority": "medium"},
    ],
    "transient_spike": [
        {"action": "Log the event and watch for recurrence",
         "rationale": ("A one- or two-interval spike with no context shift is likely a transient (start-up "
                       "transient, momentary measurement glitch). No action unless it repeats."),
         "priority": "low"},
    ],
    "degradation_trend": [
        {"action": "Schedule a performance test (kW per RT)",
         "rationale": ("The residual is drifting upward over months at similar load and ambient conditions, "
                       "consistent with progressive efficiency loss (fouling, charge loss, bearing wear)."),
         "priority": "high"},
        {"action": "Plan condenser / evaporator cleaning window",
         "rationale": "Heat-exchanger fouling is the most common recoverable cause of slow efficiency drift.",
         "priority": "medium"},
    ],
    "offhours_standby": [
        {"action": "Review off-hours scheduling and stand-by loads",
         "rationale": ("Consumption is elevated outside occupied hours while load is low. Check pump run-times, "
                       "VFD minimum speeds, and stand-by heaters."),
         "priority": "medium"},
    ],
    "concurrent_context_shift": [
        {"action": "Check operating setpoints and mode changes",
         "rationale": ("Multiple channels moved together in a coordinated way, which is typical of a "
                       "mode/setpoint change or a control strategy shift rather than a single component fault."),
         "priority": "medium"},
    ],
    "contextual_energy_spike": [
        {"action": "Compare with start-up and demand events in the logs",
         "rationale": ("Energy departed from its context baseline without a clear supporting shift in load or "
                       "ambient conditions. Correlate the window with maintenance and start/stop logs."),
         "priority": "medium"},
    ],
}

PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def recommendations_for_patterns(tags: list[str], severity: str) -> list[dict]:
    out: list[dict] = []
    for t in tags:
        out.extend(PATTERN_RECS.get(t, []))
    if severity == "action":
        out.append({
            "action": "Escalate to maintenance review with this evidence window",
            "rationale": ("Severity is at the highest level; attach the evidence timestamps and contributing "
                          "factors to the work order."),
            "priority": "high",
        })
    if not out:
        out.append({
            "action": "Review the flagged window in the equipment explorer",
            "rationale": ("The deviation is real but does not cleanly match a known pattern; manual review of "
                          "context is the right next step."),
            "priority": "medium",
        })
    seen = set()
    capped: list[dict] = []
    for r in out:
        if r["action"] in seen:
            continue
        seen.add(r["action"])
        capped.append(r)
    capped.sort(key=lambda r: PRIORITY_ORDER.get(r["priority"], 2))
    return capped[:4]