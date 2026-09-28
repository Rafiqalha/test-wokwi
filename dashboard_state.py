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
                "sync_error": self.sync_error,
                "uptime_seconds": int(time.monotonic() - self.started_at),
            }

    def events(self, last_revision=0):
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


def dashboard_snapshot(db_path, limit=900):
    """Return recent real readings and all-time totals; never expose API keys."""
    if not Path(db_path).exists():
        raise sqlite3.OperationalError("Database log lokal belum tersedia")
    conn = sqlite3.connect(db_path, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        totals = conn.execute("""select
            count(*) as total,
            sum(case when protokol = 'MQTT' then 1 else 0 end) as mqtt,
            sum(case when protokol = 'HTTP' then 1 else 0 end) as http,
            sum(case when synced_at is null then 1 else 0 end) as pending,
            max(waktu_diterima) as latest_received
            from readings""").fetchone()
        rows = conn.execute("""select event_id, device_id, seq, suhu,
            kelembapan, protokol, waktu_diterima, synced_at,
            local_write_ms, sync_request_ms
            from readings order by waktu_diterima desc limit ?""",
            (limit,)).fetchall()
    finally:
        conn.close()
    readings = [dict(row) for row in rows]
    latest = totals["latest_received"]
    sensor_recent = False
    if latest:
        try:
            sensor_recent = (datetime.now(timezone.utc) -
                             datetime.fromisoformat(latest)).total_seconds() < 20
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
        "window_limit": limit,
    }
