import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from server import (create_app, init_db, connect_db, handle_mqtt_message,
                    sync_once, validate_payload)


SAMPLE = {"device_id": "esp32-01", "seq": 1, "suhu": 29.4,
          "kelembapan": 71.2, "sent_ms": 5200}


class ReceiverTests(unittest.TestCase):
    def setUp(self):
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
        init_db(self.db)
        with connect_db(self.db) as conn:
            columns = {row["name"] for row in conn.execute("pragma table_info(readings)")}
        self.assertIn("local_write_ms", columns)
        self.assertIn("sync_request_ms", columns)
if __name__ == "__main__":
    unittest.main()
