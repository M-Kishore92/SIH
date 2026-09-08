"""
run_terminal_simulation.py — 45-Second Real-Time Forest Fire Terminal Stream
=============================================================================
Simulates rolling serial input from the ESP32 directly in your terminal,
parsing each frame, tracking temperature & gas rates, and demonstrating
fire detection and SMS alert debounce behavior.
"""

import sys
import time
from datetime import datetime

import parser
import sms_alert
import database

# Ensure UTF-8 output in Windows PowerShell / cmd
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ANSI Color Codes for Rich Terminal Output
COLOR_RESET = "\033[0m"
COLOR_RED = "\033[91m"
COLOR_GREEN = "\033[92m"
COLOR_YELLOW = "\033[93m"
COLOR_BLUE = "\033[94m"
COLOR_PURPLE = "\033[95m"
COLOR_CYAN = "\033[96m"
COLOR_BOLD = "\033[1m"
COLOR_DIM = "\033[2m"

DATA_FILE = "synthetic_forest_fire_45s.txt"

def main():
    print(f"{COLOR_CYAN}{COLOR_BOLD}{'='*80}{COLOR_RESET}")
    print(f"{COLOR_CYAN}{COLOR_BOLD}  PYROWATCH — 45-SECOND ROLLING FOREST FIRE TELEMETRY SIMULATOR{COLOR_RESET}")
    print(f"{COLOR_CYAN}{COLOR_BOLD}{'='*80}{COLOR_RESET}")
    print(f"{COLOR_DIM}Replaying synthetic sensor frames at 1 frame / second (Press Ctrl+C to stop)...{COLOR_RESET}\n")

    # Initialise Database for live record persistence
    database.init_db()

    # Track alerter state
    alerter = sms_alert.SMSAlerter()

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip()]
    except FileNotFoundError:
        print(f"{COLOR_RED}Error: {DATA_FILE} not found.{COLOR_RESET}")
        return

    total_frames = len(lines)
    print(f"{COLOR_BLUE}Loaded {total_frames} frames ({total_frames} seconds duration). Starting telemetry stream:{COLOR_RESET}\n")
    print(f"{'SEC':<4} | {'TIME':<8} | {'PREDICTION':<10} | {'CONF':<5} | {'TEMP':<7} | {'MQ2':<5} | {'TEMP_RATE':<10} | {'MQ2_RATE':<10} | {'STATUS'}")
    print(f"{'-'*4}-+-{'-'*8}-+-{'-'*10}-+-{'-'*5}-+-{'-'*7}-+-{'-'*5}-+-{'-'*10}-+-{'-'*10}-+-{'-'*20}")

    for idx, raw_line in enumerate(lines, start=1):
        parsed = parser.parse_line(raw_line)
        now_str = datetime.now().strftime("%H:%M:%S")

        if not parsed:
            continue

        pred = parsed.get("prediction", "UNKNOWN")
        conf = parsed.get("confidence", 0.0)
        temp = parsed.get("temp", 0.0)
        mq2 = parsed.get("mq2", 0.0)
        temp_rate = parsed.get("temp_rate", 0.0)
        mq2_rate = parsed.get("mq2_rate", 0.0)

        # Evaluate SMS alert logic
        is_fire = (pred == "FIRE")
        if is_fire:
            status_text = f"{COLOR_RED}{COLOR_BOLD}🔥 FIRE ALARM{COLOR_RESET}"
            pred_color = f"{COLOR_RED}{COLOR_BOLD}{pred:<10}{COLOR_RESET}"
        else:
            status_text = f"{COLOR_GREEN}✓ NOMINAL{COLOR_RESET}"
            pred_color = f"{COLOR_GREEN}{pred:<10}{COLOR_RESET}"

        # Insert into database
        database.insert_event(parsed)

        # Trigger alerter (prints debounce message if Twilio not set)
        alerter.handle_event(parsed)

        # Print formatted rolling row
        print(
            f"{idx:02d}s  | {now_str} | {pred_color} | {conf:3.0f}% | {temp:4.1f}°C  | {mq2:4.0f} | "
            f"{temp_rate:+5.2f}°C/s | {mq2_rate:+6.2f}/s | {status_text}"
        )

        time.sleep(1.0)

    print(f"\n{COLOR_CYAN}{COLOR_BOLD}{'='*80}{COLOR_RESET}")
    print(f"{COLOR_GREEN}{COLOR_BOLD}✔ 45-Second Simulation Completed Successfully!{COLOR_RESET}")
    print(f"{COLOR_DIM}All frames parsed, processed, and recorded into SQLite database (fire_alerts.db).{COLOR_RESET}")
    print(f"{COLOR_CYAN}{COLOR_BOLD}{'='*80}{COLOR_RESET}\n")

if __name__ == "__main__":
    main()
