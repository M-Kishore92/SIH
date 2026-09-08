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
                safety_suppressed INTEGER DEFAULT 0   -- 0=false, 1=true
            );

            CREATE TABLE IF NOT EXISTS calibration (
                id             INTEGER PRIMARY KEY,   -- always row 1 (upsert)
                baseline_mq2   REAL,
                stddev         REAL,
                threshold_delta REAL,
                baseline_temp  REAL,
                calibrated_at  TEXT
            );
        """)
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
             temp_rate, mq2_rate, prediction, confidence, safety_suppressed)
        VALUES
            (:timestamp, :temp, :humidity, :mq2, :baseline, :delta,
             :temp_rate, :mq2_rate, :prediction, :confidence, :safety_suppressed)
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
               temp_rate, mq2_rate, prediction, confidence, safety_suppressed
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
               temp_rate, mq2_rate, prediction, confidence, safety_suppressed
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
