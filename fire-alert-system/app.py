"""
app.py — Local Fire-Alert Monitoring Flask Application
======================================================
Serves the local web dashboard, exposes REST & SSE streaming APIs,
and coordinates the background serial reader / mock replay thread,
SQLite persistence, and Twilio SMS dispatch.

Run modes:
    python app.py            # Connects to real ESP32 on config.SERIAL_PORT
    python app.py --mock     # Replays canned mock_data.txt without ESP32
"""

import argparse
import json
import logging
import queue
import sys
import threading
import time
from typing import Set

from flask import Flask, Response, jsonify, render_template, request

import config
import database
from serial_reader import MockSerialReader, SerialReader
from sms_alert import send_fire_sms

# ---------------------------------------------------------------------------
# Logging Setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("FireAlertApp")

# ---------------------------------------------------------------------------
# Flask App Initialisation
# ---------------------------------------------------------------------------
app = Flask(__name__)

# Shared queue between Serial/Mock reader and consumer
event_queue: queue.Queue = queue.Queue(maxsize=1000)

# Set of active SSE client queues for real-time pushing
sse_clients: Set[queue.Queue] = set()
sse_lock = threading.Lock()

# System state cache
system_state = {
    "phase": "initializing",        # "initializing" | "warmup" | "calibrating" | "monitoring"
    "phase_details": "",
    "seconds_remaining": None,
    "mode": "hardware",             # "hardware" or "mock"
    "latest_reading": None,
    "calibration": None,
    "last_event_id": None,
}


def broadcast_sse(event_data: dict):
    """Push an event dict to all connected SSE clients."""
    payload = f"data: {json.dumps(event_data)}\n\n"
    with sse_lock:
        dead_clients = []
        for q in sse_clients:
            try:
                q.put_nowait(payload)
            except queue.Full:
                dead_clients.append(q)
        for dead in dead_clients:
            sse_clients.discard(dead)


# ---------------------------------------------------------------------------
# Background Consumer Thread
# ---------------------------------------------------------------------------

def event_consumer_worker():
    """
    Consumes events from event_queue, saves to database, executes SMS alerts,
    updates system state, and broadcasts real-time SSE updates to dashboard.
    """
    log.info("Event consumer worker started.")

    while True:
        try:
            event = event_queue.get(timeout=1.0)
        except queue.Empty:
            continue

        try:
            etype = event.get("type")

            # 1. Warm-up Phase
            if etype == "warmup":
                system_state["phase"] = "warmup"
                secs = event.get("seconds_remaining")
                system_state["seconds_remaining"] = secs
                system_state["phase_details"] = f"MQ-2 heater stabilizing ({secs}s remaining)"
                broadcast_sse({
                    "type": "phase_update",
                    "phase": "warmup",
                    "details": system_state["phase_details"],
                    "seconds_remaining": secs,
                    "timestamp": event.get("timestamp"),
                })

            # 2. Calibration Progress or Completion
            elif etype == "calibrating":
                system_state["phase"] = "calibrating"
                secs = event.get("seconds_remaining", 0)
                system_state["seconds_remaining"] = secs
                if event.get("complete"):
                    system_state["phase_details"] = "Baseline calibration complete!"
                else:
                    system_state["phase_details"] = f"Establishing baseline ({secs}s remaining)"

                broadcast_sse({
                    "type": "phase_update",
                    "phase": "calibrating",
                    "details": system_state["phase_details"],
                    "seconds_remaining": secs,
                    "complete": event.get("complete", False),
                    "timestamp": event.get("timestamp"),
                })

            # 3. Calibration Metrics line
            elif etype == "calibration_value":
                subtype = event.get("sub_type")
                if subtype == "baseline_mq2":
                    database.upsert_calibration(baseline_mq2=event.get("value"))
                elif subtype == "stddev":
                    database.upsert_calibration(stddev=event.get("value"))
                elif subtype == "threshold":
                    database.upsert_calibration(threshold_delta=event.get("threshold_delta"))
                elif subtype == "baseline_temp":
                    database.upsert_calibration(baseline_temp=event.get("value"))

                system_state["calibration"] = database.fetch_calibration()
                broadcast_sse({
                    "type": "calibration_update",
                    "calibration": system_state["calibration"],
                    "timestamp": event.get("timestamp"),
                })

            # 4. Sensor Reading Line
            elif etype == "reading":
                system_state["phase"] = "monitoring"
                system_state["phase_details"] = "Active monitoring"
                system_state["seconds_remaining"] = None

                # For FIRE predictions, peek briefly to check if immediate suppression note follows
                if event.get("prediction") == "FIRE":
                    time.sleep(0.15)
                    # Check if next item in queue is safety_suppressed_update
                    if not event_queue.empty():
                        try:
                            next_item = event_queue.queue[0]
                            if next_item.get("type") == "safety_suppressed_update":
                                event["safety_suppressed"] = True
                        except Exception:
                            pass

                # Persist to SQLite
                row_id = database.insert_event(event)
                event["id"] = row_id
                system_state["last_event_id"] = row_id
                system_state["latest_reading"] = event

                # Evaluate SMS alert (handles debounce & suppression check internally)
                try:
                    send_fire_sms(event)
                except Exception as sms_err:
                    log.exception(f"Error evaluating SMS alert: {sms_err}")

                # Broadcast live reading to connected browsers
                broadcast_sse({
                    "type": "new_reading",
                    "event": event,
                })

            # 5. Safety Suppression Note (patches previous event)
            elif etype == "safety_suppressed_update":
                database.update_safety_suppressed()
                if system_state["latest_reading"]:
                    system_state["latest_reading"]["safety_suppressed"] = True

                broadcast_sse({
                    "type": "safety_suppressed_update",
                    "raw": event.get("raw"),
                    "timestamp": event.get("timestamp"),
                })

        except Exception as exc:
            log.exception(f"Error processing event in consumer worker: {exc}")
        finally:
            event_queue.task_done()


# ---------------------------------------------------------------------------
# Web Routes & API
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """Render the main responsive web dashboard."""
    return render_template("dashboard.html")


@app.route("/api/status")
def get_status():
    """
    Returns latest reading, system phase, and current calibration snapshot.
    """
    cal = system_state["calibration"] or database.fetch_calibration()
    latest = system_state["latest_reading"] or database.fetch_latest_event()

    return jsonify({
        "status": "ok",
        "phase": system_state["phase"],
        "phase_details": system_state["phase_details"],
        "seconds_remaining": system_state["seconds_remaining"],
        "mode": system_state["mode"],
        "latest_reading": latest,
        "calibration": cal,
    })


@app.route("/api/events")
def get_events():
    """
    Returns recent events, newest first.
    Query params: ?limit=100 (default 100, max 500)
    """
    limit_arg = request.args.get("limit", default=100, type=int)
    limit = max(1, min(limit_arg, 500))
    events = database.fetch_recent_events(limit=limit)
    return jsonify({
        "count": len(events),
        "events": events,
    })


@app.route("/api/stream")
def sse_stream():
    """
    Server-Sent Events endpoint for real-time live browser updates.
    """
    def event_stream():
        client_queue = queue.Queue(maxsize=100)
        with sse_lock:
            sse_clients.add(client_queue)

        # Send initial status as first frame
        initial_status = {
            "type": "initial_state",
            "phase": system_state["phase"],
            "phase_details": system_state["phase_details"],
            "mode": system_state["mode"],
            "calibration": system_state["calibration"] or database.fetch_calibration(),
            "latest_reading": system_state["latest_reading"] or database.fetch_latest_event(),
        }
        yield f"data: {json.dumps(initial_status)}\n\n"

        try:
            while True:
                msg = client_queue.get()
                yield msg
        except GeneratorExit:
            with sse_lock:
                sse_clients.discard(client_queue)

    return Response(
        event_stream(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.route("/api/test-sms", methods=["POST"])
def trigger_test_sms():
    """
    Convenience endpoint to test Twilio SMS configuration directly from the UI.
    """
    fake_event = {
        "type": "reading",
        "temp": 34.8,
        "humidity": 55.0,
        "mq2": 480.0,
        "delta": 198.0,
        "confidence": 100.0,
        "prediction": "FIRE",
        "safety_suppressed": False,
    }
    try:
        send_fire_sms(fake_event)
        return jsonify({"status": "ok", "message": "Test fire SMS triggered"})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


# ---------------------------------------------------------------------------
# Application Entry Point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Forest Fire Alert Monitoring System")
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Run in mock replay mode using mock_data.txt without physical ESP32",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=config.FLASK_PORT,
        help=f"Port to run Flask web server (default: {config.FLASK_PORT})",
    )
    parser.add_argument(
        "--serial-port",
        type=str,
        default=config.SERIAL_PORT,
        help=f"Serial port for ESP32 (default: {config.SERIAL_PORT})",
    )
    args = parser.parse_args()

    # 1. Initialize SQLite Database
    database.init_db()
    system_state["calibration"] = database.fetch_calibration()
    system_state["latest_reading"] = database.fetch_latest_event()
    if system_state["latest_reading"]:
        system_state["phase"] = "monitoring"
        system_state["phase_details"] = "System operational (from stored events)"

    # 2. Start Background Consumer Thread
    consumer_thread = threading.Thread(
        target=event_consumer_worker,
        name="EventConsumerThread",
        daemon=True,
    )
    consumer_thread.start()

    # 3. Start Serial Reader or Mock Reader
    if args.mock:
        system_state["mode"] = "mock"
        log.info(">>> RUNNING IN MOCK / DEMO MODE (replaying mock_data.txt) <<<")
        reader = MockSerialReader(
            mock_file=config.MOCK_REPLAY_FILE,
            event_queue=event_queue,
            delay=config.MOCK_LINE_DELAY_SECONDS,
            loop_forever=True,
        )
    else:
        system_state["mode"] = "hardware"
        log.info(f">>> RUNNING IN HARDWARE SERIAL MODE ({args.serial_port} @ {config.BAUD_RATE} baud) <<<")
        reader = SerialReader(
            port=args.serial_port,
            baud_rate=config.BAUD_RATE,
            event_queue=event_queue,
        )

    reader.start()

    # 4. Start Flask HTTP Server
    log.info(f"Dashboard available at http://localhost:{args.port}")
    app.run(
        host=config.FLASK_HOST,
        port=args.port,
        debug=config.FLASK_DEBUG,
        use_reloader=False,  # Important: Avoid duplicate background threads
        threaded=True,
    )


if __name__ == "__main__":
    main()
