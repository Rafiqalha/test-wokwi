#include <WiFi.h>
#include <HTTPClient.h>
#include <PubSubClient.h>
#include <DHTesp.h>
#include <esp_system.h>

// Change to 0 for the HTTP run, then restart the simulation.
// 1 = changing synthetic readings from the ESP32 node; 0 = physical/virtual DHT22.
#define RANDOM_SENSOR_MODE 1
#define USE_MQTT 1

const char* DEVICE_ID = "esp32-01";
const char* LAPTOP_HOST = "host.wokwi.internal";
const char* MQTT_TOPIC = "sensor/dht22";
const char* MQTT_ACK_TOPIC = "sensor/ack/esp32-01";
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
  if (strcmp(topic, MQTT_ACK_TOPIC) != 0 || length >= 160) return;
  char reply[160];
  memcpy(reply, data, length);
  reply[length] = '\0';
  // Ack contains the same sequence number as the published message.
  char expected[32];
  snprintf(expected, sizeof(expected), "\"seq\":%lu,", (unsigned long)pendingSequence);
  if (awaitingAck && strstr(reply, expected) != nullptr) {
    Serial.printf("MQTT diterima laptop: seq=%lu, RTT=%lu ms, ack=%s\n",
                  (unsigned long)pendingSequence,
                  (unsigned long)(millis() - pendingStartedAt), reply);
    awaitingAck = false;
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
  char payload[160];
  snprintf(payload, sizeof(payload),
           "{\"device_id\":\"%s\",\"seq\":%lu,\"suhu\":%.1f,\"kelembapan\":%.1f,\"sent_ms\":%lu}",
           DEVICE_ID, (unsigned long)sequenceNumber, temperature,
           humidity, (unsigned long)millis());
  Serial.printf("Kirim %s: %s\n", USE_MQTT ? "MQTT" : "HTTP", payload);

#if USE_MQTT
  pendingSequence = sequenceNumber;
  pendingStartedAt = millis();
  awaitingAck = true;
  if (!mqtt.publish(MQTT_TOPIC, payload)) {
    awaitingAck = false;
    Serial.println("MQTT publish gagal");
  }
#else
  HTTPClient http;
  String url = String("http://") + LAPTOP_HOST + ":" + HTTP_PORT + "/update";
  if (!http.begin(url)) {
    Serial.println("HTTP begin gagal");
    return;
  }
  http.addHeader("Content-Type", "application/json");
  uint32_t startedAt = millis();
  int status = http.POST((uint8_t*)payload, strlen(payload));
  uint32_t rtt = millis() - startedAt;
  Serial.printf("HTTP status=%d, RTT=%lu ms, respons=%s\n",
                status, (unsigned long)rtt, http.getString().c_str());
  http.end();
#endif
}
