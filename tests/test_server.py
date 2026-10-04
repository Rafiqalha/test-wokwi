import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from dashboard_state import LiveState

from server import (create_app, init_db, connect_db, handle_mqtt_message,
                    sync_once, sync_metrics_once, sync_worker, probe_supabase, validate_payload)


SAMPLE = {"device_id": "esp32-01", "seq": 1, "suhu": 29.4,
          "kelembapan": 71.2, "sent_ms": 5200,
          "session_id": "boot-01", "source_mode": "dht22"}
METRIC = {"device_id": "esp32-01", "session_id": "boot-01", "source_mode": "dht22",
          "seq": 1, "protokol": "HTTP", "status": "ok", "rtt_ms": 12.5}


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.state = LiveState()
        for target in ("server.live_state", "dashboard_state.live_state"):
            patcher = patch(target, self.state)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.temp.name) / "sensor.db")
        init_db(self.db)
        self.client = create_app(self.db).test_client()

    def tearDown(self):
        self.temp.cleanup()

    def test_http_receives_and_logs_same_payload(self):
        response = self.client.post("/update", json=SAMPLE)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["seq"], 1)
        with connect_db(self.db) as conn:
            row = conn.execute("select * from readings").fetchone()
        self.assertEqual(row["protokol"], "HTTP")
        self.assertEqual(row["suhu"], 29.4)
        self.assertEqual(row["waktu_diterima"], response.json["waktu_diterima"])

    def test_invalid_sensor_value_is_rejected(self):
        bad = {**SAMPLE, "kelembapan": 101}
        response = self.client.post("/update", json=bad)
        self.assertEqual(response.status_code, 400)
        with connect_db(self.db) as conn:
            count = conn.execute("select count(*) from readings").fetchone()[0]
        self.assertEqual(count, 0)
        with self.assertRaises(ValueError):
            validate_payload({**SAMPLE, "suhu": float("nan")})

    def test_mqtt_subscriber_logs_and_acknowledges(self):
        broker_client = Mock()
        message = Mock(payload=json.dumps(SAMPLE).encode("utf-8"))
        handle_mqtt_message(self.db, broker_client, message)
        topic, acknowledgement = broker_client.publish.call_args.args
        self.assertEqual(topic, "sensor/ack/esp32-01")
        self.assertEqual(json.loads(acknowledgement)["seq"], 1)
        with connect_db(self.db) as conn:
            row = conn.execute("select protokol from readings").fetchone()
        self.assertEqual(row["protokol"], "MQTT")

    def test_sync_marks_only_successfully_uploaded_rows(self):
        self.client.post("/update", json=SAMPLE)
        session = Mock()
        response = Mock()
        response.raise_for_status.return_value = None
        session.post.return_value = response
        count = sync_once(self.db, "https://project.supabase.co", "test", session)
        self.assertEqual(count, 1)
        self.assertEqual(session.post.call_args.kwargs["json"][0]["protokol"], "HTTP")
        with connect_db(self.db) as conn:
            self.assertIsNotNone(conn.execute(
                "select synced_at from readings").fetchone()[0])
        self.assertEqual(sync_once(self.db, "https://project.supabase.co", "test", session), 0)


    def test_dashboard_reports_live_snapshot_and_serves_assets(self):
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Sensor telemetry", page.data)
        page.close()
        script = self.client.get("/assets/app.js")
        self.assertEqual(script.status_code, 200)
        script.close()
        self.client.post("/update", json=SAMPLE)
        data = self.client.get("/api/dashboard").json
        self.assertEqual(data["totals"], {"total": 1, "mqtt": 0,
                                          "http": 1, "pending": 1, "synced": 0})
        self.assertEqual(data["readings"][0]["suhu"], 29.4)
        self.assertIsInstance(data["readings"][0]["local_write_ms"], float)
        self.assertNotIn("SUPABASE_SECRET_KEY", str(data))

    def test_dashboard_stream_notifies_on_new_reading(self):
        stream = self.client.get("/api/stream", buffered=False)
        iterator = iter(stream.response)
        self.assertIn(b"connected", next(iterator))
        self.client.post("/update", json=SAMPLE)
        self.assertIn(b"event: update", next(iterator))
        stream.close()

    def test_old_database_schema_is_migrated(self):
        with connect_db(self.db) as conn:
            conn.execute("drop table readings")
            conn.execute("""create table readings (
                event_id text primary key, device_id text, seq integer,
                suhu real, kelembapan real, sent_ms integer, protokol text,
                waktu_diterima text, synced_at text)""")
            conn.execute("insert into readings values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         ("historical", "esp32-01", 1, 29.4, 71.2, 5200,
                          "HTTP", "2026-09-28T08:06:55+00:00", None))
        init_db(self.db)
        with connect_db(self.db) as conn:
            columns = {row["name"] for row in conn.execute("pragma table_info(readings)")}
        self.assertIn("local_write_ms", columns)
        self.assertIn("sync_request_ms", columns)
        self.assertIn("session_id", columns)
        self.assertIn("source_mode", columns)
        historical = self.client.get("/api/dashboard").json["readings"][0]
        self.assertEqual(historical["event_id"], "historical")
        self.assertEqual(historical["source_mode"], "unknown")
        self.assertIsNone(historical["local_write_ms"])
        self.assertIsNone(historical["rtt_ms"])

    def test_retry_is_idempotent_and_ack_preserves_original_timestamp(self):
        first = self.client.post("/update", json=SAMPLE)
        second = self.client.post("/update", json=SAMPLE)
        self.assertEqual(first.json, second.json)
        self.assertEqual(self.client.get("/api/dashboard").json["totals"]["total"], 1)
        self.assertEqual(second.json["session_id"], SAMPLE["session_id"])

    def test_concurrent_retry_does_not_duplicate(self):
        app = create_app(self.db)
        def send(_index):
            with app.test_client() as client:
                response = client.post("/update", json=SAMPLE)
                return response.status_code, response.json["event_id"]
        with ThreadPoolExecutor(max_workers=6) as executor:
            results = list(executor.map(send, range(6)))
        self.assertEqual({code for code, _ in results}, {200})
        self.assertEqual(len({event for _, event in results}), 1)

    def test_restart_and_protocol_use_separate_identities(self):
        self.client.post("/update", json=SAMPLE)
        self.client.post("/update", json={**SAMPLE, "session_id": "boot-02"})
        broker = Mock()
        message = Mock(topic="sensor/dht22", payload=json.dumps(SAMPLE).encode())
        handle_mqtt_message(self.db, broker, message)
        handle_mqtt_message(self.db, broker, message)
        self.assertEqual(self.client.get("/api/dashboard").json["totals"]["total"], 3)
        self.assertEqual(broker.publish.call_count, 2)
        self.assertEqual(broker.publish.call_args_list[0], broker.publish.call_args_list[1])

    def test_conflicting_retry_does_not_overwrite(self):
        first = self.client.post("/update", json=SAMPLE)
        conflict = self.client.post("/update", json={**SAMPLE, "suhu": 30})
        self.assertEqual(conflict.status_code, 409)
        snapshot = self.client.get("/api/dashboard").json
        self.assertEqual(snapshot["totals"]["total"], 1)
        self.assertEqual(snapshot["readings"][0]["suhu"], SAMPLE["suhu"])
        self.assertEqual(snapshot["readings"][0]["event_id"], first.json["event_id"])

    def test_legacy_origin_is_unknown_and_metadata_must_be_complete(self):
        legacy = {key: value for key, value in SAMPLE.items() if key not in ("session_id", "source_mode")}
        self.assertEqual(self.client.post("/update", json=legacy).status_code, 200)
        row = self.client.get("/api/dashboard").json["readings"][0]
        self.assertEqual(row["source_mode"], "unknown")
        self.assertIsNone(row["session_id"])
        for payload in ({**legacy, "session_id": "boot"}, {**SAMPLE, "source_mode": []},
                        {**SAMPLE, "suhu": 10**100}, {**SAMPLE, "seq": True}):
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post("/update", json=payload).status_code, 400)

    def test_metrics_join_readings_and_retry_is_idempotent(self):
        # A report can arrive before its sensor reading.
        self.assertEqual(self.client.post("/metrics", json=METRIC).status_code, 200)
        self.assertEqual(self.client.post("/metrics", json=METRIC).status_code, 200)
        self.client.post("/update", json=SAMPLE)
        snapshot = self.client.get("/api/dashboard").json
        self.assertEqual(snapshot["attempt_totals"]["total"], 1)
        self.assertEqual(snapshot["readings"][0]["rtt_ms"], 12.5)
        self.assertEqual(snapshot["readings"][0]["delivery_status"], "ok")
        self.assertEqual(self.client.post("/metrics", json={**METRIC, "rtt_ms": 20}).status_code, 409)

    def test_failed_attempt_is_kept_without_inventing_a_reading_or_rtt(self):
        metric = {**METRIC, "status": "timeout", "rtt_ms": None}
        self.assertEqual(self.client.post("/metrics", json=metric).status_code, 200)
        snapshot = self.client.get("/api/dashboard").json
        self.assertEqual(snapshot["readings"], [])
        self.assertEqual(snapshot["attempt_totals"]["timeout"], 1)
        self.assertIsNone(snapshot["attempts"][0]["rtt_ms"])
        for bad in ({**metric, "rtt_ms": 0}, {**METRIC, "rtt_ms": None},
                    {**METRIC, "rtt_ms": True}, {**METRIC, "rtt_ms": 10**100}):
            self.assertEqual(self.client.post("/metrics", json=bad).status_code, 400)

    def test_source_must_match_even_when_report_arrives_first(self):
        self.client.post("/metrics", json=METRIC)
        self.assertEqual(self.client.post("/update", json={**SAMPLE, "source_mode": "random"}).status_code, 409)
        self.client.post("/update", json={**SAMPLE, "session_id": "other-boot"})
        self.assertEqual(self.client.post("/metrics", json={**METRIC, "session_id": "other-boot", "source_mode": "demo"}).status_code, 409)

    def test_stream_recovers_when_client_revision_is_from_previous_process(self):
        stream = self.state.events(last_revision=999)
        self.assertIn("connected", next(stream))
        self.state.changed()
        self.assertIn("event: update", next(stream))
        stream.close()

    def test_mqtt_metric_is_not_counted_as_sensor_or_acknowledged(self):
        broker = Mock()
        metric = {**METRIC, "protokol": "MQTT"}
        handle_mqtt_message(self.db, broker, Mock(topic="sensor/metrics", payload=json.dumps(metric).encode()))
        broker.publish.assert_not_called()
        snapshot = self.client.get("/api/dashboard").json
        self.assertEqual(snapshot["totals"]["total"], 0)
        self.assertEqual(snapshot["attempt_totals"]["total"], 1)

    def test_filter_is_applied_before_window_limit_and_to_totals(self):
        self.client.post("/update", json=SAMPLE)
        self.client.post("/metrics", json=METRIC)
        self.client.post("/update", json={**SAMPLE, "session_id": "demo-boot", "source_mode": "demo"})
        selected = self.client.get("/api/dashboard?source_mode=dht22&session_id=boot-01&device_id=esp32-01").json
        self.assertEqual(selected["totals"]["total"], 1)
        self.assertEqual(selected["attempt_totals"]["total"], 1)
        self.assertEqual(len(selected["sessions"]), 2)
        self.assertEqual(self.client.get("/api/dashboard?source_mode=invalid").status_code, 400)
        self.assertEqual(self.client.get("/api/dashboard?session_id=%27").status_code, 400)

    def test_failed_upload_keeps_pending_and_retry_carries_metadata(self):
        self.client.post("/update", json=SAMPLE)
        self.client.post("/metrics", json=METRIC)
        session = Mock()
        session.post.return_value.raise_for_status.side_effect = requests.HTTPError("503")
        for sync in (sync_once, sync_metrics_once):
            with self.assertRaises(requests.HTTPError):
                sync(self.db, "http://127.0.0.1:55421", "test", session)
        snapshot = self.client.get("/api/dashboard").json
        self.assertEqual(snapshot["totals"]["pending"], 1)
        self.assertEqual(snapshot["attempt_totals"]["pending"], 1)
        session.post.return_value.raise_for_status.side_effect = None
        self.assertEqual(sync_once(self.db, "http://127.0.0.1:55421", "test", session), 1)
        self.assertEqual(session.post.call_args.kwargs["json"][0]["source_mode"], "dht22")
        self.assertEqual(sync_metrics_once(self.db, "http://127.0.0.1:55421", "test", session), 1)
        self.assertEqual(self.client.get("/api/dashboard").json["attempt_totals"]["pending"], 0)

    def test_empty_backlog_is_probed_and_offline_state_is_detected(self):
        session = Mock()
        probe_supabase("http://127.0.0.1:55421", "test", session)
        self.assertEqual(session.get.call_count, 2)
        self.assertEqual(self.state.supabase, "online")
        self.assertIsNotNone(self.state.last_check_at)
        stop = Mock()
        stop.is_set.side_effect = [False, True]
        with patch("server.probe_supabase", side_effect=requests.ConnectionError()), \
                patch("server.sync_once", return_value=0), patch("server.sync_metrics_once", return_value=0):
            sync_worker(self.db, "http://127.0.0.1:55421", "test", stop)
        self.assertEqual(self.state.supabase, "error")
        self.assertEqual(self.state.sync_error, "Koneksi gagal")
if __name__ == "__main__":
    unittest.main()
