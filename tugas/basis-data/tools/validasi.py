"""Uji payload salah terhadap receiver yang sedang berjalan (HTTP dan MQTT nyata).

Pemakaian: python validasi.py <data/sensor.db> <keluaran.json>
Menambahkan perangkat esp32-02 berstatus nonaktif untuk menguji penolakan identitas.
"""

import json
import sqlite3
import sys
import threading
import time

import paho.mqtt.client as mqtt
import requests

URL = "http://127.0.0.1:5000/update"
db_path, output = sys.argv[1], sys.argv[2]


def count():
    with sqlite3.connect(db_path) as conn:
        return conn.execute("select count(*) from readings").fetchone()[0]


with sqlite3.connect(db_path) as conn:
    conn.execute("""insert or ignore into devices (device_id, nama, lokasi, status)
        values ('esp32-02', 'ESP32 cadangan', 'Gudang', 'nonaktif')""")
    row = conn.execute("""select device_id, session_id, source_mode, seq, suhu, kelembapan, sent_ms, event_id
        from readings where protokol = 'HTTP' and session_id is not null
        order by waktu_diterima desc limit 1""").fetchone()
conn.close()
stored = dict(zip(("device_id", "session_id", "source_mode", "seq", "suhu", "kelembapan", "sent_ms"), row[:7]))
base = {"device_id": "esp32-01", "session_id": "uji-validasi", "source_mode": "dht22",
        "seq": 900, "suhu": 28.5, "kelembapan": 65.0, "sent_ms": 1000}
cases = [
    ("Suhu berupa teks", {**base, "suhu": "28.5"}, 400),
    ("Suhu di luar rentang DHT22 (200 °C)", {**base, "suhu": 200}, 400),
    ("Kelembapan di atas 100 %", {**base, "kelembapan": 101}, 400),
    ("Field suhu hilang", {k: v for k, v in base.items() if k != "suhu"}, 400),
    ("Field asing ditambahkan", {**base, "baterai": 3.7}, 400),
    ("device_id mengandung spasi", {**base, "device_id": "esp32 01"}, 400),
    ("seq negatif", {**base, "seq": -1}, 400),
    ("source_mode tidak dikenal", {**base, "source_mode": "lainnya"}, 400),
    ("JSON rusak", "{\"device_id\": ", 400),
    ("Content-Type bukan JSON", "suhu=28.5", 415),
    ("Perangkat nonaktif (esp32-02)", {**base, "device_id": "esp32-02"}, 403),
    ("Retry identik record tersimpan", stored, 200),
    ("Identitas sama, nilai berbeda", {**stored, "suhu": stored["suhu"] + 1}, 409),
]
results = []
for name, payload, expected in cases:
    before = count()
    if isinstance(payload, dict):
        response = requests.post(URL, json=payload, timeout=5)
    else:
        content_type = "text/plain" if expected == 415 else "application/json"
        response = requests.post(URL, data=payload, headers={"Content-Type": content_type}, timeout=5)
    body = response.json()
    detail = body.get("error") or (f"event_id sama: {body.get('event_id') == row[7]}")
    results.append({"pengujian": name, "protokol": "HTTP", "diharapkan": expected, "aktual": response.status_code,
                    "pesan": detail, "record_sebelum": before, "record_sesudah": count(),
                    "lulus": response.status_code == expected and count() == before})

# MQTT: payload rusak tidak boleh disimpan ataupun mendapat ACK.
acks = []
client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="uji-validasi-mqtt")
client.on_message = lambda _c, _u, message: acks.append(message.payload)
ready = threading.Event()
client.on_subscribe = lambda *_args: ready.set()
client.connect("127.0.0.1", 1883)
client.loop_start()
client.subscribe("sensor/ack/esp32-01")
ready.wait(3)
before = count()
client.publish("sensor/dht22", json.dumps({**base, "kelembapan": 150}))
time.sleep(2)
client.loop_stop()
client.disconnect()
results.append({"pengujian": "Kelembapan 150 % melalui MQTT", "protokol": "MQTT", "diharapkan": "tanpa ACK",
                "aktual": "ACK diterima" if acks else "tanpa ACK", "pesan": "ditolak validasi subscriber",
                "record_sebelum": before, "record_sesudah": count(), "lulus": not acks and count() == before})

with open(output, "w", encoding="utf-8") as handle:
    json.dump(results, handle, ensure_ascii=False, indent=2)
for item in results:
    print(f"{'LULUS' if item['lulus'] else 'GAGAL'} | {item['pengujian']} | {item['diharapkan']} -> {item['aktual']} | {item['pesan']}")
