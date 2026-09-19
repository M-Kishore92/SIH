"""
database.py — SQLite persistence layer
=======================================
Two tables:
  1. events      — every sensor reading (one row per monitoring line)
  2. calibration — the most recent calibration snapshot

All timestamps are stored in ISO-8601 UTC strings for portability.
"""

import sqlite3
import logging
from datetime import datetime, timezone
from typing import Optional

import config

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

def _get_conn() -> sqlite3.Connection:
    """Return a new SQLite connection with row_factory set for dict access."""
    conn = sqlite3.connect(config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    # Enable WAL mode for better concurrent read/write performance
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# ---------------------------------------------------------------------------
# Schema initialisation
# ---------------------------------------------------------------------------

def init_db():
    """
    Create tables if they do not exist.
    Called once at application startup.
    """
    with _get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp        TEXT    NOT NULL,
                temp             REAL,
                humidity         REAL,
                mq2              REAL,
                baseline         REAL,
                delta            REAL,
                temp_rate        REAL,
                mq2_rate         REAL,
                prediction       TEXT,
                confidence       REAL,
                safety_suppressed INTEGER DEFAULT 0,   -- 0=false, 1=true
                node_id          TEXT DEFAULT 'ESP32-LORA-NODE-01'
            );

            CREATE TABLE IF NOT EXISTS calibration (
                id             INTEGER PRIMARY KEY,   -- always row 1 (upsert)
                baseline_mq2   REAL,
                stddev         REAL,
                threshold_delta REAL,
                baseline_temp  REAL,
                calibrated_at  TEXT
            );

            CREATE TABLE IF NOT EXISTS recipients (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                phone_number  TEXT UNIQUE NOT NULL,
                name          TEXT DEFAULT 'Emergency Responder',
                is_active     INTEGER DEFAULT 1,
                registered_at TEXT
            );

            CREATE TABLE IF NOT EXISTS landslide_events (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp            TEXT NOT NULL,
                node_id              TEXT,
                packet_id            INTEGER,
                node_ts              INTEGER,
                soil_moisture        REAL,
                tilt_angle           REAL,
                vibration            REAL,
                soil_rate            REAL,
                tilt_rate            REAL,
                raw_probability      REAL,
                smoothed_probability REAL,
                trend                TEXT,
                status               TEXT,
                rssi                 REAL,
                snr                  REAL
            );
        """)
        # Ensure node_id exists if table was created earlier without it
        try:
            conn.execute("ALTER TABLE events ADD COLUMN node_id TEXT DEFAULT 'ESP32-LORA-NODE-01'")
        except Exception:
            pass

        # Add ESP32 Node B fields
        try:
            conn.executescript("""
                ALTER TABLE events ADD COLUMN rssi REAL;
                ALTER TABLE events ADD COLUMN snr REAL;
                ALTER TABLE events ADD COLUMN packet_id INTEGER;
                ALTER TABLE events ADD COLUMN node_timestamp INTEGER;
                ALTER TABLE events ADD COLUMN trend TEXT;
                ALTER TABLE events ADD COLUMN raw_probability REAL;
                ALTER TABLE events ADD COLUMN fire_status TEXT;
            """)
        except Exception:
            pass

        # Seed TWILIO_TO_NUMBER from config if recipients table is empty
        if config.TWILIO_TO_NUMBER:
            try:
                row = conn.execute("SELECT COUNT(*) as cnt FROM recipients").fetchone()
                if row and row["cnt"] == 0:
                    now = datetime.now(timezone.utc).isoformat()
                    conn.execute(
                        "INSERT OR IGNORE INTO recipients (phone_number, name, registered_at) VALUES (?, ?, ?)",
                        (config.TWILIO_TO_NUMBER.strip(), "Default Responder", now)
                    )
            except Exception:
                pass

    log.info(f"Database initialised at {config.DATABASE_PATH}")


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

def insert_event(event: dict) -> Optional[int]:
    """
    Insert a 'reading' event dict into the events table.
    Returns the new row id, or None if the event is not a reading.
    """
    if event.get("type") != "reading":
        return None

    sql = """
        INSERT INTO events
            (timestamp, temp, humidity, mq2, baseline, delta,
             temp_rate, mq2_rate, prediction, confidence, safety_suppressed, node_id,
             rssi, snr, packet_id, node_timestamp, trend, raw_probability, fire_status)
        VALUES
            (:timestamp, :temp, :humidity, :mq2, :baseline, :delta,
             :temp_rate, :mq2_rate, :prediction, :confidence, :safety_suppressed, :node_id,
             :rssi, :snr, :packet_id, :node_timestamp, :trend, :raw_probability, :fire_status)
    """
    with _get_conn() as conn:
        cur = conn.execute(sql, {
            "timestamp":        event.get("timestamp"),
            "temp":             event.get("temp"),
            "humidity":         event.get("humidity"),
            "mq2":              event.get("mq2"),
            "baseline":         event.get("baseline"),
            "delta":            event.get("delta"),
            "temp_rate":        event.get("temp_rate"),
            "mq2_rate":         event.get("mq2_rate"),
            "prediction":       event.get("prediction"),
            "confidence":       event.get("confidence"),
            "safety_suppressed": 1 if event.get("safety_suppressed") else 0,
            "node_id":          event.get("node_id", "ESP32-LORA-NODE-01"),
            "rssi":             event.get("rssi"),
            "snr":              event.get("snr"),
            "packet_id":        event.get("packet_id"),
            "node_timestamp":   event.get("node_timestamp"),
            "trend":            event.get("trend"),
            "raw_probability":  event.get("raw_probability"),
            "fire_status":      event.get("fire_status"),
        })
        row_id = cur.lastrowid
    log.debug(f"Inserted event id={row_id}, prediction={event.get('prediction')}")
    return row_id


def update_safety_suppressed(row_id: Optional[int] = None):
    """
    Mark an event as safety-suppressed.
    If row_id is provided, updates that specific event.
    Otherwise, updates the most recently inserted event.
    """
    with _get_conn() as conn:
        if row_id is not None:
            conn.execute(
                "UPDATE events SET safety_suppressed=1 WHERE id=?",
                (row_id,),
            )
        else:
            conn.execute(
                "UPDATE events SET safety_suppressed=1 WHERE id = (SELECT MAX(id) FROM events)"
            )
    log.info(f"Updated event {row_id or 'latest'} as safety-suppressed")



def fetch_recent_events(limit: int = 100) -> list[dict]:
    """
    Return the *limit* most recent events, newest first.
    Each item is a plain dict (JSON-serialisable).
    """
    sql = """
        SELECT id, timestamp, temp, humidity, mq2, baseline, delta,
               temp_rate, mq2_rate, prediction, confidence, safety_suppressed, node_id,
               rssi, snr, packet_id, node_timestamp, trend, raw_probability, fire_status
        FROM events
        ORDER BY id DESC
        LIMIT ?
    """
    with _get_conn() as conn:
        rows = conn.execute(sql, (limit,)).fetchall()
    return [dict(r) for r in rows]


def fetch_latest_event() -> Optional[dict]:
    """Return the single most recent reading, or None if no data yet."""
    sql = """
        SELECT id, timestamp, temp, humidity, mq2, baseline, delta,
               temp_rate, mq2_rate, prediction, confidence, safety_suppressed, node_id,
               rssi, snr, packet_id, node_timestamp, trend, raw_probability, fire_status
        FROM events
        ORDER BY id DESC
        LIMIT 1
    """
    with _get_conn() as conn:
        row = conn.execute(sql).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------

def upsert_calibration(baseline_mq2: float = None, stddev: float = None,
                        threshold_delta: float = None, baseline_temp: float = None):
    """
    Upsert the single calibration row (id=1).
    Only non-None values are written so partial updates are safe.
    """
    now = datetime.now(timezone.utc).isoformat()

    with _get_conn() as conn:
        # Ensure row exists
        conn.execute("""
            INSERT OR IGNORE INTO calibration (id, calibrated_at)
            VALUES (1, ?)
        """, (now,))

        if baseline_mq2 is not None:
            conn.execute("UPDATE calibration SET baseline_mq2=?, calibrated_at=? WHERE id=1",
                         (baseline_mq2, now))
        if stddev is not None:
            conn.execute("UPDATE calibration SET stddev=?, calibrated_at=? WHERE id=1",
                         (stddev, now))
        if threshold_delta is not None:
            conn.execute("UPDATE calibration SET threshold_delta=?, calibrated_at=? WHERE id=1",
                         (threshold_delta, now))
        if baseline_temp is not None:
            conn.execute("UPDATE calibration SET baseline_temp=?, calibrated_at=? WHERE id=1",
                         (baseline_temp, now))

    log.debug("Calibration table updated")


def fetch_calibration() -> Optional[dict]:
    """Return the current calibration snapshot, or None if not yet received."""
    with _get_conn() as conn:
        row = conn.execute("SELECT * FROM calibration WHERE id=1").fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# SMS Alert Recipients
# ---------------------------------------------------------------------------

def add_or_update_recipient(phone_number: str, name: str = "Emergency Responder") -> dict:
    """
    Add or update a registered mobile number for SMS alerts.
    """
    phone = phone_number.strip().replace(" ", "").replace("-", "")
    now = datetime.now(timezone.utc).isoformat()

    with _get_conn() as conn:
        conn.execute("""
            INSERT INTO recipients (phone_number, name, is_active, registered_at)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(phone_number) DO UPDATE SET
                name = excluded.name,
                is_active = 1,
                registered_at = excluded.registered_at
        """, (phone, name.strip() or "Emergency Responder", now))

    log.info(f"Registered SMS alert recipient: {phone} ({name})")
    return {"phone_number": phone, "name": name, "is_active": 1, "registered_at": now}


def delete_recipient(phone_number: str) -> bool:
    """
    Remove a registered mobile number from SMS alerts.
    """
    phone = phone_number.strip().replace(" ", "").replace("-", "")
    with _get_conn() as conn:
        cur = conn.execute("DELETE FROM recipients WHERE phone_number = ?", (phone,))
        deleted = cur.rowcount > 0

    log.info(f"Deleted SMS alert recipient: {phone} (success={deleted})")
    return deleted


def fetch_recipients() -> list[dict]:
    """
    Return all active registered recipients.
    """
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT id, phone_number, name, is_active, registered_at FROM recipients WHERE is_active=1 ORDER BY id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def fetch_primary_recipient() -> Optional[str]:
    """
    Return the most recently registered phone number, or config fallback.
    """
    recipients = fetch_recipients()
    if recipients:
        return recipients[0]["phone_number"]
    return config.TWILIO_TO_NUMBER or None


# ---------------------------------------------------------------------------
# Landslide Events
# ---------------------------------------------------------------------------

def insert_landslide_event(event: dict) -> Optional[int]:
    """
    Insert a landslide telemetry event into the landslide_events table.
    Returns the new row id, or None if the event type is wrong.
    """
    if event.get("type") != "landslide_reading":
        return None

    sql = """
        INSERT INTO landslide_events
            (timestamp, node_id, packet_id, node_ts,
             soil_moisture, tilt_angle, vibration,
             soil_rate, tilt_rate,
             raw_probability, smoothed_probability,
             trend, status, rssi, snr)
        VALUES
            (:timestamp, :node_id, :packet_id, :node_ts,
             :soil_moisture, :tilt_angle, :vibration,
             :soil_rate, :tilt_rate,
             :raw_probability, :smoothed_probability,
             :trend, :status, :rssi, :snr)
    """
    with _get_conn() as conn:
        cur = conn.execute(sql, {
            "timestamp":            event.get("timestamp"),
            "node_id":              event.get("node_id"),
            "packet_id":            event.get("packet_id"),
            "node_ts":              event.get("node_ts"),
            "soil_moisture":        event.get("soil_moisture"),
            "tilt_angle":           event.get("tilt_angle"),
            "vibration":            event.get("vibration"),
            "soil_rate":            event.get("soil_rate"),
            "tilt_rate":            event.get("tilt_rate"),
            "raw_probability":      event.get("raw_probability"),
            "smoothed_probability": event.get("smoothed_probability"),
            "trend":                event.get("trend"),
            "status":               event.get("status"),
            "rssi":                 event.get("rssi"),
            "snr":                  event.get("snr"),
        })
        row_id = cur.lastrowid
    log.debug(f"Inserted landslide event id={row_id}, status={event.get('status')}")
    return row_id


def fetch_recent_landslide_events(limit: int = 100) -> list[dict]:
    """
    Return the most recent landslide events, newest first.
    """
    sql = """
        SELECT id, timestamp, node_id, packet_id, node_ts,
               soil_moisture, tilt_angle, vibration,
               soil_rate, tilt_rate,
               raw_probability, smoothed_probability,
               trend, status, rssi, snr
        FROM landslide_events
        ORDER BY id DESC
        LIMIT ?
    """
    with _get_conn() as conn:
        rows = conn.execute(sql, (limit,)).fetchall()
    return [dict(r) for r in rows]


def fetch_latest_landslide_event() -> Optional[dict]:
    """Return the single most recent landslide reading, or None."""
    sql = """
        SELECT id, timestamp, node_id, packet_id, node_ts,
               soil_moisture, tilt_angle, vibration,
               soil_rate, tilt_rate,
               raw_probability, smoothed_probability,
               trend, status, rssi, snr
        FROM landslide_events
        ORDER BY id DESC
        LIMIT 1
    """
    with _get_conn() as conn:
        row = conn.execute(sql).fetchone()
    return dict(row) if row else None

