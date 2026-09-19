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
from datetime import datetime, timezone
from typing import Set

from flask import Flask, Response, jsonify, render_template, request

import config
import database
from serial_reader import MockSerialReader, SerialReader
from sms_alert import send_fire_sms, send_landslide_sms

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

# Landslide monitoring state cache
landslide_state = {
    "latest_reading": None,
    "last_event_id": None,
    "node_id": None,
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
                    "type": "fire",
                    "event": event,
                    "data": event,
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

            # 6. Landslide Telemetry Reading
            elif etype == "landslide_reading":
                row_id = database.insert_landslide_event(event)
                event["id"] = row_id
                landslide_state["last_event_id"] = row_id
                landslide_state["latest_reading"] = event
                landslide_state["node_id"] = event.get("node_id")

                # Evaluate landslide SMS alert
                try:
                    send_landslide_sms(event)
                except Exception as ls_err:
                    log.exception(f"Error evaluating landslide SMS alert: {ls_err}")

                # Broadcast live reading to connected browsers
                broadcast_sse({
                    "type": "landslide",
                    "event": event,
                    "data": event,
                })
                log.info(
                    f"[LANDSLIDE] Node {event.get('node_id')} | Pkt {event.get('packet_id')} "
                    f"| Soil {event.get('soil_moisture')} ADC "
                    f"| Tilt {event.get('tilt_angle')}° "
                    f"| Risk {(event.get('smoothed_probability', 0) * 100):.1f}% "
                    f"| {event.get('status')}"
                )

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
            "latest_landslide_reading": landslide_state["latest_reading"] or database.fetch_latest_landslide_event(),
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


@app.route("/api/sensor-data", methods=["POST"])
def receive_sensor_data():
    """
    Receive telemetry directly from ESP32 via Wi-Fi/HTTP.
    """
    if not request.is_json:
        return jsonify({"status": "error", "message": "Content-Type must be application/json"}), 400

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "error", "message": "Invalid JSON"}), 400

    # -----------------------------------------------------------------------
    # DISPATCH: Route to landslide handler if soil_moisture key is present
    # -----------------------------------------------------------------------
    if "soil_moisture" in data:
        return _handle_landslide_payload(data)

    # Verify API Key from headers (case-insensitive) or JSON body for fire nodes
    api_key = (
        request.headers.get("X-ESP32-API-Key")
        or request.headers.get("x-esp32-api-key")
        or request.headers.get("X-API-Key")
        or data.get("api_key")
        or data.get("esp32_api_key")
    )
    if config.ESP32_API_KEY and config.ESP32_API_KEY != "change-this-secret":
        if api_key != config.ESP32_API_KEY:
            return jsonify({"status": "error", "message": "Unauthorized: Invalid or missing X-ESP32-API-Key"}), 401

    try:
        esp_status = str(data.get("status", data.get("fire_status", "NORMAL")))
        
        # Risk probability handling (supports smoothed_probability, risk_probability, fire_probability, confidence)
        prob_input = float(data.get("smoothed_probability", data.get("risk_probability", data.get("fire_probability", data.get("confidence", 0.0)))))
        confidence = prob_input * 100.0 if prob_input <= 1.0 else prob_input
        
        raw_prob = float(data.get("raw_probability", data.get("risk_probability", prob_input)))
        if raw_prob > 1.0:
            raw_prob /= 100.0

        # Determine prediction
        prediction = data.get("prediction")
        if not prediction:
            prediction = "FIRE" if ("FIRE" in esp_status.upper() or confidence >= 70.0) else "NOT FIRE"

        pkt_id = data.get("packet_id")
        if pkt_id is None:
            pkt_id = int(time.time() % 100000)

        node_ts = data.get("node_timestamp", data.get("node_ts", int(time.time() * 1000)))

        event = {
            "type": "reading",
            "temp": float(data.get("temperature", data.get("temp", 0.0))),
            "humidity": float(data.get("humidity", data.get("hum", 0.0))),
            "mq2": float(data.get("mq2", data.get("gas", 0.0))),
            "temp_rate": float(data.get("temp_rate", 0.0)),
            "mq2_rate": float(data.get("mq2_rate", 0.0)),
            "delta": float(data.get("delta", 0.0)),
            "confidence": round(confidence, 1),
            "raw_probability": round(raw_prob, 3),
            "trend": str(data.get("trend", "—")),
            "fire_status": esp_status,
            "prediction": prediction,
            "safety_suppressed": bool(data.get("safety_suppressed", False)),
            "node_id": str(data.get("node_id", "ESP32-NODE-B")),
            "rssi": float(data.get("rssi", data.get("lora_rssi", 0.0))),
            "snr": float(data.get("snr", data.get("lora_snr", 0.0))),
            "packet_id": pkt_id,
            "node_timestamp": node_ts,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except (ValueError, TypeError) as e:
        return jsonify({"status": "error", "message": f"Invalid field type: {e}"}), 400

    try:
        event_queue.put_nowait(event)
        log.info(f"[ESP32] Node {event['node_id']} | Packet {event['packet_id']} | Temp {event['temp']}C | Hum {event['humidity']}% | MQ2 {event['mq2']} | Risk {event['confidence']:.1f}% | {event['trend']} | {event['fire_status']}")
    except queue.Full:
        log.warning("Event queue full, dropping ESP32 HTTP reading")
        return jsonify({"status": "error", "message": "Queue full"}), 503

    return jsonify({"status": "ok", "message": "Data received", "packet_id": pkt_id}), 200


def _handle_landslide_payload(data: dict):
    """
    Parse and enqueue a landslide telemetry payload from ESP32 Node B.
    Called from receive_sensor_data() when 'soil_moisture' key is detected.
    """
    try:
        pkt_id = data.get("packet_id")
        if pkt_id is None:
            pkt_id = int(time.time() % 100000)

        raw_prob = float(data.get("raw_probability", 0.0))
        smoothed_prob = float(data.get("smoothed_probability", raw_prob))

        event = {
            "type":               "landslide_reading",
            "node_id":            str(data.get("node_id", "NODE_B")),
            "packet_id":          pkt_id,
            "node_ts":            int(data.get("node_ts", int(time.time() * 1000))),
            "soil_moisture":      float(data.get("soil_moisture", 0.0)),
            "tilt_angle":         float(data.get("tilt_angle", 0.0)),
            "vibration":          float(data.get("vibration", 0.0)),
            "soil_rate":          float(data.get("soil_rate", 0.0)),
            "tilt_rate":          float(data.get("tilt_rate", 0.0)),
            "raw_probability":    round(raw_prob, 4),
            "smoothed_probability": round(smoothed_prob, 4),
            "trend":              str(data.get("trend", "STABLE")),
            "status":             str(data.get("status", "NORMAL")),
            "rssi":               float(data.get("rssi", 0.0)),
            "snr":                float(data.get("snr", 0.0)),
            "timestamp":          datetime.now(timezone.utc).isoformat(),
        }
    except (ValueError, TypeError) as e:
        return jsonify({"status": "error", "message": f"Invalid landslide field type: {e}"}), 400

    try:
        event_queue.put_nowait(event)
    except queue.Full:
        log.warning("Event queue full, dropping landslide HTTP reading")
        return jsonify({"status": "error", "message": "Queue full"}), 503

    return jsonify({"status": "ok", "message": "Landslide data received", "packet_id": pkt_id}), 200

# ---------------------------------------------------------------------------
# SMS Recipients & Testing Endpoints
# ---------------------------------------------------------------------------

@app.route("/api/recipients", methods=["GET"])
def get_recipients():
    """
    Return all registered alert phone numbers and Twilio gateway status.
    """
    recipients = database.fetch_recipients()
    has_auth_token = bool(config.TWILIO_ACCOUNT_SID and config.TWILIO_AUTH_TOKEN)
    has_api_key = bool(config.TWILIO_ACCOUNT_SID and config.TWILIO_API_KEY_SID and config.TWILIO_API_KEY_SECRET)
    sender_configured = bool(config.TWILIO_WHATSAPP_FROM if config.ALERT_CHANNEL == "whatsapp" else config.TWILIO_FROM_NUMBER)
    twilio_configured = (has_auth_token or has_api_key) and sender_configured
    return jsonify({
        "status": "ok",
        "recipients": recipients,
        "count": len(recipients),
        "primary": database.fetch_primary_recipient(),
        "twilio_configured": twilio_configured,
        "alert_channel": config.ALERT_CHANNEL,
        "from_number": (config.TWILIO_WHATSAPP_FROM if config.ALERT_CHANNEL == "whatsapp" else config.TWILIO_FROM_NUMBER) or "Not set",
    })


@app.route("/api/recipients", methods=["POST"])
def register_recipient():
    """
    Register or update a phone number to receive emergency fire SMS alerts.
    """
    data = request.get_json(silent=True) or {}
    raw_phone = str(data.get("phone_number", "")).strip()
    name = str(data.get("name", "Emergency Responder")).strip()

    # Normalize phone number
    phone = raw_phone.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if not phone:
        return jsonify({"status": "error", "message": "Phone number cannot be empty."}), 400

    # Ensure E.164 formatting (starts with +)
    if not phone.startswith("+"):
        # If standard 10 digit Indian number without country code, prefix +91
        if len(phone) == 10 and phone.isdigit():
            phone = f"+91{phone}"
        else:
            phone = f"+{phone}"

    saved = database.add_or_update_recipient(phone, name)
    config.TWILIO_TO_NUMBER = phone  # Update current active memory target

    has_auth_token = bool(config.TWILIO_ACCOUNT_SID and config.TWILIO_AUTH_TOKEN)
    has_api_key = bool(config.TWILIO_ACCOUNT_SID and config.TWILIO_API_KEY_SID and config.TWILIO_API_KEY_SECRET)
    sender_configured = bool(config.TWILIO_WHATSAPP_FROM if config.ALERT_CHANNEL == "whatsapp" else config.TWILIO_FROM_NUMBER)
    twilio_configured = (has_auth_token or has_api_key) and sender_configured

    msg = f"Mobile number {phone} registered successfully for alerts!"
    if not twilio_configured:
        msg += " (Twilio SID/Auth Token pending in .env for delivery)"

    return jsonify({
        "status": "ok",
        "message": msg,
        "recipient": saved,
        "recipients": database.fetch_recipients(),
        "twilio_configured": twilio_configured,
    })


@app.route("/api/recipients", methods=["DELETE"])
def remove_recipient():
    """
    Delete a registered mobile number.
    """
    data = request.get_json(silent=True) or {}
    phone = str(data.get("phone_number", "")).strip()
    if not phone:
        return jsonify({"status": "error", "message": "Phone number required"}), 400

    deleted = database.delete_recipient(phone)
    return jsonify({
        "status": "ok",
        "deleted": deleted,
        "recipients": database.fetch_recipients(),
    })


@app.route("/api/test-sms", methods=["POST"])
def trigger_test_sms():
    """
    Endpoint triggered by the top-right button to manually dispatch an alert.
    Uses the latest real-time telemetry from the dashboard.
    """
    data = request.get_json(silent=True) or {}
    custom_target = data.get("phone_number")

    latest = system_state.get("latest_reading") or {}
    alert_event = {
        "type": "reading",
        "temp": latest.get("temp", 35.2),
        "humidity": latest.get("humidity", 54.8),
        "mq2": latest.get("mq2", 510.0),
        "delta": latest.get("delta", 228.0),
        "confidence": latest.get("confidence", 100.0),
        "prediction": "FIRE",
        "safety_suppressed": False,
        "node_id": latest.get("node_id", "ESP32-LORA-NODE-01"),
        "timestamp": latest.get("timestamp", datetime.now(timezone.utc).isoformat()),
    }
    try:
        result = send_fire_sms(alert_event, target_number=custom_target)
        if result.get("twilio_configured") is False:
            return jsonify({
                "status": "info",
                "message": result.get("message", "Number registered. Twilio credentials not configured in .env yet."),
                "details": result,
            })
        return jsonify({
            "status": "ok" if result.get("success", True) else "warning",
            "message": result.get("message", "Fire alert triggered"),
            "details": result,
        })
    except Exception as exc:
        log.exception(f"Error in manual alert trigger: {exc}")
        return jsonify({"status": "error", "message": str(exc)}), 500


# ---------------------------------------------------------------------------
# Landslide REST API Endpoints
# ---------------------------------------------------------------------------

@app.route("/api/landslide/events")
def get_landslide_events():
    """
    Returns recent landslide events, newest first.
    Query params: ?limit=100 (default 100, max 500)
    """
    limit_arg = request.args.get("limit", default=100, type=int)
    limit = max(1, min(limit_arg, 500))
    events = database.fetch_recent_landslide_events(limit=limit)
    return jsonify({
        "count": len(events),
        "events": events,
    })


@app.route("/api/landslide/status")
def get_landslide_status():
    """
    Returns latest landslide reading and state.
    """
    latest = landslide_state.get("latest_reading") or database.fetch_latest_landslide_event()
    return jsonify({
        "status": "ok",
        "latest_reading": latest,
        "node_id": landslide_state.get("node_id"),
    })



# ---------------------------------------------------------------------------
# Application Entry Point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Forest Fire Alert Monitoring System")
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Run in mock replay mode using mock data without physical ESP32",
    )
    parser.add_argument(
        "--mock-file",
        type=str,
        default=config.MOCK_REPLAY_FILE,
        help=f"File path for mock replay (default: {config.MOCK_REPLAY_FILE})",
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
    parser.add_argument(
        "--no-loop",
        action="store_true",
        help="Replay mock file only once without continuous cycling",
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
        should_loop = config.MOCK_LOOP_FOREVER and not args.no_loop
        log.info(
            f">>> RUNNING IN MOCK / DEMO MODE (replaying {args.mock_file}, loop={should_loop}) <<<"
        )
        reader = MockSerialReader(
            mock_file=args.mock_file,
            event_queue=event_queue,
            delay=config.MOCK_LINE_DELAY_SECONDS,
            loop_forever=should_loop,
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
    log.info(f"Dashboard available at: http://localhost:{args.port}")
    log.info(f"Network / Wi-Fi Access: http://10.29.159.211:{args.port}")
    log.info(f"ESP32 Telemetry Target: http://10.29.159.211:{args.port}/api/sensor-data")
    app.run(
        host=config.FLASK_HOST,
        port=args.port,
        debug=config.FLASK_DEBUG,
        use_reloader=False,  # Important: Avoid duplicate background threads
        threaded=True,
    )


if __name__ == "__main__":
    main()
