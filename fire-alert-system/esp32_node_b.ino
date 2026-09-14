/*
 * ==============================================================================
 * PyroWatch - Node B (ESP32 Gateway / Wi-Fi Client)
 * ==============================================================================
 * Configured for:
 *   Wi-Fi SSID  : Deepak
 *   Password    : Deepak12345
 *   Server IP   : 10.29.159.211
 *   Server Port : 5000
 *   Endpoint    : http://10.29.159.211:5000/api/sensor-data
 *   API Key     : pyrowatch123
 * ==============================================================================
 */

#include <WiFi.h>
#include <HTTPClient.h>

// --- Wi-Fi Configuration ---
const char* WIFI_SSID     = "Deepak";
const char* WIFI_PASSWORD = "Deepak12345";

// --- Flask Server Endpoint & Security ---
const char* SERVER_URL    = "http://10.29.159.211:5000/api/sensor-data";
const char* ESP32_API_KEY = "pyrowatch123";

// --- Packet Counter ---
unsigned long packetId = 0;

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("\n--- PyroWatch Node B Starting ---");

  // Connect to Wi-Fi
  connectToWiFi();
}

void loop() {
  // Ensure Wi-Fi stays connected
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[Wi-Fi] Connection lost. Reconnecting...");
    connectToWiFi();
  }

  // Sample or received telemetry data
  // (Replace these with your actual LoRa/Sensor variables if receiving from Node A)
  float temperature       = 31.4;   // In degrees Celsius
  float humidity          = 58.0;   // Relative Humidity %
  int mq2Gas              = 428;    // MQ-2 ADC reading / ppm
  float riskProbability   = 0.15;   // 0.0 to 1.0 (ML fire risk output)
  const char* status      = (riskProbability > 0.70) ? "FIRE" : "NORMAL";
  const char* trend       = "STABLE";

  packetId++;

  // Send data to Flask server
  sendSensorData(temperature, humidity, mq2Gas, riskProbability, status, trend);

  // Send interval (e.g., every 3 seconds)
  delay(3000);
}

void connectToWiFi() {
  Serial.print("Connecting to WiFi: ");
  Serial.println(WIFI_SSID);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 30) {
    delay(500);
    Serial.print(".");
    attempts++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\nWiFi connected!");
    Serial.print("ESP32 IP Address: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("\nFailed to connect to WiFi. Will retry...");
  }
}

void sendSensorData(float temp, float hum, int mq2, float risk, const char* status, const char* trend) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[HTTP] Cannot send: Wi-Fi not connected.");
    return;
  }

  HTTPClient http;
  http.begin(SERVER_URL);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-ESP32-API-Key", ESP32_API_KEY);

  // Build JSON payload string
  String jsonPayload = "{";
  jsonPayload += "\"node_id\":\"NODE_B\",";
  jsonPayload += "\"packet_id\":" + String(packetId) + ",";
  jsonPayload += "\"temperature\":" + String(temp, 1) + ",";
  jsonPayload += "\"humidity\":" + String(hum, 1) + ",";
  jsonPayload += "\"mq2\":" + String(mq2) + ",";
  jsonPayload += "\"risk_probability\":" + String(risk, 2) + ",";
  jsonPayload += "\"status\":\"" + String(status) + "\",";
  jsonPayload += "\"trend\":\"" + String(trend) + "\"";
  jsonPayload += "}";

  Serial.println("\n[HTTP] Sending to " + String(SERVER_URL) + "...");
  Serial.println("[HTTP] Payload: " + jsonPayload);

  int httpResponseCode = http.POST(jsonPayload);

  if (httpResponseCode > 0) {
    Serial.print("[HTTP] Response Code: ");
    Serial.println(httpResponseCode);
    String response = http.getString();
    Serial.print("[HTTP] Server Response: ");
    Serial.println(response);
  } else {
    Serial.print("[HTTP] POST failed, error: ");
    Serial.println(http.errorToString(httpResponseCode).c_str());
  }

  http.end();
}
