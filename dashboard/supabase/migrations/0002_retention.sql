-- Optional: keep the cloud database small (free tier = 500 MB).
-- One reading every 10 s is ~8,600 rows/day/device, roughly 2 MB/day.
-- Requires the pg_cron extension (Database > Extensions > pg_cron).
create extension if not exists pg_cron;

select cron.schedule('voc-retention', '17 3 * * *', $$
  delete from public.readings   where ts < now() - interval '90 days';
  delete from public.heartbeats where ts < now() - interval '90 days';
$$);
