/*
 * =============================================================================
 *  Landslide_NodeB.ino
 *  SIH Research Project -- Landslide Early Warning Edge-Cloud Gateway (Node B)
 *  Author    : Embedded Systems & Edge-Cloud Integration Engineer
 *  Target MCU: ESP32 Dev Module (Xtensa LX6 @ 240 MHz, 4 MB Flash)
 *  Upgraded from: PyroWatch_NodeB.ino (fire detection gateway paradigm)
 *
 *  GATEWAY PIPELINE ARCHITECTURE
 *  ──────────────────────────────────────────────────────────────────────────
 *                   COMMUNICATION LAYER (LORA SX1278)
 *           ┌──────────────────────────────────────────────┐
 *           │   SX1278 LoRa RX (433 MHz, SF9, BW=62.5kHz)   │
 *           │           Non-blocking parsePacket()         │
 *           └──────────────────────┬───────────────────────┘
 *                                  │
 *                                  ▼
 *                         PARSING & EXTRACTION
 *           ┌──────────────────────────────────────────────┐
 *           │ • Extract String: "NID:0,PID:12,TS:...,etc."  │
 *           │ • Extract RF Metrics: RSSI (dBm) & SNR (dB)  │
 *           │ • Filter by Expected Node ID (NID == 0)      │
 *           │ • Flash Onboard LED (GPIO 2)                 │
 *           └──────────────────────┬───────────────────────┘
 *                                  │
 *                                  ▼
 *                        DATA TRANSLATION LAYER
 *           ┌──────────────────────────────────────────────┐
 *           │ Convert parsed telemetry into strictly-typed  │
 *           │ standard JSON payload                        │
 *           └──────────────────────┬───────────────────────┘
 *                                  │
 *                                  ▼
 *                        NETWORK & CLOUD LAYER
 *     ┌────────────────────────────┴────────────────────────────┐
 *     │                                                         │
 *     ▼                                                         ▼
 * Serial Monitor Diagnostics                         ESP32 Wi-Fi HTTP Client
 * (115200 Baud Terminal Dashboard)             (Non-blocking Auto-Reconnect)
 *                                                               │
 *                                                               ▼
 *                                                      Flask / Cloud Web API
 *                                                   POST http://<IP>:5000/api/sensor-data
 *
 *  REQUIRED LIBRARIES (install via Arduino Library Manager):
 *  ─────────────────────────────────────────────────────────
 *   - LoRa by Sandeep Mistry  (v0.8.0+)
 *   - WiFi.h                  (ESP32 built-in)
 *   - HTTPClient.h            (ESP32 built-in)
 *   - SPI.h                   (ESP32 built-in)
 * =============================================================================
 */

// =============================================================================
//  INCLUDES
// =============================================================================
#include <WiFi.h>
#include <HTTPClient.h>
#include <SPI.h>
#include <LoRa.h>
#include "config.h"

// =============================================================================
//  GLOBAL STATE VARIABLES
// =============================================================================
static unsigned long g_lastWifiRetryMs    = 0;
static unsigned long g_ledOffTimeMs       = 0;
static bool          g_ledActive          = false;
static unsigned long g_totalPacketsRx     = 0;
static unsigned long g_totalPacketsFwd    = 0;
static unsigned long g_totalFwdFails      = 0;
static unsigned long g_filteredPackets    = 0;

// Reconstructed Server URL
char g_serverUrl[128];

// =============================================================================
//  FORWARD DECLARATIONS
// =============================================================================
void initHardware();
void initLoRaRadio();
void connectWiFi();
void maintainWiFiAsync();
void processLoRaReception();
bool parseLandslideTelemetry(const String& rawStr, LandslidePacket& pkt);
void forwardTelemetryToCloud(const LandslidePacket& pkt);
void printDashboard(const LandslidePacket& pkt, int httpResult);
void triggerLedPulse();
void updateLedAsync();

// =============================================================================
//  SETUP ROUTINE
// =============================================================================
void setup() {
    Serial.begin(SERIAL_BAUD);
    delay(1000);  // Allow USB serial bridge to enumerate

    Serial.println();
    Serial.println(F("========================================================================"));
    Serial.println(F("  SIH LANDSLIDE EARLY DETECTION SYSTEM -- GATEWAY NODE (NODE B)        "));
    Serial.println(F("  Role   : LoRa SX1278 Telemetry Receiver & Edge-Cloud Wi-Fi Bridge     "));
    Serial.println(F("  Target : ESP32 Dev Module (240 MHz) | Carrier: 433 MHz (SF9/BW 62.5k) "));
    Serial.println(F("========================================================================"));

    // Construct full HTTP endpoint URL
    snprintf(g_serverUrl, sizeof(g_serverUrl), "http://%s:%u%s",
             SERVER_IP, SERVER_PORT, API_ENDPOINT);
    Serial.printf("[INIT] Cloud target: %s (API-Key: %s)\n", g_serverUrl, API_KEY);

    // Initialise Pins, SPI, and SX1278
    initHardware();
    initLoRaRadio();

    // Begin initial non-blocking Wi-Fi connection
    connectWiFi();

    Serial.println(F("[INIT] Node B Gateway online and actively listening for LoRa packets...\n"));
}

// =============================================================================
//  MAIN EXECUTION LOOP (100% Non-Blocking)
// =============================================================================
void loop() {
    // 1. Manage asynchronous Wi-Fi link without halting LoRa processing
    maintainWiFiAsync();

    // 2. Poll LoRa radio for incoming packets
    processLoRaReception();

    // 3. Update non-blocking packet reception LED pulse
    updateLedAsync();
}

// =============================================================================
//  HARDWARE INITIALISATION
// =============================================================================
void initHardware() {
    pinMode(LED_PIN, OUTPUT);
    digitalWrite(LED_PIN, LOW);

    // Explicit VSPI setup for ESP32
    SPI.begin(LORA_SCK_PIN, LORA_MISO_PIN, LORA_MOSI_PIN, LORA_NSS_PIN);
    Serial.println(F("[INIT] SPI Bus configured: SCK=18, MISO=19, MOSI=23, NSS=5"));
}

/**
 * @brief  Initialise and configure SX1278 radio to match Node A physical profile.
 */
void initLoRaRadio() {
    LoRa.setPins(LORA_NSS_PIN, LORA_RST_PIN, LORA_DIO0_PIN);

    Serial.print(F("[LoRa] Initialising SX1278 on 433 MHz... "));
    if (!LoRa.begin(LORA_BAND_FREQ)) {
        Serial.println(F("FAILED!"));
        Serial.println(F("[LoRa] CRITICAL: Check SX1278 wiring, power, and SPI pins."));
        Serial.println(F("[LoRa] Gateway halted. Reset board to retry."));
        while (true) {
            // Rapid error strobe
            digitalWrite(LED_PIN, HIGH);
            delay(100);
            digitalWrite(LED_PIN, LOW);
            delay(100);
        }
    }
    Serial.println(F("SUCCESS."));

    // Configure exact physical layer parameters matching Node A
    LoRa.setFrequency(LORA_BAND_FREQ);
    LoRa.setSpreadingFactor(LORA_SPREADING_FACTOR);
    LoRa.setSignalBandwidth(LORA_BANDWIDTH);
    LoRa.setCodingRate4(LORA_CODING_RATE);
    LoRa.setPreambleLength(LORA_PREAMBLE_LENGTH);
    LoRa.enableCrc();

    Serial.println(F("[LoRa] Radio synced: 433.0 MHz | SF9 | BW 62.5 kHz | CR 4/5 | CRC ON"));
}

// =============================================================================
//  NETWORK MANAGEMENT (Non-Blocking Wi-Fi Manager)
// =============================================================================

/**
 * @brief  Begin Wi-Fi connection in Station mode.
 */
void connectWiFi() {
    WiFi.mode(WIFI_STA);
    WiFi.disconnect();
    delay(100);

    Serial.printf("[WIFI] Connecting to 2.4 GHz SSID: '%s' ...\n", WIFI_SSID);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    g_lastWifiRetryMs = millis();
}

/**
 * @brief  Asynchronous Wi-Fi connection monitor.
 *         Periodically verifies connection and triggers re-association every 10s
 *         without using blocking delays.
 */
void maintainWiFiAsync() {
    static wl_status_t lastReportedStatus = WL_IDLE_STATUS;
    wl_status_t currentStatus = WiFi.status();

    // Log connection status change
    if (currentStatus != lastReportedStatus) {
        if (currentStatus == WL_CONNECTED) {
            Serial.println();
            Serial.println(F("────────────────────────────────────────────────────────────────────────"));
            Serial.printf("[WIFI] CONNECTED! IP: %s | RSSI: %d dBm | Gateway: %s\n",
                          WiFi.localIP().toString().c_str(),
                          WiFi.RSSI(),
                          WiFi.gatewayIP().toString().c_str());
            Serial.println(F("────────────────────────────────────────────────────────────────────────"));
        } else if (lastReportedStatus == WL_CONNECTED) {
            Serial.println(F("[WIFI] WARNING: Connection lost. Entering auto-reconnect cycle..."));
        }
        lastReportedStatus = currentStatus;
    }

    // Attempt reconnect if offline and interval elapsed
    if (currentStatus != WL_CONNECTED) {
        unsigned long now = millis();
        if (now - g_lastWifiRetryMs >= WIFI_RECONNECT_INTERVAL) {
            g_lastWifiRetryMs = now;
            Serial.printf("[WIFI] Still disconnected (status %d). Re-initiating WiFi.begin() ...\n", currentStatus);
            WiFi.disconnect();
            WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
        }
    }
}

// =============================================================================
//  LORA RECEPTION & TELEMETRY PARSING
// =============================================================================

/**
 * @brief  Polls the LoRa transceiver for incoming packets and processes them.
 */
void processLoRaReception() {
    int packetSize = LoRa.parsePacket();
    if (packetSize == 0) {
        return;  // No packet available
    }

    // Packet arrived! Flash onboard LED indicator immediately
    triggerLedPulse();
    g_totalPacketsRx++;

    // Read full packet payload into a String
    String rawPayload = "";
    rawPayload.reserve(packetSize + 1);
    while (LoRa.available()) {
        rawPayload += (char)LoRa.read();
    }

    // Capture physical layer signal quality metrics
    int   rssi = LoRa.packetRssi();
    float snr  = LoRa.packetSnr();

    // Parse the comma-separated key:value payload
    LandslidePacket pkt;
    pkt.rssi = rssi;
    pkt.snr  = snr;

    bool parseOk = parseLandslideTelemetry(rawPayload, pkt);

    if (!parseOk) {
        Serial.println(F("[LoRa] ERROR: Malformed packet received:"));
        Serial.printf("       Raw: %s\n", rawPayload.c_str());
        return;
    }

    // Enforce Node ID filtering: Ignore packets not matching expected Node A (0)
    if (pkt.nodeId != EXPECTED_NODE_ID) {
        g_filteredPackets++;
        Serial.printf("[LoRa] FILTERED: Packet ignored from foreign Node ID: %d (Expected: %d)\n",
                      pkt.nodeId, EXPECTED_NODE_ID);
        return;
    }

    // Forward telemetry to cloud over HTTP POST
    forwardTelemetryToCloud(pkt);
}

/**
 * @brief  Parses raw packet formatted as:
 *         "NID:0,PID:12,TS:1234,SOIL:3200.5,TILT:12.400,VIB:1.0500,
 *          SROC:45.200,TROC:0.8000,RAWP:0.88,SMTHP:0.82,TREND:RISING,STATUS:LANDSLIDE ALERT"
 *
 * @param  rawStr String containing incoming packet
 * @param  pkt    Output LandslidePacket struct
 * @return true on valid parse of all required fields, false otherwise
 */
bool parseLandslideTelemetry(const String& rawStr, LandslidePacket& pkt) {
    if (rawStr.length() < 16) return false;

    // Default structure initialization
    pkt.nodeId       = -1;
    pkt.packetId     = 0;
    pkt.timestampMs  = 0;
    pkt.soilMoisture = 0.0f;
    pkt.tiltAngle    = 0.0f;
    pkt.vibration    = 0.0f;
    pkt.soilRoC      = 0.0f;
    pkt.tiltRoC      = 0.0f;
    pkt.rawProb      = 0.0f;
    pkt.smoothedProb = 0.0f;
    strncpy(pkt.trend, "UNKNOWN", sizeof(pkt.trend) - 1);
    strncpy(pkt.status, "UNKNOWN", sizeof(pkt.status) - 1);

    uint8_t fieldsFound = 0;
    int startIdx = 0;
    int len = rawStr.length();

    while (startIdx < len) {
        int commaIdx = rawStr.indexOf(',', startIdx);
        if (commaIdx == -1) commaIdx = len;

        String token = rawStr.substring(startIdx, commaIdx);
        token.trim();

        int colonIdx = token.indexOf(':');
        if (colonIdx > 0) {
            String key = token.substring(0, colonIdx);
            String val = token.substring(colonIdx + 1);
            key.trim();
            val.trim();

            if (key.equalsIgnoreCase("NID")) {
                pkt.nodeId = val.toInt();
                fieldsFound++;
            } else if (key.equalsIgnoreCase("PID")) {
                pkt.packetId = (unsigned long)val.toInt();
                fieldsFound++;
            } else if (key.equalsIgnoreCase("TS")) {
                pkt.timestampMs = (unsigned long)val.toInt();
                fieldsFound++;
            } else if (key.equalsIgnoreCase("SOIL")) {
                pkt.soilMoisture = val.toFloat();
                fieldsFound++;
            } else if (key.equalsIgnoreCase("TILT")) {
                pkt.tiltAngle = val.toFloat();
                fieldsFound++;
            } else if (key.equalsIgnoreCase("VIB")) {
                pkt.vibration = val.toFloat();
                fieldsFound++;
            } else if (key.equalsIgnoreCase("SROC")) {
                pkt.soilRoC = val.toFloat();
                fieldsFound++;
            } else if (key.equalsIgnoreCase("TROC")) {
                pkt.tiltRoC = val.toFloat();
                fieldsFound++;
            } else if (key.equalsIgnoreCase("RAWP")) {
                pkt.rawProb = val.toFloat();
                fieldsFound++;
            } else if (key.equalsIgnoreCase("SMTHP")) {
                pkt.smoothedProb = val.toFloat();
                fieldsFound++;
            } else if (key.equalsIgnoreCase("TREND")) {
                strncpy(pkt.trend, val.c_str(), sizeof(pkt.trend) - 1);
                pkt.trend[sizeof(pkt.trend) - 1] = '\0';
                fieldsFound++;
            } else if (key.equalsIgnoreCase("STATUS")) {
                strncpy(pkt.status, val.c_str(), sizeof(pkt.status) - 1);
                pkt.status[sizeof(pkt.status) - 1] = '\0';
                fieldsFound++;
            }
        }
        startIdx = commaIdx + 1;
    }

    // All 12 mandatory telemetry tokens must be parsed
    return (fieldsFound >= 12 && pkt.nodeId >= 0);
}

// =============================================================================
//  CLOUD FORWARDING (HTTPClient POST)
// =============================================================================

/**
 * @brief  Serialises telemetry to JSON and dispatches an HTTP POST request
 *         to http://<SERVER_IP>:5000/api/sensor-data with timeout protection.
 */
void forwardTelemetryToCloud(const LandslidePacket& pkt) {
    int httpResponseCode = 0;

    if (WiFi.status() == WL_CONNECTED) {
        HTTPClient http;
        http.begin(g_serverUrl);

        // Required headers
        http.addHeader("Content-Type", "application/json");
        http.addHeader("X-ESP32-API-Key", API_KEY);

        // CRITICAL: Prevent network latency from blocking the main LoRa listening loop
        http.setTimeout(HTTP_TIMEOUT_MS);

        // Construct JSON Payload matching exact schema requirement
        char jsonBuffer[512];
        snprintf(jsonBuffer, sizeof(jsonBuffer),
            "{\n"
            "  \"node_id\": \"NODE_B\",\n"
            "  \"packet_id\": %lu,\n"
            "  \"node_ts\": %lu,\n"
            "  \"soil_moisture\": %.1f,\n"
            "  \"tilt_angle\": %.2f,\n"
            "  \"vibration\": %.4f,\n"
            "  \"soil_rate\": %.2f,\n"
            "  \"tilt_rate\": %.4f,\n"
            "  \"raw_probability\": %.2f,\n"
            "  \"smoothed_probability\": %.2f,\n"
            "  \"trend\": \"%s\",\n"
            "  \"status\": \"%s\",\n"
            "  \"rssi\": %d,\n"
            "  \"snr\": %.1f\n"
            "}",
            pkt.packetId,
            pkt.timestampMs,
            pkt.soilMoisture,
            pkt.tiltAngle,
            pkt.vibration,
            pkt.soilRoC,
            pkt.tiltRoC,
            pkt.rawProb,
            pkt.smoothedProb,
            pkt.trend,
            pkt.status,
            pkt.rssi,
            pkt.snr
        );

        // Send HTTP POST
        httpResponseCode = http.POST((uint8_t*)jsonBuffer, strlen(jsonBuffer));

        if (httpResponseCode > 0) {
            g_totalPacketsFwd++;
        } else {
            g_totalFwdFails++;
            Serial.printf("[HTTP] POST failed, error: %s (code: %d)\n",
                          http.errorToString(httpResponseCode).c_str(),
                          httpResponseCode);
        }

        http.end();
    } else {
        httpResponseCode = -99;  // Custom code representing Wi-Fi offline
        g_totalFwdFails++;
    }

    // Display formatted dashboard for each parsed packet
    printDashboard(pkt, httpResponseCode);
}

// =============================================================================
//  SERIAL DASHBOARD OUTPUT
// =============================================================================

/**
 * @brief  Prints a neat, human-readable terminal dashboard to Serial Monitor.
 */
void printDashboard(const LandslidePacket& pkt, int httpResult) {
    const char* alertTag = "  NORMAL  ";
    if (strcmp(pkt.status, "LANDSLIDE ALERT") == 0) {
        alertTag = "!! ALERT !!";
    } else if (strcmp(pkt.status, "WARNING") == 0) {
        alertTag = ">> WARNING ";
    }

    Serial.println();
    Serial.println(F("┌──────────────────────────────────────────────────────────────────────┐"));
    Serial.printf( "│  LANDSLIDE TELEMETRY PACKET #%-6lu           [%s]          │\n",
                  pkt.packetId, alertTag);
    Serial.println(F("├──────────────────────────────────────────────────────────────────────┤"));
    Serial.printf( "│  Node ID      : %-8d          Node Timestamp : %-10lu ms      │\n",
                  pkt.nodeId, pkt.timestampMs);
    Serial.printf( "│  LoRa RSSI    : %-4d dBm         LoRa SNR       : %+5.1f dB           │\n",
                  pkt.rssi, pkt.snr);
    Serial.println(F("├──────────────────────────────────────────────────────────────────────┤"));
    Serial.printf( "│  Soil Moisture: %-6.1f ADC       Soil Rate (RoC): %+7.2f ADC/s       │\n",
                  pkt.soilMoisture, pkt.soilRoC);
    Serial.printf( "│  Tilt Pitch   : %+6.2f deg       Tilt Rate (RoC): %+7.4f deg/s       │\n",
                  pkt.tiltAngle, pkt.tiltRoC);
    Serial.printf( "│  Vibration Mag: %-6.4f g         Trend Vector   : %-16s │\n",
                  pkt.vibration, pkt.trend);
    Serial.println(F("├──────────────────────────────────────────────────────────────────────┤"));
    Serial.printf( "│  AI Inference : P_raw=%.2f  │  P_smoothed=%.2f  │  Risk: %-15s│\n",
                  pkt.rawProb, pkt.smoothedProb, pkt.status);
    Serial.println(F("├──────────────────────────────────────────────────────────────────────┤"));

    if (httpResult > 0) {
        Serial.printf( "│  Cloud Forward: HTTP %-3d SUCCESS  │  Fwd Count: %-5lu   Failures: %-4lu │\n",
                      httpResult, g_totalPacketsFwd, g_totalFwdFails);
    } else if (httpResult == -99) {
        Serial.printf( "│  Cloud Forward: SKIPPED (Wi-Fi Offline) │  Pending Retries: %-8lu │\n",
                      g_totalFwdFails);
    } else {
        Serial.printf( "│  Cloud Forward: FAILED (Err %-4d) │  Total Failures: %-16lu│\n",
                      httpResult, g_totalFwdFails);
    }
    Serial.println(F("└──────────────────────────────────────────────────────────────────────┘"));
}

// =============================================================================
//  STATUS LED INDICATOR (Non-Blocking Pulse)
// =============================================================================

/**
 * @brief  Triggers non-blocking LED pulse on packet arrival.
 */
void triggerLedPulse() {
    digitalWrite(LED_PIN, HIGH);
    g_ledActive = true;
    g_ledOffTimeMs = millis() + LED_PULSE_DURATION_MS;
}

/**
 * @brief  Turns off LED once pulse duration expires without using delay().
 */
void updateLedAsync() {
    if (g_ledActive && (millis() >= g_ledOffTimeMs)) {
        digitalWrite(LED_PIN, LOW);
        g_ledActive = false;
    }
}
