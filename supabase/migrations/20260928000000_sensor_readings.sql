create table if not exists public.sensor_readings (
  event_id uuid primary key,
  device_id text not null,
  seq bigint not null,
  suhu double precision not null,
  kelembapan double precision not null,
  sent_ms bigint not null,
  protokol text not null check (protokol in ('MQTT', 'HTTP')),
  waktu_diterima timestamptz not null
);

create index if not exists sensor_readings_received_idx
  on public.sensor_readings (waktu_diterima desc);

alter table public.sensor_readings enable row level security;

grant usage on schema public to service_role;
grant select, insert, update on public.sensor_readings to service_role;
