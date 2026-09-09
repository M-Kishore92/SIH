"""
serial_reader.py — ESP32 Serial Port Reader
=============================================
Opens a pyserial connection to the ESP32 COM port and reads lines
continuously in a background daemon thread.  Each raw line is handed to
parser.py; the resulting structured dict (if any) is placed on a
shared queue so the Flask app can consume it without blocking.

Key design decisions
---------------------
* Runs as a **daemon thread** — it dies automatically when the main
  process exits.
* Uses a threading.Event (stop_event) so the thread can be stopped
  cleanly during tests.
* The caller supplies an optional on_event callback for immediate
  side-effects (e.g. SMS alert); queued events serve the dashboard.
* Safety-suppression lines patch the *last* reading event in-place so
  the dashboard can colour them amber.
"""

import queue
import serial
import threading
import time
import logging

from parser import parse_line

log = logging.getLogger(__name__)


class SerialReader:
    """
    Wraps pyserial and drives the parse/queue pipeline.

    Parameters
    ----------
    port : str
        Serial port, e.g. "COM3" on Windows or "/dev/ttyUSB0" on Linux.
    baud_rate : int
        Must match the ESP32 firmware (115200).
    event_queue : queue.Queue
        Parsed events are put() here for the Flask app to get().
    on_event : callable, optional
        Called synchronously in the reader thread with each parsed event.
        Use for SMS alerts where you want zero-latency reaction.
    """

    def __init__(
        self,
        port: str,
        baud_rate: int,
        event_queue: queue.Queue,
        on_event=None,
    ):
        self.port = port
        self.baud_rate = baud_rate
        self.event_queue = event_queue
        self.on_event = on_event

        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="SerialReaderThread",
            daemon=True,          # exits with the main process
        )

        # Track the last parsed "reading" event so we can mark it
        # safety_suppressed if the very next line is a suppression note
        self._last_reading: dict | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self):
        """Start the background reader thread."""
        log.info(f"Starting serial reader on {self.port} @ {self.baud_rate} baud")
        self._thread.start()

    def stop(self):
        """Signal the background thread to stop and wait for it."""
        self._stop_event.set()
        self._thread.join(timeout=5)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run(self):
        """Main loop: open port → read lines → parse → enqueue."""
        while not self._stop_event.is_set():
            try:
                with serial.Serial(self.port, self.baud_rate, timeout=2) as ser:
                    log.info(f"Serial port {self.port} opened successfully")
                    while not self._stop_event.is_set():
                        raw = ser.readline()
                        if not raw:
                            continue  # timeout — keep looping
                        try:
                            line = raw.decode("utf-8", errors="replace")
                        except Exception:
                            continue

                        self._process_line(line)

            except serial.SerialException as exc:
                # Port not available yet — retry after a pause
                log.warning(f"Serial error ({exc}). Retrying in 5 s …")
                time.sleep(5)
            except Exception as exc:
                log.exception(f"Unexpected error in serial reader: {exc}")
                time.sleep(2)

    def _process_line(self, raw_line: str):
        """
        Parse one raw line and handle safety-suppression bookkeeping.

        The ESP32 emits suppression notes on the line *immediately after*
        the FIRE reading they refer to.  We track the last reading and
        patch its safety_suppressed flag when we see a suppression note.
        """
        event = parse_line(raw_line)
        if event is None:
            return  # unrecognised line — skip

        if event["type"] == "safety_suppression":
            # Patch the previous reading that triggered this note
            if self._last_reading is not None:
                self._last_reading["safety_suppressed"] = True
                log.info("Marked last reading as safety-suppressed")
                # Push update event so DB and dashboard update in real-time
                update_evt = {
                    "type": "safety_suppressed_update",
                    "reading": self._last_reading,
                    "timestamp": event["timestamp"],
                    "raw": event["raw"]
                }
                try:
                    self.event_queue.put_nowait(update_evt)
                except queue.Full:
                    pass
                if self.on_event:
                    try:
                        self.on_event(update_evt)
                    except Exception as exc:
                        log.exception(f"on_event callback raised on suppression: {exc}")
            return

        if event["type"] == "reading":
            self._last_reading = event   # keep reference for patching
        else:
            self._last_reading = None    # non-reading resets the window

        # Push to queue (non-blocking; drop if full — dashboard will refresh)
        try:
            self.event_queue.put_nowait(event)
        except queue.Full:
            log.warning("Event queue full — dropping oldest event")

        # Call the synchronous callback (e.g. SMS)
        if self.on_event:
            try:
                self.on_event(event)
            except Exception as exc:
                log.exception(f"on_event callback raised: {exc}")


class MockSerialReader:
    """
    Simulates the ESP32 serial output by reading lines from mock_data.txt
    in a background loop with a configurable delay.

    Parameters
    ----------
    mock_file : str
        Path to mock text file containing canned ESP32 lines.
    event_queue : queue.Queue
        Parsed events are put() here.
    on_event : callable, optional
        Callback for each parsed event.
    delay : float
        Delay in seconds between lines (default 1.0s).
    loop_forever : bool
        If True, restarts replay from the beginning indefinitely.
    """

    def __init__(
        self,
        mock_file: str,
        event_queue: queue.Queue,
        on_event=None,
        delay: float = 1.0,
        loop_forever: bool = True,
    ):
        self.mock_file = mock_file
        self.event_queue = event_queue
        self.on_event = on_event
        self.delay = delay
        self.loop_forever = loop_forever

        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="MockSerialReaderThread",
            daemon=True,
        )
        self._last_reading: dict | None = None

    def start(self):
        """Start the background mock replay thread."""
        log.info(f"Starting MOCK serial reader using '{self.mock_file}' (delay={self.delay}s)")
        self._thread.start()

    def stop(self):
        """Signal mock reader to stop and wait for it."""
        self._stop_event.set()
        self._thread.join(timeout=3)

    def _run(self):
        while not self._stop_event.is_set():
            try:
                with open(self.mock_file, "r", encoding="utf-8") as f:
                    for line in f:
                        if self._stop_event.is_set():
                            break
                        self._process_line(line)
                        time.sleep(self.delay)
            except FileNotFoundError:
                log.error(f"Mock file not found: {self.mock_file}")
                time.sleep(3)
            except Exception as exc:
                log.exception(f"Error in mock serial reader: {exc}")
                time.sleep(1)

            if not self.loop_forever:
                log.info("Mock data replay finished (one-time pass). Reader idle while server continues running.")
                break
            log.info("Mock data replay finished. Looping from start...")

    def _process_line(self, raw_line: str):
        event = parse_line(raw_line)
        if event is None:
            return

        if event["type"] == "safety_suppression":
            if self._last_reading is not None:
                self._last_reading["safety_suppressed"] = True
                log.info("Marked last reading as safety-suppressed")
                update_evt = {
                    "type": "safety_suppressed_update",
                    "reading": self._last_reading,
                    "timestamp": event["timestamp"],
                    "raw": event["raw"]
                }
                try:
                    self.event_queue.put_nowait(update_evt)
                except queue.Full:
                    pass
                if self.on_event:
                    try:
                        self.on_event(update_evt)
                    except Exception as exc:
                        log.exception(f"on_event callback raised: {exc}")
            return

        if event["type"] == "reading":
            self._last_reading = event
        else:
            self._last_reading = None

        try:
            self.event_queue.put_nowait(event)
        except queue.Full:
            log.warning("Event queue full")

        if self.on_event:
            try:
                self.on_event(event)
            except Exception as exc:
                log.exception(f"on_event callback raised: {exc}")

