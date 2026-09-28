"""Laptop receiver: Flask HTTP endpoint, MQTT subscriber, durable local log."""

import json
import logging
import math
import os
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import paho.mqtt.client as mqtt
import requests
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request, stream_with_context

from dashboard_state import dashboard_snapshot, live_state


TOPIC = "sensor/dht22"
ACK_PREFIX = "sensor/ack/"
DEVICE_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,32}\Z")
log = logging.getLogger("sensor-server")


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def validate_payload(value):
    if not isinstance(value, dict):
        raise ValueError("Body harus objek JSON")
    required = {"device_id", "seq", "suhu", "kelembapan", "sent_ms"}
    if set(value) != required:
        raise ValueError("Field harus: device_id, seq, suhu, kelembapan, sent_ms")
    device_id = value["device_id"]
    if not isinstance(device_id, str) or not DEVICE_PATTERN.fullmatch(device_id):
        raise ValueError("device_id tidak valid")
    for field in ("seq", "sent_ms"):
        if type(value[field]) is not int or not 0 <= value[field] <= 2**32 - 1:
            raise ValueError(f"{field} harus bilangan bulat 0..4294967295")
    for field, minimum, maximum in (("suhu", -40, 125), ("kelembapan", 0, 100)):
        number = value[field]
        if type(number) not in (int, float) or not math.isfinite(number):
            raise ValueError(f"{field} harus angka terbatas")
        if not minimum <= number <= maximum:
            raise ValueError(f"{field} di luar rentang DHT22")
    return value


@contextmanager
def connect_db(path):
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("pragma busy_timeout=5000")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with connect_db(path) as conn:
        conn.execute("pragma journal_mode=WAL")
        conn.execute("""create table if not exists readings (
            event_id text primary key,
            device_id text not null,
            seq integer not null,
            suhu real not null,
            kelembapan real not null,
            sent_ms integer not null,
            protokol text not null,
            waktu_diterima text not null,
            synced_at text,
            local_write_ms real,
            sync_request_ms real
        )""")
        columns = {row["name"] for row in conn.execute("pragma table_info(readings)")}
        for name in ("local_write_ms", "sync_request_ms"):
            if name not in columns:
                conn.execute(f"alter table readings add column {name} real")
        conn.execute("create index if not exists readings_received_idx "
                     "on readings(waktu_diterima desc)")


def save_reading(db_path, payload, protocol, received_at):
    started = time.perf_counter()
    reading = validate_payload(payload)
    event_id = str(uuid.uuid4())
    with connect_db(db_path) as conn:
        conn.execute("""insert into readings
            (event_id, device_id, seq, suhu, kelembapan, sent_ms,
             protokol, waktu_diterima)
            values (?, ?, ?, ?, ?, ?, ?, ?)""",
            (event_id, reading["device_id"], reading["seq"], reading["suhu"],
             reading["kelembapan"], reading["sent_ms"], protocol, received_at))
        conn.commit()
        local_ms = (time.perf_counter() - started) * 1000
        conn.execute("update readings set local_write_ms=? where event_id=?",
                     (local_ms, event_id))
    live_state.changed()
    log.info("DITERIMA %s %s seq=%d suhu=%.1f kelembapan=%.1f waktu=%s",
             protocol, reading["device_id"], reading["seq"], reading["suhu"],
             reading["kelembapan"], received_at)
    return event_id


def create_app(db_path):
    app = Flask(__name__, static_folder="dashboard", static_url_path="/assets")
    app.config["MAX_CONTENT_LENGTH"] = 1024

    @app.get("/")
    def dashboard_page():
        return app.send_static_file("index.html")

    @app.get("/api/dashboard")
    def dashboard_data():
        try:
            return jsonify(dashboard_snapshot(db_path))
        except sqlite3.Error:
            log.exception("Gagal membaca data dashboard")
            return jsonify(error="Data dashboard tidak tersedia"), 503

    @app.get("/api/stream")
    def dashboard_stream():
        try:
            last_id = max(0, int(request.headers.get("Last-Event-ID", "0")))
        except ValueError:
            last_id = 0
        return Response(stream_with_context(live_state.events(last_id)),
                        mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache",
                                 "X-Accel-Buffering": "no"})

    @app.post("/update")
    def update():
        received_at = now_utc()  # Capture before validation, disk, or cloud I/O.
        if not request.is_json:
            return jsonify(error="Content-Type harus application/json"), 415
        try:
            payload = validate_payload(request.get_json(silent=True))
            event_id = save_reading(db_path, payload, "HTTP", received_at)
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        except sqlite3.Error:
            log.exception("Gagal menyimpan log lokal HTTP")
            return jsonify(error="Penyimpanan lokal gagal"), 503
        return jsonify(status="ok", event_id=event_id,
                       seq=payload["seq"], waktu_diterima=received_at), 200

    return app


def handle_mqtt_message(db_path, client, message):
    received_at = now_utc()  # Captured as soon as the subscriber receives it.
    try:
        payload = validate_payload(json.loads(message.payload))
        event_id = save_reading(db_path, payload, "MQTT", received_at)
    except (ValueError, UnicodeError, sqlite3.Error):
        log.exception("Pesan MQTT tidak valid atau penyimpanan gagal")
        return
    ack_topic = ACK_PREFIX + payload["device_id"]
    ack = json.dumps({"seq": payload["seq"], "event_id": event_id,
                      "waktu_diterima": received_at}, separators=(",", ":"))
    client.publish(ack_topic, ack, qos=0)


def start_mqtt(db_path, host, port):
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id="laptop-subscriber")

    def on_connect(client, _userdata, _flags, reason_code, _properties):
        if reason_code == 0:
            client.subscribe(TOPIC, qos=0)
            live_state.update(mqtt="online")
            log.info("MQTT subscribe: %s", TOPIC)
        else:
            live_state.update(mqtt="offline")
            log.error("MQTT connect gagal: %s", reason_code)

    def on_disconnect(_client, _userdata, _flags, _reason_code, _properties):
        live_state.update(mqtt="offline")

    def on_message(client, _userdata, message):
        handle_mqtt_message(db_path, client, message)

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=10)
    client.connect_async(host, port, keepalive=30)
    client.loop_start()
    return client


def sync_once(db_path, url, key, session=None):
    session = session or requests
    with connect_db(db_path) as conn:
        rows = conn.execute("""select event_id, device_id, seq, suhu,
            kelembapan, sent_ms, protokol, waktu_diterima
            from readings where synced_at is null
            order by waktu_diterima limit 20""").fetchall()
    if not rows:
        return 0
    endpoint = url.rstrip("/") + "/rest/v1/sensor_readings?on_conflict=event_id"
    started = time.perf_counter()
    response = session.post(
        endpoint,
        headers={"apikey": key, "Content-Type": "application/json",
                 "Prefer": "resolution=merge-duplicates,return=minimal"},
        json=[dict(row) for row in rows], timeout=10)
    response.raise_for_status()
    request_ms = (time.perf_counter() - started) * 1000
    synced_at = now_utc()
    with connect_db(db_path) as conn:
        conn.executemany("update readings set synced_at=?, sync_request_ms=? "
                         "where event_id=?",
                         [(synced_at, request_ms, row["event_id"]) for row in rows])
    live_state.update(supabase="online", last_sync_at=synced_at, sync_error=None)
    log.info("Supabase tersinkron: %d baris", len(rows))
    return len(rows)


def sync_worker(db_path, url, key, stop):
    if not url or not key or "REPLACE_ME" in key:
        live_state.update(supabase="unconfigured")
        log.warning("Supabase belum dikonfigurasi; data tetap tersimpan di SQLite")
        return
    parsed = urlparse(url)
    is_local = parsed.hostname in ("127.0.0.1", "localhost", "::1")
    if parsed.scheme != "https" and not (parsed.scheme == "http" and is_local):
        live_state.update(supabase="error", sync_error="URL tidak valid")
        log.error("SUPABASE_URL harus HTTPS atau HTTP pada loopback lokal")
        return
    live_state.update(supabase="waiting", sync_error=None)
    while not stop.is_set():
        try:
            count = sync_once(db_path, url, key)
            if count:
                continue
        except requests.RequestException as exc:
            code = getattr(getattr(exc, "response", None), "status_code", None)
            live_state.update(supabase="error",
                              sync_error=f"HTTP {code}" if code else "Koneksi gagal")
            log.warning("Sinkronisasi Supabase gagal: %s", exc)
        except sqlite3.Error:
            live_state.update(supabase="error", sync_error="Log lokal gagal dibaca")
            log.exception("Gagal membaca log lokal untuk sinkronisasi")
        stop.wait(2)


def main():
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    db_path = os.getenv("SQLITE_PATH", "data/sensor.db")
    init_db(db_path)
    stop = threading.Event()
    threading.Thread(target=sync_worker,
                     args=(db_path, os.getenv("SUPABASE_URL"),
                           os.getenv("SUPABASE_SECRET_KEY"), stop),
                     daemon=True).start()
    host = os.getenv("MQTT_HOST", "127.0.0.1")
    port = int(os.getenv("MQTT_PORT", "1883"))
    mqtt_client = start_mqtt(db_path, host, port)
    try:
        create_app(db_path).run(host=os.getenv("HTTP_HOST", "127.0.0.1"),
                                port=int(os.getenv("HTTP_PORT", "5000")),
                                threaded=True, use_reloader=False)
    finally:
        stop.set()
        mqtt_client.loop_stop()
        mqtt_client.disconnect()


if __name__ == "__main__":
    main()
