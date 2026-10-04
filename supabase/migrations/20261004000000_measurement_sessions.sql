-- Keep historical readings unclassified; their origin cannot be reconstructed.
alter table public.sensor_readings
  add column if not exists session_id text,
  add column if not exists source_mode text not null default 'unknown';

create unique index if not exists sensor_readings_identity_idx
  on public.sensor_readings(device_id, session_id, protokol, seq)
  where session_id is not null;

create table if not exists public.delivery_attempts (
  event_id uuid primary key,
  device_id text not null,
  session_id text not null,
  source_mode text not null check (source_mode in ('dht22', 'random', 'demo')),
  seq bigint not null check (seq between 0 and 4294967295),
  protokol text not null check (protokol in ('MQTT', 'HTTP')),
  status text not null check (status in ('ok', 'timeout', 'error')),
  rtt_ms double precision,
  waktu_dilaporkan timestamptz not null,
  unique(device_id, session_id, protokol, seq),
  check ((status = 'ok' and rtt_ms is not null and rtt_ms between 0 and 120000)
      or (status <> 'ok' and rtt_ms is null))
);

create index if not exists delivery_attempts_reported_idx
  on public.delivery_attempts(waktu_dilaporkan desc);

alter table public.delivery_attempts enable row level security;
grant select, insert, update on public.delivery_attempts to service_role;
