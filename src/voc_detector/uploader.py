"""Uploads queued readings / events / heartbeats to Supabase.

Runs in its own thread with its own SQLite connection. Talks to a single
Postgres function `ingest` (see dashboard/supabase/migrations) through
Supabase's REST API. The device authenticates with its own device key; the
public "anon" key alone cannot write anything.

Uses only the standard library (urllib), so nothing extra to install.
"""

import json
import logging
import threading
import time
import urllib.error
import urllib.request

from .storage import Storage

log = logging.getLogger(__name__)


class Uploader(threading.Thread):
    def __init__(self, db_path, cloud_cfg, device_id):
        super().__init__(daemon=True, name="uploader")
        self.db_path = db_path
        self.url = cloud_cfg["supabase_url"].rstrip("/") + "/rest/v1/rpc/ingest"
        self.anon_key = cloud_cfg["supabase_anon_key"]
        self.device_key = cloud_cfg["device_key"]
        self.device_id = device_id
        self.interval = float(cloud_cfg.get("upload_interval_s", 15))
        self.max_outbox = int(cloud_cfg.get("max_outbox_rows", 200_000))
        self.stop_event = threading.Event()
        self.last_ok = None
        self.last_error = None

    def run(self):
        store = Storage(self.db_path)
        backoff = self.interval
        while not self.stop_event.is_set():
            try:
                store.trim_outbox(self.max_outbox)
                sent = self.flush(store)
                backoff = self.interval
                if sent >= 200:
                    continue  # more waiting, keep draining
            except Exception as e:  # noqa: BLE001
                self.last_error = f"{type(e).__name__}: {e}"
                log.warning("upload failed: %s", self.last_error)
                backoff = min(backoff * 2, 300)
            self.stop_event.wait(backoff)

    def flush(self, store):
        batch = store.peek_outbox(200)
        if not batch:
            return 0
        body = {"p_device_id": self.device_id, "p_key": self.device_key,
                "p_readings": [], "p_events": [], "p_heartbeats": []}
        for _, kind, payload in batch:
            body[{"reading": "p_readings", "event": "p_events",
                  "heartbeat": "p_heartbeats"}[kind]].append(payload)
        self._post(body)
        store.ack_outbox([i for i, _, _ in batch])
        self.last_ok = time.time()
        self.last_error = None
        return len(batch)

    def _post(self, body):
        req = urllib.request.Request(
            self.url, data=json.dumps(body).encode(), method="POST",
            headers={"apikey": self.anon_key,
                     "Authorization": f"Bearer {self.anon_key}",
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                r.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            raise RuntimeError(f"HTTP {e.code}: {detail}") from None
