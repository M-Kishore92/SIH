# 🔥 PyroWatch — Real-Time Forest Fire Alert & Sensor Monitoring System

An edge-computing telemetry parser, SQLite event store, real-time web dashboard, and automated Twilio SMS alert gateway running entirely on your laptop. It intercepts live serial telemetry at **115200 baud** from an ESP32 edge microcontroller executing forest-fire inference firmware.

---

## 🌟 Hackathon Presentation Highlights

* **100% Non-Intrusive & Decoupled:** Operates strictly on passive serial output. Does not require changing a single line of ESP32 firmware.
* **Intelligent False-Positive Suppression:** Automatically captures and logs firmware `[SAFETY]` suppression events (near-misses where the ML model flagged a fire but the MQ-2 smoke sensor remained within calibrated baseline resistance), highlighting them in amber on the UI without triggering spurious SMS panics.
* **Debounced SMS Alert Gateway:** Alerts emergency teams instantly via Twilio on new fire transitions, while applying an automatic rate-limit debounce (120s) to prevent SMS message flooding during ongoing blazes.
* **Real-Time Dual-Axis Telemetry Dashboard:** Uses Server-Sent Events (SSE) and Chart.js to stream Temperature, Humidity, MQ-2 Gas Levels, Temp Rates, MQ-2 Rates, and Model Confidence with sub-second latency.
* **Zero-Hardware Mock Simulation Mode:** Includes an integrated replay engine (`python app.py --mock`) that accurately simulates all 4 ESP32 phases (Warm-up, Calibration, Baseline Lock, Normal Monitoring, Near-Miss Suppression, and Critical Fire Spike) for live stage demos without needing physical sensors plugged in.

---

## 📁 Project Architecture

```
fire-alert-system/
├── app.py               # Flask application: routes, SSE stream, background reader thread
├── serial_reader.py    # PySerial background thread reader & MockSerialReader replay engine
├── parser.py           # Regex parser decoding all 4 ESP32 serial output formats
├── database.py         # SQLite persistence: `events` timeline & `calibration` snapshots
├── sms_alert.py        # Twilio SMS sender with state-transition & time debounce logic
├── config.py           # Centralized configuration loaded from .env
├── mock_data.txt       # Realistic ESP32 serial log for hardware-free testing
├── requirements.txt    # Python package dependencies
├── .env.example        # Template for API keys, COM port, and tuning parameters
├── templates/
│   └── dashboard.html  # Responsive HTML5 dashboard with glassmorphism layout
└── static/
    ├── dashboard.css   # Dark-mode design system with glowing indicators & animations
    └── dashboard.js    # Chart.js dual-axis graph, SSE streaming, and toast notifications
```

---

## 📡 ESP32 Serial Protocol Specification

The ESP32 communicates at **115200 baud**. The Python parser decodes all four distinct phases:

### 1. Warm-up Phase
```
[Warm-up] MQ-2 stabilizing heater... 60s remaining
```
*Extracted:* `type="warmup"`, `seconds_remaining=60`

### 2. Calibration Phase & Baseline Output
```
[Calibrating] Establishing baseline... 110s remaining
[Calibrating] Baseline calibration complete!
   Baseline MQ-2    : 282.4
   MQ-2 StdDev (σ)  : 18.50
   Fire Threshold Δ : +92.5 (triggers when MQ2 >= 374.9)
   Baseline Temp    : 32.8 C
```
*Extracted:* `baseline_mq2`, `stddev`, `threshold_delta`, `baseline_temp`, stored into `calibration` table.

### 3. Normal Monitoring Line & Safety Suppression
```
Temp=33.2C | Hum=58.0% | MQ2=295 (Baseline=282, Delta=+13) | TempRate=+0.00 | MQ2Rate=+1.79 | Prediction=NOT FIRE | Confidence=100%
```
When an edge-case trigger occurs:
```
Temp=34.2C | Hum=56.1% | MQ2=365 (Baseline=282, Delta=+83) | TempRate=+0.01 | MQ2Rate=+12.00 | Prediction=FIRE | Confidence=72%
  └─ [SAFETY] Model flagged FIRE but MQ2 is within calibrated baseline - suppressed as likely false positive
```
*Extracted:* Line 1 parsed as `FIRE`. Line 2 immediately detected and updates `safety_suppressed = True` in memory, SQLite, and UI (highlighted in amber, SMS bypassed).

### 4. Critical Fire Alarm
```
Temp=34.3C | Hum=56.0% | MQ2=470 (Baseline=282, Delta=+188) | TempRate=+0.10 | MQ2Rate=+14.84 | Prediction=FIRE | Confidence=100%
```
*Extracted:* `prediction="FIRE"`, `safety_suppressed=False`. Dispatches Twilio SMS alert:
> 🔥 FIRE DETECTED — Temp 34.3C, MQ2 470 (Δ+188), Confidence 100%. Time: 2026-09-08 15:30:00 UTC

---

## 🚀 Getting Started

### 1. Clone or Open the Repository
```bash
cd fire-alert-system
```

### 2. Install Dependencies
Ensure you have Python 3.10+ installed:
```bash
pip install -r requirements.txt
```

### 3. Configure Environment Variables
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Open `.env` and fill in:
* `SERIAL_PORT`: Your ESP32 port (e.g. `COM3` on Windows, `/dev/ttyUSB0` on Linux)
* `TWILIO_ACCOUNT_SID`: Your Twilio Account SID (starts with `AC`)
* `TWILIO_AUTH_TOKEN`: Your Twilio Auth Token
* `TWILIO_FROM_NUMBER`: Your Twilio phone number (e.g. `+1234567890`)
* `TWILIO_TO_NUMBER`: Your phone number (e.g. `+919876543210`)

> 💡 **Note on Twilio:** If Twilio credentials are left blank, the app runs normally without crashing—it will log a clean warning and continue telemetry logging and dashboard rendering.

---

## 🔍 How to Find Your ESP32 COM Port

### On Windows
1. Press `Win + X` and select **Device Manager**.
2. Expand the **Ports (COM & LPT)** section.
3. Plug in or unplug your ESP32 board to see which port appears (e.g., `COM3`, `COM4`, `Silicon Labs CP210x USB to UART Bridge (COMx)` or `CH340 (COMx)`).
4. Set `SERIAL_PORT=COM3` in your `.env`.

### On macOS / Linux
```bash
# List available serial ports
ls /dev/tty.*
# or
ls /dev/ttyUSB* /dev/ttyACM*
```
Set `SERIAL_PORT=/dev/ttyUSB0` in your `.env`.

---

## 💻 Running the System

### Option A: Demo / Mock Mode (No Hardware Required)
Replays `mock_data.txt` continuously through the entire pipeline:
```bash
python app.py --mock
```
Open your browser at: **[http://localhost:5000](http://localhost:5000)**

### Option B: Real Hardware Mode
Connect the ESP32 to your USB port and run:
```bash
python app.py
```
Or specify a custom port / serial port on the command line:
```bash
python app.py --serial-port COM4 --port 5000
```

---

## 🌐 REST & Streaming APIs

| Endpoint | Method | Description |
|---|---|---|
| `/` | `GET` | Main responsive web dashboard |
| `/api/status` | `GET` | JSON snapshot of latest reading, system phase, and calibration values |
| `/api/events?limit=100` | `GET` | JSON array of recent sensor readings, newest first |
| `/api/stream` | `GET` | Server-Sent Events (SSE) live push stream for browsers |
| `/api/test-sms` | `POST` | Dispatches a test SMS to verify Twilio connectivity |

---

## 🛡️ Database Schema

### `events` Table
| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER | Primary Key, Auto-increment |
| `timestamp` | TEXT | ISO-8601 UTC timestamp |
| `temp` | REAL | Ambient temperature (°C) |
| `humidity` | REAL | Relative humidity (%) |
| `mq2` | REAL | MQ-2 raw ADC / sensor output |
| `baseline` | REAL | Calibrated clean-air baseline |
| `delta` | REAL | Difference from baseline (MQ2 - Baseline) |
| `temp_rate` | REAL | Temperature derivative (°C/s) |
| `mq2_rate` | REAL | Gas rate of change (ADC/s) |
| `prediction` | TEXT | `"FIRE"` or `"NOT FIRE"` |
| `confidence` | REAL | Model confidence percentage (e.g. 100.0) |
| `safety_suppressed` | INTEGER | `0` = normal, `1` = suppressed false-positive |

### `calibration` Table
| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER | Row `1` (Single active profile snapshot) |
| `baseline_mq2` | REAL | Mean clean-air MQ-2 baseline |
| `stddev` | REAL | Standard deviation (σ) |
| `threshold_delta` | REAL | Fire trigger delta threshold |
| `baseline_temp` | REAL | Baseline temperature (°C) |
| `calibrated_at` | TEXT | ISO-8601 timestamp of calibration lock |

---

## 🧪 Verification & Unit Testing

Run parser unit test:
```bash
python parser.py
```
This tests all 4 line formats and prints the parsed structured objects.
