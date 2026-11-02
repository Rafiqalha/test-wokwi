"""Read-only dashboard data and local server-sent event notifications."""

import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


QUERIES_PATH = Path(__file__).with_name("database") / "queries.sql"
TABLE_NOTES = {
    "devices": "Metadata perangkat (master)",
    "readings": "Telemetri sensor, satu baris per pembacaan",
    "alerts": "Alarm ketika nilai melewati ambang",
    "delivery_attempts": "Laporan RTT dari pengirim",
}
TABLE_CONSTRAINTS = ("unique", "check", "primary", "foreign", "constraint")
CHECK_PATTERN = re.compile(r"\bcheck\s*\(", re.IGNORECASE)
COMMENT_PATTERN = re.compile(r"--([^\n]*)")


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
        alerts = conn.execute("""select a.alert_id, a.waktu, a.device_id, d.nama,
            a.parameter, a.nilai, a.ambang, a.severity, a.pesan, r.protokol, r.seq
            from alerts a join readings r on r.event_id = a.event_id
            join devices d on d.device_id = a.device_id""" + where +
            " order by a.waktu desc, a.alert_id desc limit 8", args).fetchall()
        alert_totals = conn.execute("""select count(*) as total,
            sum(a.severity = 'kritis') as kritis, sum(a.severity = 'peringatan') as peringatan
            from alerts a join readings r on r.event_id = a.event_id""" + where, args).fetchone()
        hourly = conn.execute("""select strftime('%Y-%m-%dT%H:00:00Z', r.waktu_diterima) as jam_utc,
            count(*) as jumlah, round(avg(r.suhu), 2) as suhu_rata, min(r.suhu) as suhu_min,
            max(r.suhu) as suhu_maks, round(avg(r.kelembapan), 2) as kelembapan_rata
            from readings r""" + where +
            " group by jam_utc order by jam_utc desc limit 12", args).fetchall()
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
        "alerts": [dict(row) for row in alerts],
        "alert_totals": {name: value or 0 for name, value in dict(alert_totals).items()},
        "hourly": [dict(row) for row in hourly],
        "window_limit": limit,
    }


def split_definitions(body):
    """Split a CREATE TABLE body on top-level commas; a trailing comment stays with its column."""
    parts, start, depth, index, quote = [], 0, 0, 0, None
    while index < len(body):
        char = body[index]
        if quote:
            quote = None if char == quote else quote
        elif char in "'\"":
            quote = char
        elif body.startswith("--", index):
            index = body.find("\n", index)
            if index < 0:
                break
            continue
        elif char in "()":
            depth += 1 if char == "(" else -1
        elif char == "," and depth == 0:
            line_end = body.find("\n", index)
            line_end = len(body) if line_end < 0 else line_end
            rest = body[index + 1:line_end].strip()
            cut = line_end if not rest or rest.startswith("--") else index + 1
            parts.append(body[start:cut])
            start = index = cut
            continue
        index += 1
    parts.append(body[start:])
    return [part for part in parts if part.strip()]


def check_expressions(code):
    """Return the expressions of every CHECK (...) clause in a definition."""
    found = []
    for match in CHECK_PATTERN.finditer(code):
        depth = 0
        for end in range(match.end() - 1, len(code)):
            depth += {"(": 1, ")": -1}.get(code[end], 0)
            if depth == 0:
                found.append(" ".join(code[match.end():end].split()))
                break
    return found


def table_structure(conn, name):
    """Describe columns, keys, CHECK rules, comments and indexes from the live schema."""
    sql = conn.execute("select sql from sqlite_master where type = 'table' and name = ?",
                       (name,)).fetchone()[0]
    described, constraints = {}, []
    for part in split_definitions(sql[sql.index("(") + 1:sql.rindex(")")]):
        comment = " ".join(text.strip() for text in COMMENT_PATTERN.findall(part))
        code = COMMENT_PATTERN.sub("", part).strip().rstrip(",").strip()
        if not code:
            continue
        first = code.split(None, 1)[0].strip('"`[]').lower()
        if first in TABLE_CONSTRAINTS:
            constraints.append(" ".join(code.split()))
        else:
            described[first] = {"checks": check_expressions(code), "comment": comment}
    foreign = {}
    for row in conn.execute(f'pragma foreign_key_list("{name}")'):
        action = "" if row["on_delete"] == "NO ACTION" else f" on delete {row['on_delete'].lower()}"
        foreign[row["from"]] = f"{row['table']}({row['to']}){action}"
    columns = [{"name": row["name"], "type": row["type"].upper(),
                "notnull": bool(row["notnull"]), "default": row["dflt_value"],
                "pk": bool(row["pk"]), "fk": foreign.get(row["name"]),
                **described.get(row["name"].lower(), {"checks": [], "comment": ""})}
               for row in conn.execute(f'pragma table_info("{name}")')]
    indexes = []
    for row in conn.execute(f'pragma index_list("{name}")'):
        keys = [info["name"] + (" DESC" if info["desc"] else "")
                for info in conn.execute(f'pragma index_xinfo("{row["name"]}")')
                if info["key"] and info["name"] is not None]
        definition = conn.execute("select sql from sqlite_master where type = 'index' and name = ?",
                                  (row["name"],)).fetchone()
        where = None
        if row["partial"] and definition and definition[0]:
            where = " ".join(re.split(r"\bwhere\b", definition[0], flags=re.IGNORECASE)[-1].split())
        indexes.append({"name": row["name"], "columns": keys, "unique": bool(row["unique"]),
                        "origin": {"pk": "primary key", "u": "unique"}.get(row["origin"], "index"),
                        "where": where})
    count = conn.execute(f'select count(*) from "{name}"').fetchone()[0]
    return {"name": name, "note": TABLE_NOTES.get(name, ""), "rows": count, "columns": columns,
            "constraints": constraints, "indexes": indexes}


def load_queries(path=QUERIES_PATH):
    """Read statements headed by '-- judul:' and '-- tujuan:' comments."""
    queries, pending, meta = [], "", {}
    for line in Path(path).read_text(encoding="utf-8").splitlines(keepends=True):
        stripped = line.strip()
        key = next((name for name in ("judul", "tujuan") if stripped.startswith(f"-- {name}:")), None)
        if key and not pending:
            meta[key] = stripped.split(":", 1)[1].strip()
            continue
        if pending or (stripped and not stripped.startswith("--")):
            pending += line
        if pending and sqlite3.complete_statement(pending):
            queries.append({"judul": meta.get("judul", f"Query {len(queries) + 1}"),
                            "tujuan": meta.get("tujuan", ""), "sql": pending.strip()})
            pending, meta = "", {}
    return queries


def run_query(conn, query, row_limit):
    started = time.perf_counter()
    try:
        cursor = conn.execute(query["sql"])
        rows = cursor.fetchmany(row_limit + 1)
        elapsed = (time.perf_counter() - started) * 1000
        plan = [row[3] for row in conn.execute("explain query plan " + query["sql"])]
    except sqlite3.Error as exc:
        return {**query, "error": str(exc)}
    return {**query, "columns": [item[0] for item in cursor.description],
            "rows": [list(row) for row in rows[:row_limit]], "truncated": len(rows) > row_limit,
            "elapsed_ms": round(elapsed, 3), "plan": plan}


def database_overview(db_path, query_path=QUERIES_PATH, row_limit=20):
    """Table structure, row counts and analysis queries, read in one read-only snapshot."""
    if not Path(db_path).exists():
        raise sqlite3.OperationalError("Database log lokal belum tersedia")
    conn = sqlite3.connect(db_path, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("pragma query_only=on")  # The page can never modify the log.
        conn.execute("begin")
        names = [row["name"] for row in conn.execute(
            "select name from sqlite_master where type = 'table' and name not like 'sqlite_%'")]
        ordered = [name for name in TABLE_NOTES if name in names]
        ordered += sorted(name for name in names if name not in TABLE_NOTES)
        tables = [table_structure(conn, name) for name in ordered]
        queries = [run_query(conn, query, row_limit) for query in load_queries(query_path)]
        journal = conn.execute("pragma journal_mode").fetchone()[0]
    finally:
        conn.close()
    wal = Path(f"{db_path}-wal")
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "database": {"file": str(db_path), "bytes": os.path.getsize(db_path),
                     "wal_bytes": wal.stat().st_size if wal.exists() else 0,
                     "engine": f"SQLite {sqlite3.sqlite_version}", "journal_mode": journal},
        "tables": tables,
        "queries": queries,
    }
