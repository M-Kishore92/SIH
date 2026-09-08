"""
sms_alert.py — Twilio SMS alert sender with debounce logic
===========================================================
Sends a fire-alert SMS via the Twilio REST API.

Debounce rules
--------------
An SMS is sent when:
  (a) prediction is FIRE, AND
  (b) EITHER the last SMS was > SMS_DEBOUNCE_SECONDS ago
      OR the prediction just transitioned from NOT FIRE → FIRE

This prevents SMS storms during a sustained fire event while still
alerting promptly on every new ignition event.
"""

import logging
import time
from datetime import datetime, timezone

import config

log = logging.getLogger(__name__)


class SMSAlerter:
    """
    Stateful SMS sender that implements the debounce / transition logic.

    Usage
    -----
    alerter = SMSAlerter()
    alerter.handle_event(parsed_event_dict)
    """

    def __init__(self):
        self._last_sms_time: float = 0.0          # epoch seconds
        self._last_prediction: str = "NOT FIRE"   # track state transitions

    def handle_event(self, event: dict):
        """
        Evaluate an event dict and send an SMS if warranted.
        Safe to call with any event type — only "reading" events with
        Prediction=FIRE will trigger an SMS.
        """
        if event.get("type") != "reading":
            return

        prediction = event.get("prediction", "NOT FIRE")

        # Gate 1 — only care about FIRE predictions
        if prediction != "FIRE":
            self._last_prediction = prediction
            return

        # Gate 2 — safety-suppressed events are near-misses, not real fires
        if event.get("safety_suppressed"):
            log.info("SMS skipped — event is safety-suppressed (near-miss)")
            self._last_prediction = prediction
            return

        now = time.time()
        time_since_last_sms = now - self._last_sms_time
        transitioned = self._last_prediction != "FIRE"  # just became FIRE

        should_send = transitioned or (time_since_last_sms >= config.SMS_DEBOUNCE_SECONDS)

        if should_send:
            self._send_sms(event)
            self._last_sms_time = now
        else:
            remaining = config.SMS_DEBOUNCE_SECONDS - time_since_last_sms
            log.debug(f"SMS debounced — {remaining:.0f}s until next allowed SMS")

        self._last_prediction = prediction

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _format_message(self, event: dict) -> str:
        """Build the SMS body string."""
        ts_raw = event.get("timestamp", datetime.now(timezone.utc).isoformat())
        # Friendly local representation
        try:
            ts_obj = datetime.fromisoformat(ts_raw)
            ts_str = ts_obj.strftime("%Y-%m-%d %H:%M:%S UTC")
        except Exception:
            ts_str = ts_raw

        return (
            f"🔥 FIRE DETECTED — "
            f"Temp {event.get('temp')}C, "
            f"MQ2 {event.get('mq2')} (Δ+{event.get('delta')}), "
            f"Confidence {event.get('confidence')}%. "
            f"Time: {ts_str}"
        )

    def _send_sms(self, event: dict):
        """
        Actually dispatch the Twilio SMS.
        Logs a warning (instead of crashing) if Twilio credentials are missing.
        """
        if not all([
            config.TWILIO_ACCOUNT_SID,
            config.TWILIO_AUTH_TOKEN,
            config.TWILIO_FROM_NUMBER,
            config.TWILIO_TO_NUMBER,
        ]):
            log.warning(
                "Twilio credentials not configured — SMS not sent. "
                "Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, "
                "TWILIO_FROM_NUMBER, TWILIO_TO_NUMBER in .env"
            )
            return

        message_body = self._format_message(event)

        try:
            # Import here so the app still starts even if twilio is not installed
            from twilio.rest import Client  # type: ignore

            client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
            msg = client.messages.create(
                body=message_body,
                from_=config.TWILIO_FROM_NUMBER,
                to=config.TWILIO_TO_NUMBER,
            )
            log.info(f"SMS sent! SID={msg.sid} | Body: {message_body}")

        except ImportError:
            log.error(
                "twilio package not installed. Run: pip install twilio"
            )
        except Exception as exc:
            log.exception(f"Failed to send SMS via Twilio: {exc}")


# ---------------------------------------------------------------------------
# Module-level convenience instance (used by app.py)
# ---------------------------------------------------------------------------
_alerter = SMSAlerter()


def send_fire_sms(event: dict):
    """
    Module-level function so app.py can do:
        from sms_alert import send_fire_sms
        reader = SerialReader(..., on_event=send_fire_sms)
    """
    _alerter.handle_event(event)
