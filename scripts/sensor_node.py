"""Optional local sensor-node demo. Generates readings without a browser request."""

import argparse
import json
import random
import threading
import time
import uuid
from urllib.parse import urlparse

import paho.mqtt.client as mqtt
import requests


TOPIC = "sensor/dht22"
DEVICE_ID = "demo-node-01"
ACK_TOPIC = "sensor/ack/" + DEVICE_ID


class SensorNode:
    def __init__(self, rng=None, session_id=None):
        self.rng = rng or random.Random()
        self.session_id = session_id or uuid.uuid4().hex
        self.temperature = 27.5
        self.humidity = 66.0

    def reading(self, seq, sent_ms):
        self.temperature = min(34.0, max(24.0,
            self.temperature + self.rng.uniform(-0.6, 0.6)))
        self.humidity = min(84.0, max(48.0,
            self.humidity + self.rng.uniform(-1.3, 1.3)))
        return {"device_id": DEVICE_ID, "session_id": self.session_id,
                "source_mode": "demo", "seq": seq,
                "suhu": round(self.temperature, 1),
                "kelembapan": round(self.humidity, 1),
                "sent_ms": sent_ms % (2**32)}


class AckTracker:
    def __init__(self, session_id):
        self.session_id = session_id
        self.condition = threading.Condition()
        self.expected = None
        self.acknowledged = False

    def expect(self, seq):
        with self.condition:
            self.expected = seq
            self.acknowledged = False

    def receive(self, value):
        if not isinstance(value, dict):
            return
        with self.condition:
            if (value.get("session_id") == self.session_id and
                    type(value.get("seq")) is int and value["seq"] == self.expected):
                self.acknowledged = True
                self.condition.notify_all()

    def wait(self, timeout):
        with self.condition:
            return self.condition.wait_for(lambda: self.acknowledged, timeout=timeout)


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


def connect_mqtt(host, port, tracker):
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id="demo-sensor-node")
    subscribed = threading.Event()

    def on_connect(client, _userdata, _flags, reason_code, _properties):
        if reason_code == 0:
            client.subscribe(ACK_TOPIC, qos=0)

    def on_subscribe(_client, _userdata, _mid, reasons, _properties):
        if reasons and all(not reason.is_failure for reason in reasons):
            subscribed.set()

    def on_message(_client, _userdata, message):
        if message.topic == ACK_TOPIC:
            try:
                tracker.receive(json.loads(message.payload))
            except (ValueError, UnicodeError):
                pass

    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_message = on_message
    try:
        client.connect(host, port, keepalive=30)
    except OSError as exc:
        raise RuntimeError("Broker MQTT belum tersedia; jalankan Mosquitto atau pakai --protocol http") from exc
    client.loop_start()
    if not subscribed.wait(3):
        client.disconnect()
        client.loop_stop()
        raise RuntimeError("Broker MQTT tidak menerima koneksi atau langganan ACK")
    return client


def run(args, session=None, mqtt_client=None, output=print, node=None, tracker=None):
    session = session or requests
    node = node or SensorNode()
    tracker = tracker or AckTracker(node.session_id)
    started = time.monotonic()
    next_send = started
    sequence = 0
    output(f"Node {DEVICE_ID}, sesi {node.session_id}: {args.protocol}, tiap {args.interval:g} detik. Ctrl+C untuk berhenti.")
    while args.count == 0 or sequence < args.count:
        time.sleep(max(0, next_send - time.monotonic()))
        sequence += 1
        payload = node.reading(sequence, int((time.monotonic() - started) * 1000))
        protocol = args.protocol
        if protocol == "both":
            protocol = "mqtt" if sequence % 2 else "http"
        status, rtt = "error", None
        measured_at = time.perf_counter()
        try:
            if protocol == "http":
                response = session.post(args.http_url, json=payload, timeout=4)
                response.raise_for_status()
                reply = response.json()
                if (reply.get("status") != "ok" or reply.get("session_id") != node.session_id or
                        type(reply.get("seq")) is not int or reply["seq"] != sequence):
                    raise RuntimeError("ACK HTTP tidak cocok dengan sesi/pembacaan")
            else:
                if mqtt_client is None or not mqtt_client.is_connected():
                    raise RuntimeError("Broker MQTT terputus")
                tracker.expect(sequence)
                measured_at = time.perf_counter()
                info = mqtt_client.publish(TOPIC, json.dumps(payload), qos=0)
                if info.rc != mqtt.MQTT_ERR_SUCCESS:
                    raise RuntimeError(f"MQTT publish gagal: {info.rc}")
                if not tracker.wait(4):
                    raise TimeoutError("ACK MQTT belum diterima")
            rtt = (time.perf_counter() - measured_at) * 1000
            status = "ok"
        except (requests.Timeout, TimeoutError) as exc:
            status = "timeout"
            output(f"{protocol.upper()} seq={sequence} timeout: {exc}")
        except (requests.RequestException, RuntimeError, ValueError, AttributeError) as exc:
            output(f"{protocol.upper()} seq={sequence} gagal: {exc}")
        metric = {name: payload[name] for name in ("device_id", "session_id", "source_mode", "seq")}
        metric.update(protokol=protocol.upper(), status=status, rtt_ms=rtt)
        try:
            if protocol == "http":
                endpoint = args.http_url.rsplit("/", 1)[0] + "/metrics"
                session.post(endpoint, json=metric, timeout=4).raise_for_status()
            else:
                info = mqtt_client.publish("sensor/metrics", json.dumps(metric), qos=0)
                if info.rc != mqtt.MQTT_ERR_SUCCESS:
                    raise RuntimeError("Publish metrik gagal")
        except (requests.RequestException, RuntimeError, AttributeError) as exc:
            output(f"Metrik seq={sequence} belum tersimpan: {exc}")
        if status == "ok":
            output(f"{protocol.upper()} seq={sequence} suhu={payload['suhu']:.1f}C kelembapan={payload['kelembapan']:.1f}% RTT={rtt:.2f}ms")
        next_send = max(next_send + args.interval, time.monotonic())


def main(argv=None):
    args = arguments(argv)
    node = SensorNode()
    tracker = AckTracker(node.session_id)
    client = connect_mqtt(args.mqtt_host, args.mqtt_port, tracker) if args.protocol in ("mqtt", "both") else None
    try:
        run(args, mqtt_client=client, node=node, tracker=tracker)
    except KeyboardInterrupt:
        print("Node demo dihentikan.")
    finally:
        if client is not None:
            client.disconnect()
            client.loop_stop()


if __name__ == "__main__":
    main()
