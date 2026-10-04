"""Read-only dashboard data and local server-sent event notifications."""

import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


class LiveState:
    def __init__(self):
        self.condition = threading.Condition()
        self.revision = 0
        self.started_at = time.monotonic()
        self.mqtt = "connecting"
        self.supabase = "unknown"
        self.last_sync_at = None
        self.last_check_at = None
        self.sync_error = None

    def changed(self):
        with self.condition:
            self.revision += 1
            self.condition.notify_all()

    def update(self, **fields):
        with self.condition:
            if any(getattr(self, name) != value for name, value in fields.items()):
                for name, value in fields.items():
                    setattr(self, name, value)
                self.revision += 1
                self.condition.notify_all()

    def status(self):
        with self.condition:
            return {
                "revision": self.revision,
                "mqtt": self.mqtt,
                "supabase": self.supabase,
                "last_sync_at": self.last_sync_at,
                "last_check_at": self.last_check_at,
                "sync_error": self.sync_error,
                "uptime_seconds": int(time.monotonic() - self.started_at),
            }

    def events(self, last_revision=0):
        with self.condition:
            if last_revision > self.revision:
                last_revision = 0
        yield ": connected\n\n"
        while True:
            with self.condition:
                self.condition.wait_for(
                    lambda: self.revision > last_revision, timeout=15)
                revision = self.revision
            if revision > last_revision:
                last_revision = revision
                yield f"id: {revision}\nevent: update\ndata: {revision}\n\n"
            else:
                yield ": keepalive\n\n"


live_state = LiveState()


def dashboard_snapshot(db_path, limit=900, source_mode=None, device_id=None, session_id=None):
    """Return recent real readings and all-time totals; never expose API keys."""
    if not Path(db_path).exists():
        raise sqlite3.OperationalError("Database log lokal belum tersedia")
    conn = sqlite3.connect(db_path, timeout=5)
    conn.row_factory = sqlite3.Row
    filters = {name: value for name, value in
               (("source_mode", source_mode), ("device_id", device_id), ("session_id", session_id))
               if value is not None}
    where = " where " + " and ".join(f"r.{name}=?" for name in filters) if filters else ""
    args = list(filters.values())
    try:
        conn.execute("begin")  # Keep rows and totals from the same SQLite snapshot.
        totals = conn.execute("""select
            count(*) as total,
            sum(case when protokol = 'MQTT' then 1 else 0 end) as mqtt,
            sum(case when protokol = 'HTTP' then 1 else 0 end) as http,
            sum(case when synced_at is null then 1 else 0 end) as pending,
            max(waktu_diterima) as latest_received
            from readings r""" + where, args).fetchone()
        rows = conn.execute("""select r.event_id, r.device_id, r.seq, r.suhu,
            r.kelembapan, r.protokol, r.waktu_diterima, r.synced_at,
            r.local_write_ms, r.sync_request_ms, r.session_id, r.source_mode,
            a.rtt_ms, a.status as delivery_status
            from readings r left join delivery_attempts a on
            a.device_id=r.device_id and a.session_id=r.session_id
            and a.protokol=r.protokol and a.seq=r.seq""" + where +
            " order by r.waktu_diterima desc, r.rowid desc limit ?", [*args, limit]).fetchall()
        attempts = conn.execute("""select event_id, device_id, session_id, source_mode,
            seq, protokol, status, rtt_ms, waktu_dilaporkan, synced_at
            from delivery_attempts r""" + where +
            " order by waktu_dilaporkan desc, rowid desc limit ?", [*args, limit]).fetchall()
        attempt_totals = conn.execute("""select count(*) as total,
            sum(status='ok') as ok, sum(status='timeout') as timeout,
            sum(status='error') as error, sum(synced_at is null) as pending
            from delivery_attempts r""" + where, args).fetchone()
        sessions = conn.execute("""select distinct device_id, session_id, source_mode from readings
            union select distinct device_id, session_id, source_mode from delivery_attempts
            order by device_id, session_id""").fetchall()
    finally:
        conn.close()
    readings = [dict(row) for row in rows]
    latest = totals["latest_received"]
    sensor_recent = False
    if latest:
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(latest)).total_seconds()
            sensor_recent = 0 <= age < 20
        except ValueError:
            pass
    total = totals["total"] or 0
    pending = totals["pending"] or 0
    return {
        "server_time": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "totals": {"total": total, "mqtt": totals["mqtt"] or 0,
                   "http": totals["http"] or 0, "pending": pending,
                   "synced": total - pending},
        "status": {**live_state.status(), "sensor_recent": sensor_recent},
        "readings": readings,
        "attempts": [dict(row) for row in attempts],
        "attempt_totals": {name: value or 0 for name, value in dict(attempt_totals).items()},
        "sessions": [dict(row) for row in sessions],
        "window_limit": limit,
    }
