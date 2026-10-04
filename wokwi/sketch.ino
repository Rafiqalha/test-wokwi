#include <WiFi.h>
#include <HTTPClient.h>
#include <PubSubClient.h>
#include <DHTesp.h>
#include <esp_system.h>

// Change to 0 for the HTTP run, then restart the simulation.
// 1 = changing synthetic readings from the ESP32 node; 0 = physical/virtual DHT22.
#define RANDOM_SENSOR_MODE 0
#define USE_MQTT 1

const char* DEVICE_ID = "esp32-01";
const char* LAPTOP_HOST = "host.wokwi.internal";
const char* MQTT_TOPIC = "sensor/dht22";
const char* MQTT_ACK_TOPIC = "sensor/ack/esp32-01";
const char* MQTT_METRICS_TOPIC = "sensor/metrics";
#if RANDOM_SENSOR_MODE
const char* SOURCE_MODE = "random";
#else
const char* SOURCE_MODE = "dht22";
#endif
const uint16_t MQTT_PORT = 1883;
const uint16_t HTTP_PORT = 5000;
const uint32_t INTERVAL_MS = 2000;
const uint32_t ACK_TIMEOUT_MS = 5000;

DHTesp dht;
WiFiClient wifiClient;
PubSubClient mqtt(wifiClient);
uint32_t nextSendAt = 0;
uint32_t sequenceNumber = 0;
uint32_t pendingSequence = 0;
uint32_t pendingStartedAt = 0;
bool awaitingAck = false;
float simulatedTemperature = 27.5f;
float simulatedHumidity = 66.0f;
char sessionId[17];

bool matchesAck(String reply, uint32_t seq) {
  // Both server routes return these exact JSON fields. Ignore formatting spaces.
  reply.replace(" ", ""); reply.replace("\n", ""); reply.replace("\r", "");
  String sequence = String("\"seq\":") + String(seq);
  String session = String("\"session_id\":\"") + sessionId + "\"";
  return reply.indexOf(session) >= 0 &&
         (reply.indexOf(sequence + ",") >= 0 || reply.indexOf(sequence + "}") >= 0);
}

void reportMetric(uint32_t seq, const char* protocol, const char* status, uint32_t rtt) {
  char metric[256];
  char duration[16];
  if (strcmp(status, "ok") == 0) snprintf(duration, sizeof(duration), "%lu", (unsigned long)rtt);
  else strcpy(duration, "null");
  snprintf(metric, sizeof(metric),
      "{\"device_id\":\"%s\",\"session_id\":\"%s\",\"source_mode\":\"%s\","
      "\"seq\":%lu,\"protokol\":\"%s\",\"status\":\"%s\",\"rtt_ms\":%s}",
      DEVICE_ID, sessionId, SOURCE_MODE, (unsigned long)seq, protocol, status, duration);
#if USE_MQTT
  if (!mqtt.publish(MQTT_METRICS_TOPIC, metric)) Serial.println("Laporan RTT MQTT gagal dikirim");
#else
  HTTPClient reporter;
  reporter.setConnectTimeout(5000);
  reporter.setTimeout(5000);
  String endpoint = String("http://") + LAPTOP_HOST + ":" + HTTP_PORT + "/metrics";
  if (!reporter.begin(endpoint)) { Serial.println("Laporan RTT HTTP gagal dimulai"); return; }
  reporter.addHeader("Content-Type", "application/json");
  int code = reporter.POST((uint8_t*)metric, strlen(metric));
  if (code != 200) Serial.printf("Laporan RTT HTTP gagal: %d\n", code);
  reporter.end();
#endif
}

bool readSensor(float& temperature, float& humidity) {
#if RANDOM_SENSOR_MODE
  // Random walk keeps the live chart moving without browser-generated data.
  simulatedTemperature = constrain(simulatedTemperature + random(-6, 7) / 10.0f,
                                   24.0f, 34.0f);
  simulatedHumidity = constrain(simulatedHumidity + random(-13, 14) / 10.0f,
                                48.0f, 84.0f);
  temperature = simulatedTemperature;
  humidity = simulatedHumidity;
  return true;
#else
  TempAndHumidity reading = dht.getTempAndHumidity();
  temperature = reading.temperature;
  humidity = reading.humidity;
  return !isnan(temperature) && !isnan(humidity);
#endif
}

void onMqttMessage(char* topic, byte* data, unsigned int length) {
  if (strcmp(topic, MQTT_ACK_TOPIC) != 0 || length >= 256) return;
  char reply[256];
  memcpy(reply, data, length);
  reply[length] = '\0';
  // Ack contains the same sequence number as the published message.
  if (awaitingAck && matchesAck(String(reply), pendingSequence)) {
    uint32_t rtt = millis() - pendingStartedAt;
    Serial.printf("MQTT diterima laptop: seq=%lu, RTT=%lu ms, ack=%s\n",
                  (unsigned long)pendingSequence,
                  (unsigned long)rtt, reply);
    awaitingAck = false;
    reportMetric(pendingSequence, "MQTT", "ok", rtt);
  }
}

void connectWifi() {
  if (WiFi.status() == WL_CONNECTED) return;
  Serial.print("Menghubungkan WiFi");
  WiFi.begin("Wokwi-GUEST", "", 6);
  while (WiFi.status() != WL_CONNECTED) {
    delay(250);
    Serial.print(".");
  }
  Serial.println(" tersambung");
}

void connectMqtt() {
  if (mqtt.connected()) return;
  mqtt.setServer(LAPTOP_HOST, MQTT_PORT);
  mqtt.setCallback(onMqttMessage);
  while (!mqtt.connected()) {
    if (WiFi.status() != WL_CONNECTED) connectWifi();
    Serial.print("Menghubungkan broker lokal...");
    if (mqtt.connect(DEVICE_ID)) {
      mqtt.subscribe(MQTT_ACK_TOPIC);
      Serial.println(" tersambung");
    } else {
      Serial.printf(" gagal (%d), coba lagi\n", mqtt.state());
      delay(2000);
    }
  }
}

void setup() {
  Serial.begin(115200);
  snprintf(sessionId, sizeof(sessionId), "%08lx%08lx",
           (unsigned long)esp_random(), (unsigned long)esp_random());
  Serial.printf("Sesi: %s\n", sessionId);
  mqtt.setBufferSize(512);
#if RANDOM_SENSOR_MODE
  randomSeed(esp_random());
  Serial.println("Mode sensor: simulasi acak dari node ESP32, interval 2 detik");
#else
  dht.setup(15, DHTesp::DHT22);
  Serial.println("Mode sensor: DHT22");
#endif
  connectWifi();
#if USE_MQTT
  connectMqtt();
#endif
  nextSendAt = millis();
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) connectWifi();
#if USE_MQTT
  if (!mqtt.connected()) connectMqtt();
  mqtt.loop();
  if (awaitingAck && millis() - pendingStartedAt > ACK_TIMEOUT_MS) {
    Serial.printf("MQTT ack timeout: seq=%lu\n", (unsigned long)pendingSequence);
    awaitingAck = false;
    reportMetric(pendingSequence, "MQTT", "timeout", 0);
  }
#endif
  if ((int32_t)(millis() - nextSendAt) < 0) return;
  nextSendAt = millis() + INTERVAL_MS;
#if USE_MQTT
  if (awaitingAck) return;
#endif

  float temperature;
  float humidity;
  if (!readSensor(temperature, humidity)) {
    Serial.println("DHT22 gagal dibaca");
    return;
  }
  sequenceNumber++;
  char payload[256];
  snprintf(payload, sizeof(payload),
           "{\"device_id\":\"%s\",\"session_id\":\"%s\",\"source_mode\":\"%s\","
           "\"seq\":%lu,\"suhu\":%.1f,\"kelembapan\":%.1f,\"sent_ms\":%lu}",
           DEVICE_ID, sessionId, SOURCE_MODE, (unsigned long)sequenceNumber, temperature,
           humidity, (unsigned long)millis());
  Serial.printf("Kirim %s: %s\n", USE_MQTT ? "MQTT" : "HTTP", payload);

#if USE_MQTT
  pendingSequence = sequenceNumber;
  pendingStartedAt = millis();
  awaitingAck = true;
  if (!mqtt.publish(MQTT_TOPIC, payload)) {
    awaitingAck = false;
    Serial.println("MQTT publish gagal");
    reportMetric(sequenceNumber, "MQTT", "error", 0);
  }
#else
  HTTPClient http;
  http.setConnectTimeout(5000);
  http.setTimeout(5000);
  String url = String("http://") + LAPTOP_HOST + ":" + HTTP_PORT + "/update";
  if (!http.begin(url)) {
    Serial.println("HTTP begin gagal");
    reportMetric(sequenceNumber, "HTTP", "error", 0);
    return;
  }
  http.addHeader("Content-Type", "application/json");
  uint32_t startedAt = millis();
  int status = http.POST((uint8_t*)payload, strlen(payload));
  String reply = http.getString();
  uint32_t rtt = millis() - startedAt;
  Serial.printf("HTTP status=%d, RTT=%lu ms, respons=%s\n",
                status, (unsigned long)rtt, reply.c_str());
  http.end();
  if (status == 200 && matchesAck(reply, sequenceNumber)) reportMetric(sequenceNumber, "HTTP", "ok", rtt);
  else reportMetric(sequenceNumber, "HTTP", status == HTTPC_ERROR_READ_TIMEOUT ? "timeout" : "error", 0);
#endif
}
