-- VOC detector cloud schema.
--
-- Security model
--   * Devices hold a per-device secret key. They can ONLY call ingest(),
--     which checks the key against a bcrypt hash. The public anon key on its
--     own cannot read or write any table.
--   * Dashboard users sign in with Supabase Auth (email). Signed-in users can
--     read everything and acknowledge events. Create users in the Supabase
--     dashboard (Authentication > Users > Add user) and disable public sign-ups.

create extension if not exists pgcrypto with schema extensions;

-- ---------------------------------------------------------------- tables
create table public.devices (
  id                   text primary key,
  name                 text not null,
  location             text,
  key_hash             text not null,
  heartbeat_interval_s int  not null default 60,
  created_at           timestamptz not null default now(),
  last_seen            timestamptz,
  last_heartbeat       jsonb,
  last_reading         jsonb,
  detecting            boolean not null default false
);

create table public.readings (
  id            bigint generated always as identity primary key,
  device_id     text not null references public.devices(id) on delete cascade,
  ts            timestamptz not null,
  ens_tvoc      real, ens_eco2 real, ens_aqi real,
  sgp_raw       real, sgp_voc_index real,
  temp_c        real, rh real,
  score         real, prob real,
  detected      boolean
);
create index readings_device_ts on public.readings (device_id, ts desc);

create table public.heartbeats (
  id             bigint generated always as identity primary key,
  device_id      text not null references public.devices(id) on delete cascade,
  ts             timestamptz not null,
  battery_pct    real, battery_v real, charging boolean,
  cpu_temp_c     real, wifi_rssi real, uptime_s bigint, free_disk_mb int,
  model_version  text,
  details        jsonb
);
create index heartbeats_device_ts on public.heartbeats (device_id, ts desc);

create table public.events (
  id             bigint generated always as identity primary key,
  device_id      text not null references public.devices(id) on delete cascade,
  ts             timestamptz not null,
  kind           text not null,  -- detection_start, detection_end, low_battery, battery_shutdown, boot, shutdown
  details        jsonb,
  acknowledged   boolean not null default false,
  acknowledged_by uuid,
  acknowledged_at timestamptz
);
create index events_device_ts on public.events (device_id, ts desc);

-- ---------------------------------------------------------------- status view
-- status: online | detecting | low_battery | offline | offline_battery
-- "offline_battery" = went silent while its battery was low and not charging,
-- i.e. it most likely needs a charge.
create or replace view public.device_status with (security_invoker = true) as
select
  d.id, d.name, d.location, d.last_seen, d.detecting, d.heartbeat_interval_s,
  (d.last_heartbeat->>'battery_pct')::real  as battery_pct,
  (d.last_heartbeat->>'charging')::boolean  as charging,
  d.last_heartbeat->>'battery_source'       as battery_source,
  (d.last_heartbeat->>'wifi_rssi')::real    as wifi_rssi,
  (d.last_heartbeat->>'cpu_temp_c')::real   as cpu_temp_c,
  d.last_heartbeat->>'model_version'        as model_version,
  d.last_heartbeat->>'upload_error'         as upload_error,
  d.last_heartbeat->'sensors'               as sensors,
  d.last_reading,
  d.last_heartbeat as heartbeat,
  case
    when d.last_seen is null
      or d.last_seen < now() - make_interval(secs => d.heartbeat_interval_s * 3) then
      case when coalesce((d.last_heartbeat->>'battery_pct')::real, 100) <= 25
                and not coalesce((d.last_heartbeat->>'charging')::boolean, false)
           then 'offline_battery' else 'offline' end
    when d.detecting then 'detecting'
    when coalesce((d.last_heartbeat->>'battery_pct')::real, 100) <= 20
         and not coalesce((d.last_heartbeat->>'charging')::boolean, false) then 'low_battery'
    else 'online'
  end as status,
  (select count(*) from public.events e
    where e.device_id = d.id and e.kind = 'detection_start' and not e.acknowledged) as unacked_detections
from public.devices d;

-- ---------------------------------------------------------------- ingest
create or replace function public.ingest(
  p_device_id text, p_key text,
  p_readings jsonb default '[]', p_events jsonb default '[]', p_heartbeats jsonb default '[]')
returns jsonb
language plpgsql
security definer
set search_path = public, extensions
as $$
declare
  dev public.devices;
  r jsonb;
  last_ts timestamptz;
begin
  select * into dev from public.devices where id = p_device_id;
  if dev.id is null or dev.key_hash <> crypt(p_key, dev.key_hash) then
    raise exception 'unknown device or bad key' using errcode = '28000';
  end if;

  insert into public.readings (device_id, ts, ens_tvoc, ens_eco2, ens_aqi, sgp_raw, sgp_voc_index,
                               temp_c, rh, score, prob, detected)
  select p_device_id, to_timestamp((x->>'ts')::float8),
         (x->>'ens_tvoc')::real, (x->>'ens_eco2')::real, (x->>'ens_aqi')::real,
         (x->>'sgp_raw')::real, (x->>'sgp_voc_index')::real,
         (x->>'temp_c')::real, (x->>'rh')::real,
         (x->>'score')::real, (x->>'prob')::real, (x->>'detected')::boolean
  from jsonb_array_elements(coalesce(p_readings, '[]')) x;

  insert into public.heartbeats (device_id, ts, battery_pct, battery_v, charging, cpu_temp_c,
                                 wifi_rssi, uptime_s, free_disk_mb, model_version, details)
  select p_device_id, to_timestamp((x->>'ts')::float8),
         (x->>'battery_pct')::real, (x->>'battery_v')::real, (x->>'charging')::boolean,
         (x->>'cpu_temp_c')::real, (x->>'wifi_rssi')::real, (x->>'uptime_s')::bigint,
         (x->>'free_disk_mb')::int, x->>'model_version', x
  from jsonb_array_elements(coalesce(p_heartbeats, '[]')) x;

  insert into public.events (device_id, ts, kind, details)
  select p_device_id, to_timestamp((x->>'ts')::float8), x->>'kind', x->'details'
  from jsonb_array_elements(coalesce(p_events, '[]')) x;

  -- keep the devices row current
  select x into r from jsonb_array_elements(coalesce(p_heartbeats, '[]')) x
    order by (x->>'ts')::float8 desc limit 1;
  if r is not null then
    update public.devices set last_heartbeat = r where id = p_device_id;
  end if;
  select x into r from jsonb_array_elements(coalesce(p_readings, '[]')) x
    order by (x->>'ts')::float8 desc limit 1;
  if r is not null then
    update public.devices set last_reading = r,
      detecting = coalesce((r->>'detected')::boolean, detecting) where id = p_device_id;
  end if;
  select x into r from jsonb_array_elements(coalesce(p_events, '[]')) x
    where x->>'kind' in ('detection_start', 'detection_end')
    order by (x->>'ts')::float8 desc limit 1;
  if r is not null then
    update public.devices set detecting = (r->>'kind' = 'detection_start') where id = p_device_id;
  end if;

  -- last_seen = newest timestamp in this batch (a backlog after an outage
  -- must not make an offline device look online)
  select max(to_timestamp((x->>'ts')::float8)) into last_ts from (
    select x from jsonb_array_elements(coalesce(p_readings, '[]')) x
    union all select x from jsonb_array_elements(coalesce(p_heartbeats, '[]')) x
    union all select x from jsonb_array_elements(coalesce(p_events, '[]')) x) s;
  update public.devices set last_seen = greatest(coalesce(last_seen, last_ts), least(last_ts, now()))
    where id = p_device_id;

  return jsonb_build_object('ok', true);
end $$;

-- ---------------------------------------------------------------- device registration
-- Run in the SQL editor:  select register_device('detector-01', 'Front office', 'Lagos HQ');
-- It returns the device key ONCE - copy it into the Pi's config.toml.
create or replace function public.register_device(p_id text, p_name text, p_location text default null)
returns text
language plpgsql
security definer
set search_path = public, extensions
as $$
declare k text := encode(gen_random_bytes(24), 'hex');
begin
  insert into public.devices (id, name, location, key_hash)
  values (p_id, p_name, p_location, crypt(k, gen_salt('bf')))
  on conflict (id) do update set key_hash = excluded.key_hash, name = excluded.name,
    location = coalesce(excluded.location, devices.location);
  return k;
end $$;


-- ---------------------------------------------------------------- chart helpers
-- Downsampled series for charts (keeps every chart to a few hundred points).
create or replace function public.readings_bucketed(
  p_device_id text, p_from timestamptz, p_to timestamptz, p_bucket_s int)
returns table (bucket timestamptz, tvoc real, eco2 real, voc_index real, temp real, humidity real,
               max_score real, any_detected boolean)
language sql stable security invoker set search_path = public as $$
  select to_timestamp(floor(extract(epoch from r.ts) / p_bucket_s) * p_bucket_s),
         avg(r.ens_tvoc)::real, avg(r.ens_eco2)::real, avg(r.sgp_voc_index)::real,
         avg(r.temp_c)::real, avg(r.rh)::real, max(r.score)::real, bool_or(r.detected)
  from public.readings r
  where r.device_id = p_device_id and r.ts >= p_from and r.ts < p_to
  group by 1 order by 1
$$;

create or replace function public.battery_bucketed(
  p_device_id text, p_from timestamptz, p_to timestamptz, p_bucket_s int)
returns table (bucket timestamptz, battery real, is_charging boolean)
language sql stable security invoker set search_path = public as $$
  select to_timestamp(floor(extract(epoch from h.ts) / p_bucket_s) * p_bucket_s),
         avg(h.battery_pct)::real, bool_or(h.charging)
  from public.heartbeats h
  where h.device_id = p_device_id and h.ts >= p_from and h.ts < p_to
  group by 1 order by 1
$$;

-- ---------------------------------------------------------------- permissions
alter table public.devices    enable row level security;
alter table public.readings   enable row level security;
alter table public.heartbeats enable row level security;
alter table public.events     enable row level security;

create policy "signed-in read devices"    on public.devices    for select to authenticated using (true);
create policy "signed-in read readings"   on public.readings   for select to authenticated using (true);
create policy "signed-in read heartbeats" on public.heartbeats for select to authenticated using (true);
create policy "signed-in read events"     on public.events     for select to authenticated using (true);
create policy "signed-in ack events"      on public.events     for update to authenticated using (true) with check (true);

-- Hide the key hash from the dashboard.
revoke select on public.devices from anon, authenticated;
grant select (id, name, location, heartbeat_interval_s, created_at, last_seen, last_heartbeat,
              last_reading, detecting) on public.devices to authenticated;
revoke all on public.readings, public.heartbeats, public.events from anon;
revoke insert, update, delete on public.readings, public.heartbeats, public.devices from authenticated;
revoke insert, delete on public.events from authenticated;
revoke update on public.events from authenticated;
grant update (acknowledged, acknowledged_by, acknowledged_at) on public.events to authenticated;
grant select on public.device_status to authenticated;
revoke all on public.device_status from anon;

revoke all on function public.ingest(text, text, jsonb, jsonb, jsonb) from public;
grant execute on function public.ingest(text, text, jsonb, jsonb, jsonb) to anon, authenticated;
revoke all on function public.readings_bucketed(text, timestamptz, timestamptz, int) from public, anon;
revoke all on function public.battery_bucketed(text, timestamptz, timestamptz, int) from public, anon;
grant execute on function public.readings_bucketed(text, timestamptz, timestamptz, int) to authenticated;
grant execute on function public.battery_bucketed(text, timestamptz, timestamptz, int) to authenticated;
revoke all on function public.register_device(text, text, text) from public, anon, authenticated;

-- live updates for the dashboard
alter publication supabase_realtime add table public.events;
