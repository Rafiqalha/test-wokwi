-- =========================================================================
-- Skema basis data IoT: penerima ESP32 + DHT22 proyek UTS (SQLite 3)
-- File database: data/sensor.db, dijalankan otomatis oleh init_db() di
-- server.py. Waktu disimpan sebagai teks ISO 8601 UTC, misalnya
-- 2026-11-02T03:15:20.123+00:00. Dashboard mengubahnya ke WIB saat tampil.
-- =========================================================================

-- 1) Metadata perangkat. Satu perangkat memiliki banyak pembacaan dan alarm.
create table if not exists devices (
    device_id         text primary key not null      -- ID unik perangkat
                      check (length(device_id) between 1 and 32),
    nama              text not null,                 -- nama tampilan
    lokasi            text,                          -- lokasi pemasangan
    status            text not null default 'aktif'  -- nonaktif: data ditolak
                      check (status in ('aktif', 'nonaktif')),
    pertama_terlihat  text,                          -- pembacaan pertama (UTC)
    terakhir_terlihat text                           -- pembacaan terakhir (UTC)
);

-- 2) Telemetri sensor: satu baris untuk setiap pembacaan yang diterima.
create table if not exists readings (
    event_id        text primary key not null,       -- UUID dari server
    device_id       text not null                    -- pengirim (FK)
                    references devices(device_id),
    session_id      text,                            -- sesi boot pengirim
    source_mode     text not null default 'unknown'  -- asal data
                    check (source_mode in ('dht22', 'random', 'demo', 'unknown')),
    protokol        text not null                    -- jalur pengiriman
                    check (protokol in ('MQTT', 'HTTP')),
    seq             integer not null                 -- nomor urut per sesi
                    check (seq between 0 and 4294967295),
    suhu            real not null                    -- derajat Celsius
                    check (suhu between -40 and 125),
    kelembapan      real not null                    -- persen RH
                    check (kelembapan between 0 and 100),
    sent_ms         integer not null                 -- millis() pengirim
                    check (sent_ms between 0 and 4294967295),
    waktu_diterima  text not null                    -- ingestion time (UTC)
                    check (waktu_diterima like '____-__-__T__:__:__%+00:00'),
    local_write_ms  real,                            -- durasi commit lokal
    synced_at       text,                            -- waktu salin Supabase
    sync_request_ms real                             -- durasi unggah batch
);
create index if not exists readings_received_idx
    on readings(waktu_diterima desc);
create index if not exists readings_device_time_idx
    on readings(device_id, waktu_diterima desc);
-- Kunci idempotensi: pengiriman ulang tidak membuat record ganda.
create unique index if not exists readings_identity_idx
    on readings(device_id, session_id, protokol, seq)
    where session_id is not null;

-- 3) Laporan RTT dari pengirim; boleh tiba sebelum pembacaannya.
create table if not exists delivery_attempts (
    event_id         text primary key not null,      -- UUID v5 dari identitas
    device_id        text not null,                  -- pengirim laporan
    session_id       text not null,                  -- sesi boot pengirim
    source_mode      text not null                   -- asal data
                     check (source_mode in ('dht22', 'random', 'demo')),
    seq              integer not null                -- seq pembacaan terkait
                     check (seq between 0 and 4294967295),
    protokol         text not null                   -- MQTT atau HTTP
                     check (protokol in ('MQTT', 'HTTP')),
    status           text not null                   -- hasil pengiriman
                     check (status in ('ok', 'timeout', 'error')),
    rtt_ms           real,                           -- kosong jika gagal
    waktu_dilaporkan text not null                   -- waktu laporan (UTC)
                     check (waktu_dilaporkan like '____-__-__T__:__:__%+00:00'),
    synced_at        text,                           -- waktu salin Supabase
    unique (device_id, session_id, protokol, seq),
    check ((status = 'ok' and rtt_ms is not null and rtt_ms between 0 and 120000)
           or (status <> 'ok' and rtt_ms is null))
);
create index if not exists attempts_reported_idx
    on delivery_attempts(waktu_dilaporkan desc);

-- 4) Alarm saat nilai melewati ambang. Maksimal satu alarm per parameter
--    untuk tiap pembacaan; alarm ikut terhapus jika pembacaannya dihapus.
create table if not exists alerts (
    alert_id   integer primary key autoincrement,    -- nomor alarm
    event_id   text not null                         -- pembacaan pemicu (FK)
               references readings(event_id) on delete cascade,
    device_id  text not null                         -- perangkat (FK)
               references devices(device_id),
    waktu      text not null,                        -- waktu pembacaan (UTC)
    parameter  text not null                         -- besaran yang dipantau
               check (parameter in ('suhu', 'kelembapan')),
    nilai      real not null,                        -- nilai terukur
    ambang     real not null,                        -- batas yang dilewati
    severity   text not null                         -- tingkat keparahan
               check (severity in ('peringatan', 'kritis')),
    pesan      text not null,                        -- keterangan aturan
    unique (event_id, parameter)
);
create index if not exists alerts_device_time_idx
    on alerts(device_id, waktu desc);

-- Perangkat yang sudah dikenal. Perangkat baru didaftarkan otomatis.
insert or ignore into devices (device_id, nama, lokasi) values
    ('esp32-01', 'ESP32 DevKit C + DHT22', 'Simulasi Wokwi'),
    ('demo-node-01', 'Node demo Python', 'Laptop (127.0.0.1)');
