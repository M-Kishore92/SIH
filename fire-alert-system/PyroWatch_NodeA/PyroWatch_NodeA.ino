/*
  ============================================================
  PYROWATCH  --  NODE A
  ESP32 + DHT11 + MQ-2 + SX1278 RA-02 LoRa
  ============================================================
  Sketch folder (2 files):
    PyroWatch_NodeA.ino   <- this file
    model_data.h          <- empty placeholder (not used in heuristic mode)

  LIBRARIES  (Arduino IDE -> Tools -> Manage Libraries):
    1. DHT sensor library       by Adafruit
    2. Adafruit Unified Sensor  by Adafruit
    3. LoRa                     by Sandeep Mistry

  BOARD SETTINGS:
    Board            : ESP32 Dev Module
    Partition Scheme : Default 4MB with spiffs
    Upload Speed     : 921600
    CPU Frequency    : 240MHz

  WIRING:
    RA-02 VCC  -> 3.3V   (NEVER 5V!)     DHT11 S -> GPIO 4
    RA-02 GND  -> GND                    DHT11 + -> 5V (VIN)
    RA-02 SCK  -> GPIO 18                DHT11 - -> GND
    RA-02 MISO -> GPIO 19
    RA-02 MOSI -> GPIO 23    MQ-2 AOUT -> GPIO 34
    RA-02 NSS  -> GPIO 5     MQ-2 VCC  -> 5V (VIN)
    RA-02 RST  -> GPIO 14    MQ-2 GND  -> GND
    RA-02 DIO0 -> GPIO 26
    RA-02 ANT  -> 17.3 cm wire soldered to ANT pad (REQUIRED)
  ============================================================
*/

#include <SPI.h>
#include <LoRa.h>
#include <DHT.h>

// ============================================================
//  CONFIGURATION -- edit only this section
// ============================================================
#define NODE_ID           0          // Unique ID for this node

#define DHT_PIN           4
#define DHT_TYPE          DHT11

#define MQ2_PIN           34
#define MQ2_WARMUP_MS     10000UL    // 10s for testing | use 120000UL for production

#define LORA_SS           5
#define LORA_RST          14
#define LORA_DIO0         26
#define LORA_FREQ         433E6      // 433 MHz -- must match Node B exactly
#define LORA_SF           10
#define LORA_BW           125E3
#define LORA_CR           5
#define LORA_TX_POWER     17         // dBm

#define FIRE_THRESHOLD    0.75f
#define WATCH_THRESHOLD   0.50f
#define WINDOW_SIZE       5
#define LOOP_INTERVAL_MS  1000UL     // 1 Hz transmit rate

// ============================================================
//  GLOBALS
// ============================================================
DHT dht(DHT_PIN, DHT_TYPE);

static bool loraReady = false;

// Rolling probability smoothing window
static float        probWin[WINDOW_SIZE] = {0};
static int          winIdx    = 0;
static int          winFilled = 0;

// Sensor state (for rate-of-change)
static float         prevTemp  = NAN;
static float         prevHum   = NAN;
static float         prevMQ2   = 0.0f;
static unsigned long prevTime  = 0;
static unsigned long lastDhtMs = 0;   // DHT11 requires >= 2000ms between reads

// Packet counter and diagnostic counters
static uint32_t pktId        = 0;
static uint32_t dhtOkCount   = 0;
static uint32_t dhtFailCount = 0;

// ============================================================
//  FORWARD DECLARATIONS
// ============================================================
void   initLoRa();
bool   readSensors(float &t, float &h, float &m);
void   computeRates(float t, float m, unsigned long now, float &dT, float &dM);
float  doSmooth(float p);
String getTrend();
String getDecision(float sp);
void   txLoRa(float t, float h, float m, float dT, float dM,
              float rp, float sp, const String &tr, const String &st);
void   debugPrint(float t, float h, float m, float dT, float dM,
                  float rp, float sp, const String &tr, const String &st);

// ============================================================
//  SETUP
// ============================================================
void setup() {
  Serial.begin(115200);
  delay(500);

  Serial.println(F("\n================================================"));
  Serial.println(F("  PYROWATCH NODE A  --  STARTING"));
  Serial.println(F("  Heuristic Fire Detection + LoRa TX"));
  Serial.println(F("================================================"));

  // DHT11 stabilisation
  Serial.println(F("[DHT11] Stabilising 2 s..."));
  delay(2000);
  dht.begin();
  Serial.println(F("[DHT11] Ready."));

  // MQ-2 warm-up
  Serial.printf("[MQ-2] Warming up %lu s...\n", MQ2_WARMUP_MS / 1000UL);
  unsigned long ws = millis();
  while (millis() - ws < MQ2_WARMUP_MS) {
    unsigned long rem = (MQ2_WARMUP_MS - (millis() - ws)) / 1000UL;
    Serial.printf("[MQ-2]  %lu s remaining\r", rem);
    delay(2000);
  }
  Serial.println(F("\n[MQ-2] Warm-up done."));

  // LoRa
  initLoRa();

  // Seed previous readings
  float t, h, m;
  readSensors(t, h, m);
  prevTemp = t;
  prevMQ2  = m;
  prevTime = millis();

  // Ready banner
  Serial.println(F("\n[SYSTEM] Running.\n"));
  if (!loraReady) {
    Serial.println(F("[WARN] LoRa NOT ready -- packets will NOT be transmitted!"));
    Serial.println(F("       Check: VCC->3.3V | SCK->18 | MISO->19 | MOSI->23"));
    Serial.println(F("             NSS->5 | RST->14 | DIO0->26 | Antenna attached?"));
  }
  Serial.println(F("[INFO] Mode: Heuristic (prob=0.8 if MQ2>1500 or Temp>45, else 0.1)"));
  Serial.println();
  Serial.println(F("Temp   Hum   MQ2    dT/dt   dM/dt   RawP   SmP    Trend     Status"));
  Serial.println(F("---------------------------------------------------------------------"));
}

// ============================================================
//  LOOP  --  1 Hz
// ============================================================
void loop() {
  unsigned long now = millis();

  // 1. Read sensors
  float t, h, m;
  readSensors(t, h, m);

  // 2. Rate of change
  float dT, dM;
  computeRates(t, m, now, dT, dM);

  // 3. Heuristic fire probability
  float rawProb = (m > 1500.0f || t > 45.0f) ? 0.8f : 0.1f;

  // 4. Smooth + decide
  float  sp = doSmooth(rawProb);
  String tr = getTrend();
  String st = getDecision(sp);

  // 5. Output
  debugPrint(t, h, m, dT, dM, rawProb, sp, tr, st);
  txLoRa(t, h, m, dT, dM, rawProb, sp, tr, st);

  // 6. Update state
  prevTemp = t;
  prevMQ2  = m;
  prevTime = now;
  pktId++;

  // 7. Pace to 1 Hz
  unsigned long elapsed = millis() - now;
  if (elapsed < LOOP_INTERVAL_MS) delay(LOOP_INTERVAL_MS - elapsed);
}

// ============================================================
//  initLoRa
// ============================================================
void initLoRa() {
  Serial.println(F("[LoRa] Initialising SX1278 / RA-02..."));

  // Hardware reset sequence
  pinMode(LORA_RST, OUTPUT);
  digitalWrite(LORA_RST, LOW);  delay(20);
  digitalWrite(LORA_RST, HIGH); delay(150);

  LoRa.setPins(LORA_SS, LORA_RST, LORA_DIO0);

  bool ok = false;
  for (int i = 1; i <= 5 && !ok; i++) {
    Serial.printf("[LoRa] Attempt %d/5...\n", i);
    if (LoRa.begin(LORA_FREQ)) {
      ok = true;
    } else {
      digitalWrite(LORA_RST, LOW);  delay(20);
      digitalWrite(LORA_RST, HIGH); delay(200);
    }
  }

  if (!ok) {
    loraReady = false;
    Serial.println(F("[LoRa] FAILED -- TX will be skipped every loop."));
    Serial.println(F("       Check: VCC->3.3V | SPI wiring | Antenna attached"));
    return;
  }

  LoRa.setSpreadingFactor(LORA_SF);
  LoRa.setSignalBandwidth(LORA_BW);
  LoRa.setCodingRate4(LORA_CR);
  LoRa.setTxPower(LORA_TX_POWER);
  LoRa.enableCrc();
  // Node A is TX only -- no LoRa.receive() needed here

  loraReady = true;
  Serial.printf("[LoRa] OK -- %.0f MHz | SF%d | BW %.0f kHz | CR 4/%d | %d dBm\n",
    LORA_FREQ / 1e6, LORA_SF, LORA_BW / 1e3, LORA_CR, LORA_TX_POWER);
}

// ============================================================
//  readSensors
//  DHT11 minimum read interval: 2000 ms
//  Falls back to last valid reading on failure
// ============================================================
bool readSensors(float &t, float &h, float &m) {
  unsigned long now = millis();

  if (now - lastDhtMs >= 2000UL) {
    lastDhtMs = now;

    float rawT = dht.readTemperature();
    float rawH = dht.readHumidity();

    bool valid = !isnan(rawT) && !isnan(rawH)
                 && rawT > -10.0f && rawT < 85.0f
                 && rawH >=   0.0f && rawH <= 100.0f;

    if (valid) {
      prevTemp = rawT;
      prevHum  = rawH;
      dhtOkCount++;
      Serial.printf("[DHT11] OK  T=%.1fC  H=%.0f%%  (OK:%lu  FAIL:%lu)\n",
                    rawT, rawH, dhtOkCount, dhtFailCount);
    } else {
      dhtFailCount++;
      Serial.printf("[DHT11] FAIL  rawT=%s  rawH=%s  (OK:%lu  FAIL:%lu)\n",
                    isnan(rawT) ? "NaN" : String(rawT, 1).c_str(),
                    isnan(rawH) ? "NaN" : String(rawH, 1).c_str(),
                    dhtOkCount, dhtFailCount);
      if (dhtFailCount == 1) {
        Serial.println(F("  Hint: DHT11 S->GPIO4 | +->5V(VIN) | -->GND"));
        Serial.println(F("  Bare 4-pin module? Add 10k pull-up between DATA and VCC."));
      }
    }
  }

  // Return last valid values (or safe defaults on very first call)
  t = isnan(prevTemp) ? 25.0f : prevTemp;
  h = isnan(prevHum)  ? 50.0f : prevHum;
  m = (float)analogRead(MQ2_PIN);   // 0-4095 (12-bit ADC)
  return true;
}

// ============================================================
//  computeRates
// ============================================================
void computeRates(float t, float m, unsigned long now,
                  float &dT, float &dM) {
  float dt = (float)(now - prevTime) / 1000.0f;
  if (dt < 0.001f) dt = 1.0f;   // guard against div-by-zero on first call

  if (isnan(prevTemp)) {
    dT = 0.0f;
    dM = 0.0f;
  } else {
    dT = (t - prevTemp) / dt;
    dM = (m - prevMQ2 ) / dt;
  }
}

// ============================================================
//  doSmooth  --  rolling mean
// ============================================================
float doSmooth(float p) {
  probWin[winIdx] = p;
  winIdx = (winIdx + 1) % WINDOW_SIZE;
  if (winFilled < WINDOW_SIZE) winFilled++;

  float s = 0.0f;
  for (int i = 0; i < winFilled; i++) s += probWin[i];
  return s / (float)winFilled;
}

// ============================================================
//  getTrend
// ============================================================
String getTrend() {
  if (winFilled < 2) return F("STABLE");

  int rises = 0, falls = 0;
  int oldest = (winFilled == WINDOW_SIZE) ? winIdx : 0;
  float prev = probWin[oldest % WINDOW_SIZE];

  for (int i = 1; i < winFilled; i++) {
    float cur = probWin[(oldest + i) % WINDOW_SIZE];
    if      (cur > prev + 0.01f) rises++;
    else if (cur < prev - 0.01f) falls++;
    prev = cur;
  }

  if (rises > falls) return F("RISING");
  if (falls > rises) return F("FALLING");
  return F("STABLE");
}

// ============================================================
//  getDecision
// ============================================================
String getDecision(float sp) {
  String tr = getTrend();
  if (sp > FIRE_THRESHOLD && tr == F("RISING")) return F("FIRE ALERT");
  if (sp > WATCH_THRESHOLD)                     return F("WATCH");
  return F("NORMAL");
}

// ============================================================
//  txLoRa  --  build and transmit one LoRa packet
// ============================================================
void txLoRa(float t, float h, float m, float dT, float dM,
            float rp, float sp, const String &tr, const String &st) {

  if (!loraReady) {
    Serial.println(F("  [TX] SKIP -- LoRa not ready"));
    return;
  }

  // Packet format -- must match extractField() keys in Node B exactly
  char pkt[220];
  int len = snprintf(pkt, sizeof(pkt),
    "NID:%d,PID:%lu,TS:%lu,"
    "TEMP:%.2f,HUM:%.1f,MQ2:%.0f,"
    "TROC:%+.3f,MROC:%+.1f,"
    "RAWP:%.4f,SMTHP:%.4f,"
    "TREND:%s,STATUS:%s",
    NODE_ID,
    (unsigned long)pktId,
    (unsigned long)millis(),
    t, h, m,
    dT, dM,
    rp, sp,
    tr.c_str(),
    st.c_str()
  );

  if (len <= 0 || len >= (int)sizeof(pkt)) {
    Serial.println(F("  [TX] ERROR -- packet build failed"));
    return;
  }

  if (!LoRa.beginPacket()) {
    Serial.println(F("  [TX] ERROR -- LoRa busy (beginPacket returned 0)"));
    return;
  }

  LoRa.print(pkt);
  LoRa.endPacket();   // blocking -- waits until TX is fully done

  Serial.printf("  [TX] OK -- PID:%lu  len:%d bytes\n",
                (unsigned long)pktId, len);
}

// ============================================================
//  debugPrint  --  one-line telemetry row in Serial Monitor
// ============================================================
void debugPrint(float t, float h, float m, float dT, float dM,
                float rp, float sp, const String &tr, const String &st) {
  Serial.printf(
    "%.1fC  %.0f%%  %.0f  %+.3f  %+.1f  %.3f  %.3f  %-8s  %s\n",
    t, h, m, dT, dM, rp, sp, tr.c_str(), st.c_str()
  );
}
