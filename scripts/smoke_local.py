"""Exercise real local MQTT/HTTP with a temporary broker/database, never the live log."""

import argparse
import csv
import json
import os
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import requests
from dotenv import dotenv_values
from werkzeug.serving import WSGIRequestHandler, make_server

from dashboard_state import live_state
from server import (connect_db, create_app, init_db, now_utc, start_mqtt,
                    probe_supabase, sync_once, sync_metrics_once)
from scripts.sensor_node import AckTracker, SensorNode, arguments, connect_mqtt, run


class QuietHandler(WSGIRequestHandler):
    def log(self, _type, _message, *args):
        pass


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def wait_for(check, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.05)
    raise RuntimeError("Layanan uji tidak siap sebelum batas waktu")


def check_supabase(db, snapshot):
    config = dotenv_values(ROOT / ".env")
    url, key = config.get("SUPABASE_URL", ""), config.get("SUPABASE_SECRET_KEY", "")
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1") or not key:
        raise RuntimeError("Uji Supabase hanya menerima konfigurasi HTTP loopback lokal")
    tables = {"sensor_readings": snapshot["readings"], "delivery_attempts": snapshot["attempts"]}
    queries = {table: {"event_id": "in.(" + ",".join(row["event_id"] for row in rows) + ")"}
               for table, rows in tables.items()}
    def select(table):
        response = requests.get(url.rstrip("/") + "/rest/v1/" + table,
                                headers={"apikey": key}, params=queries[table], timeout=5)
        response.raise_for_status()
        return response.json()
    # Only remove fixture UUIDs proven absent before this test uploads them.
    for table in tables:
        assert select(table) == [], "Fixture IDs already exist; aborting without deleting anything"
    try:
        probe_supabase(url, key)
        assert sync_once(db, url, key) == 12
        assert sync_metrics_once(db, url, key) == 12
        assert sync_once(db, url, key) == sync_metrics_once(db, url, key) == 0
        for table in tables:
            rows = select(table)
            assert len(rows) == 12
            assert all(row["source_mode"] == "demo" and
                       row["session_id"] == snapshot["readings"][0]["session_id"] for row in rows)
        return {"readings_synced": 12, "metrics_synced": 12, "retry_has_no_pending_rows": True,
                "fixtures_removed": True}
    finally:
        for table in tables:
            response = requests.delete(url.rstrip("/") + "/rest/v1/" + table,
                                       headers={"apikey": key}, params=queries[table], timeout=5)
            response.raise_for_status()
            assert select(table) == [], "Temporary Supabase fixtures were not removed"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mosquitto", default=shutil.which("mosquitto"))
    parser.add_argument("--browser", help="Optional Chrome/Edge executable for headless UI verification")
    parser.add_argument("--supabase", action="store_true", help="Also test local Supabase, then remove only this test's fixture UUIDs")
    args = parser.parse_args()
    if not args.mosquitto or not Path(args.mosquitto).is_file():
        parser.error("Berikan --mosquitto dengan path executable broker")
    artifact = ROOT / "data" / "validation"
    artifact.mkdir(parents=True, exist_ok=True)
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with tempfile.TemporaryDirectory(prefix="wokwi-smoke-") as directory:
        temp = Path(directory)
        db = str(temp / "sensor.db")
        port = free_port()
        config = temp / "mosquitto.conf"
        config.write_text(f"listener {port} 127.0.0.1\nallow_anonymous true\npersistence false\n", encoding="utf-8")
        broker_log = (temp / "broker.log").open("w", encoding="utf-8")
        broker = subprocess.Popen([args.mosquitto, "-c", str(config)],
                                  stdout=broker_log, stderr=broker_log, creationflags=flags)
        receiver = sender = http = thread = None
        try:
            def broker_ready():
                if broker.poll() is not None:
                    raise RuntimeError("Broker uji berhenti sebelum siap")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                        return True
                except OSError:
                    return False
            wait_for(broker_ready)
            init_db(db)
            receiver = start_mqtt(db, "127.0.0.1", port)
            wait_for(lambda: live_state.status()["mqtt"] == "online")
            time.sleep(0.1)
            http = make_server("127.0.0.1", 0, create_app(db), threaded=True, request_handler=QuietHandler)
            thread = threading.Thread(target=http.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{http.server_port}"
            node = SensorNode()
            tracker = AckTracker(node.session_id)
            sender = connect_mqtt("127.0.0.1", port, tracker)
            options = arguments(["--protocol", "both", "--count", "12", "--interval", "0.1",
                                 "--mqtt-port", str(port), "--http-url", base + "/update"])
            run(options, mqtt_client=sender, node=node, tracker=tracker)
            wait_for(lambda: requests.get(base + "/api/dashboard", timeout=2).json()["attempt_totals"]["total"] == 12)
            snapshot = requests.get(base + "/api/dashboard?source_mode=demo", timeout=2).json()
            assert snapshot["totals"]["total"] == 12
            assert snapshot["totals"]["mqtt"] == snapshot["totals"]["http"] == 6
            assert snapshot["attempt_totals"]["ok"] == 12
            assert all(row["session_id"] == node.session_id and row["rtt_ms"] is not None for row in snapshot["readings"])
            with connect_db(db) as conn:
                row = conn.execute("select * from readings where protokol='HTTP' limit 1").fetchone()
                replay = {name: row[name] for name in
                          ("device_id", "session_id", "source_mode", "seq", "suhu", "kelembapan", "sent_ms")}
            reply = requests.post(base + "/update", json=replay, timeout=2)
            assert reply.status_code == 200 and reply.json()["event_id"] == row["event_id"]
            assert requests.get(base + "/api/dashboard", timeout=2).json()["totals"]["total"] == 12
            report = {"checked_at": now_utc(), "source_mode": "demo", "session_id": node.session_id,
                      "scope": "Temporary local Mosquitto + Flask + SQLite; no Wokwi or Supabase",
                      "readings": 12, "metrics": 12, "duplicate_replay_preserved": True,
                      "protocols": {}, "browser": {}}
            if args.supabase:
                report["supabase"] = check_supabase(db, snapshot)
                report["scope"] = "Temporary local Mosquitto + Flask + SQLite + local Supabase; no Wokwi"
            for protocol in ("MQTT", "HTTP"):
                values = [item["rtt_ms"] for item in snapshot["attempts"] if item["protokol"] == protocol]
                report["protocols"][protocol] = {"samples": len(values), "median_rtt_ms": statistics.median(values),
                                                 "min_rtt_ms": min(values), "max_rtt_ms": max(values)}
            columns = ["device_id", "session_id", "source_mode", "seq", "protokol", "status", "rtt_ms", "waktu_dilaporkan"]
            with (artifact / "local-rtt.csv").open("w", encoding="utf-8-sig", newline="") as target:
                writer = csv.DictWriter(target, fieldnames=columns, extrasaction="ignore")
                writer.writeheader(); writer.writerows(snapshot["attempts"])
            if args.browser:
                result = subprocess.run(["node", str(ROOT / "scripts" / "check_dashboard.cjs"),
                                         args.browser, base, str(temp / "browser"), str(artifact)],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=55,
                    creationflags=flags)
                assert result.returncode == 0, result.stderr[-2000:]
                report["browser"] = json.loads(result.stdout)
            (artifact / "local-smoke.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(report, indent=2))
        finally:
            for client in (sender, receiver):
                if client is not None:
                    client.disconnect(); client.loop_stop()
            if http is not None:
                http.shutdown(); http.server_close()
            if thread is not None:
                thread.join(timeout=3)
            broker.terminate()
            broker.wait(timeout=5)
            broker_log.close()


if __name__ == "__main__":
    main()
