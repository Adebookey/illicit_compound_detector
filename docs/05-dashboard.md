# 5. Remote dashboard (Supabase + Vercel)

```
 Pi ──(HTTPS, device key)──▶ Supabase (Postgres) ◀──(sign-in)── Dashboard on Vercel ◀── client's browser/phone
```

- Each device sends a reading every 10 s, a heartbeat (battery, Wi-Fi, health)
  every 60 s, and events (detection start/end, low battery, boot) immediately.
  If Wi-Fi drops, everything is queued on the Pi and sent when it returns.
- The dashboard shows each device's status:

| Status | Meaning |
|---|---|
| ✓ Online | heartbeats arriving, battery fine |
| ⚠ COMPOUND DETECTED | alarm active right now |
| ▼ Low battery | ≤ 20 % and not charging |
| ✕ Offline | no heartbeat for 3 minutes |
| ⚡ Offline – needs charge | went silent while its battery was low: it most likely ran flat |

- Per device: live tiles, charts (score, TVOC, VOC index, eCO2, temperature,
  humidity, battery) for 1 h / 24 h / 7 d / 30 d, an event log with
  "acknowledge", and CSV download of the raw readings.
- With the dashboard open, the browser shows a notification for detections,
  low battery and devices going offline (click **Enable alerts** once).

## Setup

### 1. Supabase project

1. Create a project at supabase.com (free tier is enough for a few devices).
2. **SQL Editor** → paste and run `dashboard/supabase/migrations/0001_init.sql`.
3. Optional: run `0002_retention.sql` (deletes data older than 90 days).
4. **Authentication → Sign In / Providers**: turn **off** "Allow new users to sign up".
5. **Authentication → Users → Add user**: create a login (email + password)
   for the client, and one for yourself.
6. **Project Settings → API**: copy the **Project URL** and the **anon public** key.

### 2. Register each device

In the SQL Editor:

```sql
select register_device('detector-01', 'Front office', 'Head office, Lagos');
```

It returns a key **once**. Put it in the Pi's `/etc/voc-detector/config.toml`:

```toml
[device]
id = "detector-01"

[cloud]
enabled = true
supabase_url = "https://xxxx.supabase.co"
supabase_anon_key = "eyJ..."
device_key = "the key returned above"
```

`sudo systemctl restart voc-detector`, and within a minute the device appears
online. Lost the key? Run `register_device` again with the same id (it issues a
new key; update the Pi).

### 3. Deploy the dashboard

1. Copy `dashboard/web/config.example.js` to `dashboard/web/config.js` and fill in
   the Project URL and anon key (they are public by design; the data is protected
   by sign-in and row-level security).
2. On vercel.com: **Add New → Project**, import this GitHub repo, set **Root
   Directory** to `dashboard/web`, framework preset **Other**, no build command.
   Deploy.
3. Open the URL, sign in with the user you created.

(Any static host works too: Netlify, GitHub Pages, or simply opening
`index.html` from a local web server.)

## Security notes

- A device can only write its own data through `ingest()`, and only with its key.
- The anon key alone can't read or write any table.
- Dashboard users can read all devices and acknowledge events; they can't
  change or delete data.
- Readings may be legally sensitive. Keep the Supabase project's region and
  access list appropriate for the client.
