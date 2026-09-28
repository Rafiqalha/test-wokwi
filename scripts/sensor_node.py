"""Optional local sensor-node demo. Generates readings without a browser request."""

import argparse
import json
import random
import time
from urllib.parse import urlparse

import paho.mqtt.client as mqtt
import requests


TOPIC = "sensor/dht22"
DEVICE_ID = "demo-node-01"


class SensorNode:
    def __init__(self, rng=None):
        self.rng = rng or random.Random()
        self.temperature = 27.5
        self.humidity = 66.0

    def reading(self, seq, sent_ms):
        self.temperature = min(34.0, max(24.0,
            self.temperature + self.rng.uniform(-0.6, 0.6)))
        self.humidity = min(84.0, max(48.0,
            self.humidity + self.rng.uniform(-1.3, 1.3)))
        return {"device_id": DEVICE_ID, "seq": seq,
                "suhu": round(self.temperature, 1),
                "kelembapan": round(self.humidity, 1),
                "sent_ms": sent_ms % (2**32)}


def arguments(argv=None):
    parser = argparse.ArgumentParser(description="Aliran sensor acak untuk demo lokal")
    parser.add_argument("--protocol", choices=("http", "mqtt", "both"),
                        default="both", help="both bergantian MQTT dan HTTP")
    parser.add_argument("--interval", type=float, default=2.0,
                        help="jarak antar pembacaan dalam detik (default: 2)")
    parser.add_argument("--count", type=int, default=0,
                        help="0 berarti berjalan sampai Ctrl+C")
    parser.add_argument("--http-url", default="http://127.0.0.1:5000/update")
    parser.add_argument("--mqtt-host", default="127.0.0.1")
    parser.add_argument("--mqtt-port", type=int, default=1883)
    args = parser.parse_args(argv)
    parsed = urlparse(args.http_url)
    if args.interval < 0.1 or args.interval > 60:
        parser.error("--interval harus antara 0.1 dan 60 detik")
    if args.count < 0:
        parser.error("--count tidak boleh negatif")
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1") or parsed.path != "/update" or parsed.username or parsed.password:
        parser.error("--http-url harus endpoint /update pada HTTP loopback lokal")
    if args.mqtt_host not in ("127.0.0.1", "localhost", "::1"):
        parser.error("--mqtt-host harus loopback lokal")
    if not 1 <= args.mqtt_port <= 65535:
        parser.error("--mqtt-port tidak valid")
    return args


def connect_mqtt(host, port):
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id="demo-sensor-node")
    try:
        client.connect(host, port, keepalive=30)
    except OSError as exc:
        raise RuntimeError("Broker MQTT belum tersedia; jalankan Mosquitto atau pakai --protocol http") from exc
    client.loop_start()
    deadline = time.monotonic() + 3
    while not client.is_connected() and time.monotonic() < deadline:
        time.sleep(0.05)
    if not client.is_connected():
        client.loop_stop()
        raise RuntimeError("Broker MQTT tidak menerima koneksi")
    return client


def run(args, session=None, mqtt_client=None, output=print):
    session = session or requests
    node = SensorNode()
    started = time.monotonic()
    next_send = started
    sequence = 0
    output(f"Node {DEVICE_ID} aktif: {args.protocol}, tiap {args.interval:g} detik. Ctrl+C untuk berhenti.")
    while args.count == 0 or sequence < args.count:
        time.sleep(max(0, next_send - time.monotonic()))
        sequence += 1
        payload = node.reading(sequence, int((time.monotonic() - started) * 1000))
        protocol = args.protocol
        if protocol == "both":
            protocol = "mqtt" if sequence % 2 else "http"
        try:
            if protocol == "http":
                response = session.post(args.http_url, json=payload, timeout=4)
                response.raise_for_status()
            else:
                if mqtt_client is None or not mqtt_client.is_connected():
                    raise RuntimeError("Broker MQTT terputus")
                info = mqtt_client.publish(TOPIC, json.dumps(payload), qos=0)
                if info.rc != mqtt.MQTT_ERR_SUCCESS:
                    raise RuntimeError(f"MQTT publish gagal: {info.rc}")
            output(f"{protocol.upper()} seq={sequence} suhu={payload['suhu']:.1f}C kelembapan={payload['kelembapan']:.1f}%")
        except (requests.RequestException, RuntimeError) as exc:
            output(f"{protocol.upper()} seq={sequence} gagal: {exc}")
        next_send = max(next_send + args.interval, time.monotonic())


def main(argv=None):
    args = arguments(argv)
    client = connect_mqtt(args.mqtt_host, args.mqtt_port) if args.protocol in ("mqtt", "both") else None
    try:
        run(args, mqtt_client=client)
    except KeyboardInterrupt:
        print("Node demo dihentikan.")
    finally:
        if client is not None:
            client.disconnect()
            client.loop_stop()


if __name__ == "__main__":
    main()
