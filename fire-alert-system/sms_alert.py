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
        self._alert_sent_count: int = 0           # count of successfully dispatched alerts

    def handle_event(self, event: dict):
        """
        Evaluate an event dict and send an SMS if warranted.
        Safe to call with any event type — only "reading" events with
        Prediction=FIRE will trigger an SMS.
        """
        if event.get("type") != "reading":
            return

        prediction = event.get("prediction", "NOT FIRE")

        # Gate 0 — automatic alert dispatch disabled (manual button only)
        if not config.AUTO_DISPATCH_ENABLED:
            self._last_prediction = prediction
            return

        # Gate 1 — only care about FIRE predictions
        if prediction != "FIRE":
            self._last_prediction = prediction
            return

        # Gate 2 — safety-suppressed events are near-misses, not real fires
        if event.get("safety_suppressed"):
            log.info("SMS skipped — event is safety-suppressed (near-miss)")
            self._last_prediction = prediction
            return

        # Gate 3 — single-SMS quota protection for free tier accounts
        if config.SMS_SEND_ONCE and self._alert_sent_count >= 1:
            log.info(
                "SMS skipped — alert already sent once (SMS_SEND_ONCE enabled to conserve Twilio credits)"
            )
            self._last_prediction = prediction
            return

        now = time.time()
        time_since_last_sms = now - self._last_sms_time
        transitioned = self._last_prediction != "FIRE"  # just became FIRE

        should_send = transitioned or (time_since_last_sms >= config.SMS_DEBOUNCE_SECONDS)

        if should_send:
            res = self._send_sms(event)
            self._last_sms_time = now
            if res and res.get("success"):
                self._alert_sent_count += 1
                log.info(f"Fire alert SMS delivered! (Total sent this session: {self._alert_sent_count})")
        else:
            remaining = config.SMS_DEBOUNCE_SECONDS - time_since_last_sms
            log.debug(f"SMS debounced — {remaining:.0f}s until next allowed SMS")

        self._last_prediction = prediction

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _format_message(self, event: dict) -> str:
        """Build a detailed alert body for WhatsApp / SMS."""
        ts_raw = event.get("timestamp", datetime.now(timezone.utc).isoformat())
        try:
            ts_obj = datetime.fromisoformat(ts_raw)
            # Convert to IST (UTC+5:30) for Indian recipients
            from datetime import timedelta
            ist = ts_obj + timedelta(hours=5, minutes=30)
            ts_str = ist.strftime("%Y-%m-%d %H:%M:%S IST")
        except Exception:
            ts_str = ts_raw

        node_id = event.get("node_id", "ESP32-LORA-NODE-01")
        temp = event.get("temp", "--")
        humidity = event.get("humidity", "--")
        mq2 = event.get("mq2", "--")
        delta = event.get("delta", "--")
        confidence = event.get("confidence", "--")

        return (
            f"🔥 *PYROWATCH FIRE ALERT*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📍 *Sensor Node:* `{node_id}`\n"
            f"🌡️ *Temperature:* {temp}°C\n"
            f"💧 *Humidity:* {humidity}%\n"
            f"💨 *Smoke (MQ-2):* {mq2} ADC (Δ+{delta})\n"
            f"🎯 *Confidence:* {confidence}%\n"
            f"🕒 *Timestamp:* {ts_str}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🚨 *Immediate action recommended!*"
        )

    def _send_sms(self, event: dict, target_number: str = None) -> dict:
        """
        Actually dispatch the Twilio alert (WhatsApp or SMS) to registered recipients.
        Logs a warning (instead of crashing) if Twilio credentials are missing.
        """
        import database

        recipients = []
        if target_number:
            recipients = [target_number.strip()]
        else:
            db_recipients = database.fetch_recipients()
            recipients = [r["phone_number"] for r in db_recipients]
            if not recipients and config.TWILIO_TO_NUMBER:
                recipients = [config.TWILIO_TO_NUMBER.strip()]

        if not recipients:
            log.warning("No alert recipients registered and TWILIO_TO_NUMBER not set.")
            return {
                "success": False,
                "twilio_configured": False,
                "message": "No mobile numbers registered yet. Please register a number on the dashboard.",
            }

        # Check if Twilio is configured (either Auth Token OR API Key)
        has_auth_token = bool(config.TWILIO_ACCOUNT_SID and config.TWILIO_AUTH_TOKEN)
        has_api_key = bool(config.TWILIO_ACCOUNT_SID and config.TWILIO_API_KEY_SID and config.TWILIO_API_KEY_SECRET)
        sender_configured = bool(
            config.TWILIO_WHATSAPP_FROM if config.ALERT_CHANNEL == "whatsapp" else config.TWILIO_FROM_NUMBER
        )
        twilio_ready = (has_auth_token or has_api_key) and sender_configured

        if not twilio_ready:
            log.warning(
                "Twilio credentials not configured — alert not sent. "
                "Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN in .env"
            )
            return {
                "success": False,
                "twilio_configured": False,
                "recipients": recipients,
                "message": f"Recipient(s) {', '.join(recipients)} registered! (Note: Add TWILIO credentials in .env for delivery)",
            }

        message_body = self._format_message(event)
        sent_sids = []
        errors = []

        try:
            from twilio.rest import Client  # type: ignore

            # Prioritize Auth Token (standard account credential) over API Key
            if config.TWILIO_AUTH_TOKEN:
                client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
                log.info("Using Twilio Auth Token authentication")
            elif config.TWILIO_API_KEY_SID and config.TWILIO_API_KEY_SECRET:
                client = Client(
                    config.TWILIO_API_KEY_SID,
                    config.TWILIO_API_KEY_SECRET,
                    config.TWILIO_ACCOUNT_SID,
                )
                log.info("Using Twilio API Key authentication")
            else:
                client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
                log.info("Using Twilio Auth Token authentication")

            for phone in recipients:
                raw_phone = phone.strip()
                clean_phone = raw_phone.replace("whatsapp:", "").strip()

                # Dispatch via WhatsApp if configured
                if config.ALERT_CHANNEL in ("whatsapp", "both"):
                    wa_to = f"whatsapp:{clean_phone}"
                    try:
                        msg = client.messages.create(
                            body=message_body,
                            from_=config.TWILIO_WHATSAPP_FROM,
                            to=wa_to,
                        )
                        sent_sids.append(msg.sid)
                        log.info(f"WhatsApp alert sent to {wa_to}! SID={msg.sid}")
                    except Exception as wa_err:
                        log.error(f"Failed to send WhatsApp to {wa_to}: {wa_err}")
                        errors.append(f"WhatsApp {wa_to}: {wa_err}")

                # Dispatch via Cellular SMS if configured
                if config.ALERT_CHANNEL in ("sms", "both"):
                    try:
                        msg = client.messages.create(
                            body=message_body,
                            from_=config.TWILIO_FROM_NUMBER,
                            to=clean_phone,
                        )
                        sent_sids.append(msg.sid)
                        log.info(f"SMS sent to {clean_phone}! SID={msg.sid}")
                    except Exception as sms_err:
                        log.error(f"Failed to send SMS to {clean_phone}: {sms_err}")
                        errors.append(f"SMS {clean_phone}: {sms_err}")

            success = len(sent_sids) > 0
            channel_name = "WhatsApp" if config.ALERT_CHANNEL == "whatsapp" else ("WhatsApp & SMS" if config.ALERT_CHANNEL == "both" else "SMS")
            msg_text = f"{channel_name} alert sent to {len(sent_sids)} destination(s): {', '.join(recipients)}"
            if errors and not success:
                msg_text = f"Failed to send: {'; '.join(errors)}"

            return {
                "success": success,
                "twilio_configured": True,
                "sent_count": len(sent_sids),
                "sids": sent_sids,
                "errors": errors,
                "recipients": recipients,
                "message": msg_text,
            }

        except ImportError:
            log.error("twilio package not installed. Run: pip install twilio")
            return {
                "success": False,
                "twilio_configured": True,
                "message": "twilio package not installed. Run: pip install twilio",
            }
        except Exception as exc:
            log.exception(f"Failed to send SMS via Twilio: {exc}")
            return {
                "success": False,
                "twilio_configured": True,
                "message": str(exc),
            }


# ---------------------------------------------------------------------------
# Module-level convenience instance (used by app.py)
# ---------------------------------------------------------------------------
_alerter = SMSAlerter()


def send_fire_sms(event: dict, target_number: str = None) -> dict:
    """
    Module-level function so app.py can do:
        from sms_alert import send_fire_sms
        reader = SerialReader(..., on_event=send_fire_sms)
    """
    if target_number:
        return _alerter._send_sms(event, target_number=target_number)
    _alerter.handle_event(event)
    return {"status": "handled"}

