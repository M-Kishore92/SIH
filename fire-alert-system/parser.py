"""
parser.py — ESP32 Serial Line Parser
=====================================
Handles all 4 message types emitted by the forest-fire detection firmware
and returns structured dicts. Returns None for unrecognised lines so the
caller can safely ignore them.

Message types supported
------------------------
1. Warm-up         → "[Warm-up] MQ-2 stabilizing heater... 60s remaining"
2. Calibration     → "[Calibrating] ..." variants + baseline summary lines
3. Normal reading  → "Temp=…C | Hum=…% | MQ2=… | … | Prediction=… | Confidence=…%"
4. Safety suppress → "  └─ [SAFETY] Model flagged FIRE but MQ2 is within …"
"""

import re
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Compiled regex patterns (compiled once at import time for performance)
# ---------------------------------------------------------------------------

# Pattern 1 — Warm-up countdown
_RE_WARMUP = re.compile(
    r"\[Warm-up\].*?(\d+)s remaining",
    re.IGNORECASE,
)

# Pattern 2a — Generic calibrating progress line
_RE_CALIBRATING_PROGRESS = re.compile(
    r"\[Calibrating\].*?(\d+)s remaining",
    re.IGNORECASE,
)

# Pattern 2b — Calibration complete banner
_RE_CALIBRATING_DONE = re.compile(
    r"\[Calibrating\].*?complete",
    re.IGNORECASE,
)

# Pattern 2c — Baseline MQ-2 value line
_RE_BASELINE_MQ2 = re.compile(
    r"Baseline MQ-2\s*:\s*([\d.]+)",
    re.IGNORECASE,
)

# Pattern 2d — MQ-2 standard deviation (handles Unicode sigma σ or ASCII)
_RE_STDDEV = re.compile(
    r"MQ-2 StdDev.*:\s*([\d.]+)",
    re.IGNORECASE,
)


# Pattern 2e — Fire threshold delta  (e.g. "+92.5 (triggers when MQ2 >= 374.9)")
_RE_THRESHOLD = re.compile(
    r"Fire Threshold.*?([+-]?[\d.]+)\s*\(triggers when MQ2\s*>=\s*([\d.]+)\)",
    re.IGNORECASE,
)

# Pattern 2f — Baseline temperature
_RE_BASELINE_TEMP = re.compile(
    r"Baseline Temp\s*:\s*([\d.]+)\s*C",
    re.IGNORECASE,
)

# Pattern 3 — Main monitoring line
# Example:
#   Temp=33.2C | Hum=58.0% | MQ2=295 (Baseline=282, Delta=+13) |
#   TempRate=+0.00 | MQ2Rate=+1.79 | Prediction=NOT FIRE | Confidence=100%
_RE_READING = re.compile(
    r"Temp=([+-]?[\d.]+)C"
    r"\s*\|\s*Hum=([+-]?[\d.]+)%"
    r"\s*\|\s*MQ2=([\d.]+)(?:\s*\(Baseline=([\d.]+),\s*Delta=([+-]?[\d.]+)\))?"
    r"\s*\|\s*TempRate=([+-]?[\d.]+)"
    r"\s*\|\s*MQ2Rate=([+-]?[\d.]+)"
    r"\s*\|\s*Prediction=(FIRE|NOT FIRE)"
    r"\s*\|\s*Confidence=([\d.]+)%",
    re.IGNORECASE,
)


# Pattern 4 — Safety-suppression note (follows immediately after a FIRE line)
_RE_SAFETY = re.compile(
    r"└─\s*\[SAFETY\]",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Public parse function
# ---------------------------------------------------------------------------

def parse_line(raw_line: str) -> dict | None:
    """
    Parse a single raw serial line from the ESP32.

    Returns a dict with at minimum:
        {
          "type": <str>,          # "warmup" | "calibrating" | "calibration_value"
                                  #  | "reading" | "safety_suppression" | "unknown"
          "timestamp": <str>,     # ISO-8601 UTC
          "raw": <str>,           # original line (stripped)
        }

    For "reading" events additional keys are present:
        temp, humidity, mq2, baseline, delta, temp_rate, mq2_rate,
        prediction, confidence, safety_suppressed (initially False;
        set to True by the caller if the next line is a safety-suppression note).

    For "calibration_value" events:
        sub_type  → "baseline_mq2" | "stddev" | "threshold" | "baseline_temp"
        value(s)  → depending on sub_type

    Returns None for completely unrecognised lines (e.g. empty lines, debug noise).
    """

    line = raw_line.strip()
    if not line:
        return None

    now = datetime.now(timezone.utc).isoformat()

    # ---- Warm-up -------------------------------------------------------
    m = _RE_WARMUP.search(line)
    if m:
        return {
            "type": "warmup",
            "seconds_remaining": int(m.group(1)),
            "timestamp": now,
            "raw": line,
        }

    # ---- Calibrating progress ------------------------------------------
    m = _RE_CALIBRATING_PROGRESS.search(line)
    if m:
        return {
            "type": "calibrating",
            "seconds_remaining": int(m.group(1)),
            "timestamp": now,
            "raw": line,
        }

    # ---- Calibration complete ------------------------------------------
    if _RE_CALIBRATING_DONE.search(line):
        return {
            "type": "calibrating",
            "seconds_remaining": 0,
            "complete": True,
            "timestamp": now,
            "raw": line,
        }

    # ---- Baseline MQ-2 value line -------------------------------------
    m = _RE_BASELINE_MQ2.search(line)
    if m:
        return {
            "type": "calibration_value",
            "sub_type": "baseline_mq2",
            "value": float(m.group(1)),
            "timestamp": now,
            "raw": line,
        }

    # ---- StdDev --------------------------------------------------------
    m = _RE_STDDEV.search(line)
    if m:
        return {
            "type": "calibration_value",
            "sub_type": "stddev",
            "value": float(m.group(1)),
            "timestamp": now,
            "raw": line,
        }

    # ---- Fire threshold -----------------------------------------------
    m = _RE_THRESHOLD.search(line)
    if m:
        return {
            "type": "calibration_value",
            "sub_type": "threshold",
            "threshold_delta": float(m.group(1)),
            "trigger_level": float(m.group(2)),
            "timestamp": now,
            "raw": line,
        }

    # ---- Baseline temperature -----------------------------------------
    m = _RE_BASELINE_TEMP.search(line)
    if m:
        return {
            "type": "calibration_value",
            "sub_type": "baseline_temp",
            "value": float(m.group(1)),
            "timestamp": now,
            "raw": line,
        }

    # ---- Safety suppression note --------------------------------------
    if _RE_SAFETY.search(line):
        return {
            "type": "safety_suppression",
            "timestamp": now,
            "raw": line,
        }

    # ---- Normal sensor reading ----------------------------------------
    m = _RE_READING.search(line)
    if m:
        base_val = float(m.group(4)) if m.group(4) is not None else 0.0
        delta_val = float(m.group(5)) if m.group(5) is not None else 0.0
        return {
            "type": "reading",
            "timestamp": now,
            "raw": line,
            "temp": float(m.group(1)),
            "humidity": float(m.group(2)),
            "mq2": float(m.group(3)),
            "baseline": base_val,
            "delta": delta_val,
            "temp_rate": float(m.group(6)),
            "mq2_rate": float(m.group(7)),
            "prediction": m.group(8).upper(),          # "FIRE" or "NOT FIRE"
            "confidence": float(m.group(9)),
            "safety_suppressed": False,                 # may be flipped by caller
        }

    # ---- Unrecognised -------------------------------------------------
    # Return None so the caller can safely skip logging this to the DB
    return None


# ---------------------------------------------------------------------------
# Quick self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    samples = [
        "[Warm-up] MQ-2 stabilizing heater... 60s remaining",
        "[Calibrating] Establishing baseline... 110s remaining",
        "[Calibrating] Baseline calibration complete!",
        "   Baseline MQ-2    : 282.4",
        "   MQ-2 StdDev (σ)  : 18.50",
        "   Fire Threshold Δ : +92.5 (triggers when MQ2 >= 374.9)",
        "   Baseline Temp    : 32.8 C",
        "Temp=33.2C | Hum=58.0% | MQ2=295 (Baseline=282, Delta=+13) | TempRate=+0.00 | MQ2Rate=+1.79 | Prediction=NOT FIRE | Confidence=100%",
        "  └─ [SAFETY] Model flagged FIRE but MQ2 is within calibrated baseline - suppressed as likely false positive",
        "Temp=34.3C | Hum=56.0% | MQ2=470 (Baseline=282, Delta=+188) | TempRate=+0.10 | MQ2Rate=+14.84 | Prediction=FIRE | Confidence=100%",
        "garbage line that should return None",
    ]
    for s in samples:
        result = parse_line(s)
        clean_s = s.encode("ascii", "replace").decode("ascii")
        print(f"INPUT : {clean_s[:80]}")
        print(f"OUTPUT: {result}")
        print()

