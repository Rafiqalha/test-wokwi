import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from dashboard_state import LiveState, database_overview, split_definitions, check_expressions
from server import connect_db, create_app, handle_mqtt_message, init_db


SAMPLE = {"device_id": "esp32-01", "seq": 1, "suhu": 29.4, "kelembapan": 71.2,
          "sent_ms": 5200, "session_id": "boot-01", "source_mode": "dht22"}
# Schema written by the release before database/schema.sql (no devices, FK or CHECK).
PREVIOUS_SCHEMA = """
create table readings (
    event_id text primary key, device_id text not null, seq integer not null,
    suhu real not null, kelembapan real not null, sent_ms integer not null,
    protokol text not null, waktu_diterima text not null, synced_at text,
    local_write_ms real, sync_request_ms real, session_id text,
    source_mode text not null default 'unknown');
create index readings_received_idx on readings(waktu_diterima desc);
create unique index readings_identity_idx on readings(device_id, session_id, protokol, seq)
    where session_id is not null;
create table delivery_attempts (
    event_id text primary key, device_id text not null, session_id text not null,
    source_mode text not null, seq integer not null, protokol text not null,
    status text not null, rtt_ms real, waktu_dilaporkan text not null, synced_at text,
    unique(device_id, session_id, protokol, seq));
create index attempts_reported_idx on delivery_attempts(waktu_dilaporkan desc);
"""


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.state = LiveState()
        for target in ("server.live_state", "dashboard_state.live_state"):
            patcher = patch(target, self.state)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = str(Path(self.temp.name) / "sensor.db")

    def client(self):
        init_db(self.db)
        return create_app(self.db).test_client()

    def legacy_log(self, rows, attempts=()):
        with sqlite3.connect(self.db) as conn:
            conn.executescript(PREVIOUS_SCHEMA)
            conn.executemany("""insert into readings (event_id, device_id, seq, suhu, kelembapan,
                sent_ms, protokol, waktu_diterima, synced_at, local_write_ms, session_id, source_mode)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", rows)
            conn.executemany("insert into delivery_attempts values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", attempts)
        conn.close()

    def test_fresh_schema_has_relations_constraints_and_known_devices(self):
        self.client()
        with connect_db(self.db) as conn:
            readings_fk = [tuple(row)[2:5] for row in conn.execute("pragma foreign_key_list(readings)")]
            alerts_fk = {row["table"]: row["on_delete"] for row in conn.execute("pragma foreign_key_list(alerts)")}
            devices = {row["device_id"]: row["status"] for row in conn.execute("select * from devices")}
        self.assertEqual(readings_fk, [("devices", "device_id", "device_id")])
        self.assertEqual(alerts_fk, {"devices": "NO ACTION", "readings": "CASCADE"})
        self.assertEqual(devices, {"esp32-01": "aktif", "demo-node-01": "aktif"})
        insert = """insert into readings (event_id, device_id, protokol, seq, suhu, kelembapan,
            sent_ms, waktu_diterima) values (?, ?, 'HTTP', 1, ?, 50, 0, '2026-11-02T00:00:00.000+00:00')"""
        for event, device, suhu in (("unknown-device", "nowhere", 25), ("too-hot", "esp32-01", 200)):
            with self.subTest(event=event), self.assertRaises(sqlite3.IntegrityError):
                with connect_db(self.db) as conn:
                    conn.execute(insert, (event, device, suhu))

    def test_reading_registers_device_and_stores_most_severe_alert_once(self):
        client = self.client()
        hot = {**SAMPLE, "device_id": "esp32-lab", "suhu": 33.5, "kelembapan": 85.0}
        first = client.post("/update", json=hot)
        self.assertEqual(client.post("/update", json=hot).json, first.json)
        client.post("/update", json={**hot, "seq": 2, "suhu": 30.5, "kelembapan": 45.0})
        client.post("/update", json={**SAMPLE, "seq": 3})
        with connect_db(self.db) as conn:
            device = conn.execute("select * from devices where device_id = 'esp32-lab'").fetchone()
            alerts = conn.execute("""select r.seq, a.parameter, a.severity, a.nilai, a.ambang, a.waktu = r.waktu_diterima
                from alerts a join readings r on r.event_id = a.event_id order by r.seq, a.parameter""").fetchall()
        self.assertEqual((device["nama"], device["status"]), ("esp32-lab", "aktif"))
        self.assertEqual(device["pertama_terlihat"], first.json["waktu_diterima"])
        self.assertGreater(device["terakhir_terlihat"], device["pertama_terlihat"])
        self.assertEqual([tuple(row) for row in alerts], [
            (1, "kelembapan", "peringatan", 85.0, 80.0, 1), (1, "suhu", "kritis", 33.5, 32.0, 1),
            (2, "kelembapan", "peringatan", 45.0, 50.0, 1), (2, "suhu", "peringatan", 30.5, 30.0, 1)])
        snapshot = client.get("/api/dashboard?device_id=esp32-lab").json
        self.assertEqual(snapshot["alert_totals"], {"total": 4, "kritis": 1, "peringatan": 3})
        self.assertEqual(snapshot["alerts"][0]["seq"], 2)
        self.assertEqual(snapshot["hourly"][0]["jumlah"], 2)
        self.assertEqual(client.get("/api/dashboard?device_id=esp32-01").json["alert_totals"]["total"], 0)

    def test_inactive_device_is_rejected_over_http_and_mqtt(self):
        client = self.client()
        with connect_db(self.db) as conn:
            conn.execute("update devices set status = 'nonaktif' where device_id = 'esp32-01'")
        response = client.post("/update", json=SAMPLE)
        self.assertEqual(response.status_code, 403)
        broker = Mock()
        handle_mqtt_message(self.db, broker, Mock(topic="sensor/dht22", payload=json.dumps(SAMPLE).encode()))
        broker.publish.assert_not_called()
        self.assertEqual(client.get("/api/dashboard").json["totals"]["total"], 0)

    def test_previous_log_is_rebuilt_with_constraints_without_losing_rows(self):
        rows = [("old-1", "esp32-01", 1, 29.4, 71.2, 5200, "HTTP", "2026-09-28T08:06:55.740+00:00",
                 "2026-09-28T08:06:56.000+00:00", 3.5, None, "unknown"),
                ("old-2", "demo-node-01", 7, 33.0, 82.0, 14000, "MQTT", "2026-10-05T01:00:00.000+00:00",
                 None, 2.0, "s1", "demo"),
                ("old-3", "demo-node-01", 8, 27.0, 60.0, 16000, "HTTP", "2026-10-05T01:00:02.000+00:00",
                 None, None, "s1", "demo")]
        attempts = [("a-1", "demo-node-01", "s1", "demo", 7, "MQTT", "ok", 12.5,
                     "2026-10-05T01:00:00.100+00:00", None)]
        self.legacy_log(rows, attempts)
        client = self.client()
        init_db(self.db)  # A second start must not rebuild again.
        with connect_db(self.db) as conn:
            copied = [tuple(row) for row in conn.execute("""select event_id, device_id, seq, suhu, kelembapan,
                sent_ms, protokol, waktu_diterima, synced_at, local_write_ms, session_id, source_mode
                from readings order by event_id""")]
            devices = {row["device_id"]: (row["pertama_terlihat"], row["terakhir_terlihat"])
                       for row in conn.execute("select * from devices")}
            alerts = [tuple(row) for row in conn.execute("select event_id, parameter, severity from alerts order by 2")]
            indexes = {row["name"] for row in conn.execute("select name from sqlite_master where type = 'index'")}
            attempts_sql = conn.execute("select sql from sqlite_master where name = 'delivery_attempts'").fetchone()[0]
            self.assertEqual(conn.execute("pragma foreign_key_check").fetchall(), [])
            self.assertEqual(conn.execute("pragma integrity_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("select count(*) from delivery_attempts").fetchone()[0], 1)
            leftovers = conn.execute("select name from sqlite_master where name like '%legacy%'").fetchall()
        self.assertEqual(copied, sorted(rows))
        self.assertEqual(devices["demo-node-01"], ("2026-10-05T01:00:00.000+00:00", "2026-10-05T01:00:02.000+00:00"))
        self.assertEqual(devices["esp32-01"][0], "2026-09-28T08:06:55.740+00:00")
        self.assertEqual(alerts, [("old-2", "kelembapan", "peringatan"), ("old-2", "suhu", "kritis")])
        self.assertTrue({"readings_received_idx", "readings_identity_idx", "readings_device_time_idx",
                         "attempts_reported_idx", "alerts_device_time_idx"} <= indexes)
        self.assertIn("check", attempts_sql.lower())
        self.assertEqual(leftovers, [])
        self.assertEqual(client.get("/api/dashboard").json["readings"][0]["rtt_ms"], None)
        retry = {"device_id": "demo-node-01", "session_id": "s1", "source_mode": "demo", "seq": 8,
                 "suhu": 27.0, "kelembapan": 60.0, "sent_ms": 16000}
        # The rebuilt table keeps the idempotency identity of historical HTTP rows.
        self.assertEqual(client.post("/update", json=retry).json["event_id"], "old-3")
        self.assertEqual(client.post("/update", json={**retry, "suhu": 34.0}).status_code, 409)

    def test_rows_breaking_new_rules_keep_the_previous_table(self):
        self.legacy_log([("bad", "esp32-01", 1, 29.4, 71.2, 5200, "COAP", "2026-09-28T08:06:55.740+00:00",
                          None, None, None, "unknown")])
        client = self.client()
        with connect_db(self.db) as conn:
            self.assertEqual(conn.execute("pragma foreign_key_list(readings)").fetchall(), [])
            self.assertEqual(conn.execute("select protokol from readings").fetchone()[0], "COAP")
            self.assertIn("check", conn.execute("select sql from sqlite_master where name = 'delivery_attempts'").fetchone()[0])
        self.assertEqual(client.post("/update", json=SAMPLE).status_code, 200)
        self.assertEqual(client.get("/api/dashboard").json["totals"]["total"], 2)

    def test_database_page_reports_live_structure_and_read_only_queries(self):
        client = self.client()
        client.post("/update", json={**SAMPLE, "device_id": "demo-node-01", "source_mode": "demo", "suhu": 31})
        page = client.get("/database")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Database explorer", page.data)
        page.close()
        data = client.get("/api/database").json
        tables = {table["name"]: table for table in data["tables"]}
        self.assertEqual(list(tables), ["devices", "readings", "alerts", "delivery_attempts"])
        columns = {column["name"]: column for column in tables["readings"]["columns"]}
        self.assertEqual(columns["device_id"]["fk"], "devices(device_id)")
        self.assertEqual(columns["suhu"]["checks"], ["suhu between -40 and 125"])
        self.assertEqual(columns["event_id"]["comment"], "UUID dari server")
        self.assertTrue(columns["event_id"]["pk"] and columns["event_id"]["notnull"])
        alert_columns = {column["name"]: column for column in tables["alerts"]["columns"]}
        self.assertEqual(alert_columns["event_id"]["fk"], "readings(event_id) on delete cascade")
        self.assertEqual(tables["alerts"]["constraints"], ["unique (event_id, parameter)"])
        identity = next(index for index in tables["readings"]["indexes"] if index["name"] == "readings_identity_idx")
        self.assertEqual((identity["unique"], identity["where"]), (True, "session_id is not null"))
        self.assertEqual(len(data["queries"]), 5)
        self.assertTrue(all("error" not in query for query in data["queries"]))
        first = data["queries"][0]
        self.assertEqual(len(first["rows"]), 1)
        self.assertTrue(any("readings_device_time_idx" in step for step in first["plan"]))
        with patch("dashboard_state.load_queries", return_value=[{"judul": "x", "tujuan": "", "sql": "delete from readings"}]):
            self.assertIn("readonly", database_overview(self.db)["queries"][0]["error"])
        self.assertEqual(client.get("/api/dashboard").json["totals"]["total"], 1)

    def test_definition_parser_keeps_comments_with_their_column(self):
        body = """
    a text not null,              -- kolom a, dengan koma
    b real                        -- kolom b
      check (b between 0 and (10 + 5)),
    c text check (c in ('x,y', 'z')), d integer,
    unique (a, b)
"""
        parts = split_definitions(body)
        self.assertEqual(len(parts), 5)
        self.assertIn("kolom a, dengan koma", parts[0])
        self.assertIn("kolom b", parts[1])
        self.assertEqual(check_expressions(parts[1]), ["b between 0 and (10 + 5)"])
        self.assertEqual(check_expressions(parts[2]), ["c in ('x,y', 'z')"])


if __name__ == "__main__":
    unittest.main()
