"""
config.py — Centralised configuration
======================================
All tuneable values live here.  Secrets are read from a .env file via
python-dotenv so they never have to be hard-coded.

Create a `.env` file in the project root (same directory as app.py):

    SERIAL_PORT=COM3
    TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
    TWILIO_AUTH_TOKEN=your_auth_token
    TWILIO_FROM_NUMBER=+1xxxxxxxxxx
    TWILIO_TO_NUMBER=+91xxxxxxxxxx

Then run:  python app.py
"""

import os
from dotenv import load_dotenv

# Load .env from the directory containing this file
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

# ---------------------------------------------------------------------------
# Serial / hardware
# ---------------------------------------------------------------------------
SERIAL_PORT: str = os.getenv("SERIAL_PORT", "COM3")        # Change to match your system
BAUD_RATE: int = 115200

# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------
DATABASE_PATH: str = os.getenv("DATABASE_PATH", "fire_alerts.db")

# ---------------------------------------------------------------------------
# Twilio credentials  (loaded from .env — do NOT hard-code here)
# ---------------------------------------------------------------------------
TWILIO_ACCOUNT_SID: str    = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN: str     = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_API_KEY_SID: str    = os.getenv("TWILIO_API_KEY_SID", "")
TWILIO_API_KEY_SECRET: str = os.getenv("TWILIO_API_KEY_SECRET", "")
TWILIO_FROM_NUMBER: str    = os.getenv("TWILIO_FROM_NUMBER", "")
TWILIO_TO_NUMBER: str      = os.getenv("TWILIO_TO_NUMBER", "")

# ---------------------------------------------------------------------------
# Alert Channel: "whatsapp" (free sandbox) or "sms" (cellular) or "both"
# ---------------------------------------------------------------------------
ALERT_CHANNEL: str         = os.getenv("ALERT_CHANNEL", "whatsapp").lower()
TWILIO_WHATSAPP_FROM: str  = os.getenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")

# ---------------------------------------------------------------------------
# SMS debounce & limits
# ---------------------------------------------------------------------------
SMS_DEBOUNCE_SECONDS: int  = int(os.getenv("SMS_DEBOUNCE_SECONDS", "120"))
SMS_SEND_ONCE: bool         = os.getenv("SMS_SEND_ONCE", "true").lower() == "true"
AUTO_DISPATCH_ENABLED: bool = os.getenv("AUTO_DISPATCH_ENABLED", "false").lower() == "true"
MOCK_LOOP_FOREVER: bool     = os.getenv("MOCK_LOOP_FOREVER", "true").lower() == "true"

# ---------------------------------------------------------------------------
# Flask
# ---------------------------------------------------------------------------
FLASK_HOST: str = os.getenv("FLASK_HOST", "0.0.0.0")
FLASK_PORT: int = int(os.getenv("FLASK_PORT", "5000"))
FLASK_DEBUG: bool = os.getenv("FLASK_DEBUG", "false").lower() == "true"

# ---------------------------------------------------------------------------
# Mock / demo mode replay file
# ---------------------------------------------------------------------------
MOCK_REPLAY_FILE: str = os.getenv("MOCK_REPLAY_FILE", "mock_data.txt")
MOCK_LINE_DELAY_SECONDS: float = float(os.getenv("MOCK_LINE_DELAY_SECONDS", "1.0"))
