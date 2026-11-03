"""Local SQLite storage on the Pi.

  samples - every reading (1 per second) with model score; this is the
            device's own data log and the source of future training data
  outbox  - readings/events/heartbeats waiting to be uploaded; survives
            reboots and Wi-Fi outages
  labels  - start/end/label records written by `voc-detector collect`
  kv      - small persistent state (e.g. feature baselines)
"""

import json
import sqlite3
import time

from .schema import ALL_CHANNELS

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS samples (
    ts REAL PRIMARY KEY,
    {", ".join(f"{c} REAL" for c in ALL_CHANNELS)},
    prob REAL, score REAL, active INTEGER
);
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS labels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_ts REAL NOT NULL, end_ts REAL,
    label TEXT NOT NULL, session TEXT, notes TEXT
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
"""


class Storage:
    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=10, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(_SCHEMA)
        self.db.commit()
        self._pending = 0

    # samples ------------------------------------------------------------
    def add_sample(self, s, prob=None, score=None, active=None):
        cols = ["ts"] + ALL_CHANNELS + ["prob", "score", "active"]
        vals = [s.get("ts")] + [s.get(c) for c in ALL_CHANNELS] + [
            prob, score, None if active is None else int(bool(active))]
        self.db.execute(
            f"INSERT OR REPLACE INTO samples ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            vals)
        self._pending += 1
        if self._pending >= 30:  # batch commits to spare the SD card
            self.commit()

    def commit(self):
        self.db.commit()
        self._pending = 0

    def prune(self, keep_days):
        self.db.execute("DELETE FROM samples WHERE ts < ?", (time.time() - keep_days * 86400,))
        self.commit()

    def iter_samples(self, start=None, end=None):
        cols = ["ts"] + ALL_CHANNELS + ["prob", "score", "active"]
        q = f"SELECT {','.join(cols)} FROM samples WHERE ts >= ? AND ts <= ? ORDER BY ts"
        for row in self.db.execute(q, (start or 0, end or 1e12)):
            yield dict(zip(cols, row))

    # outbox -------------------------------------------------------------
    def enqueue(self, kind, payload):
        self.db.execute("INSERT INTO outbox (kind, payload) VALUES (?, ?)",
                        (kind, json.dumps(payload)))
        self.commit()

    def peek_outbox(self, limit=200):
        rows = self.db.execute(
            "SELECT id, kind, payload FROM outbox ORDER BY id LIMIT ?", (limit,)).fetchall()
        return [(i, k, json.loads(p)) for i, k, p in rows]

    def ack_outbox(self, ids):
        if ids:
            self.db.executemany("DELETE FROM outbox WHERE id = ?", [(i,) for i in ids])
            self.commit()

    def outbox_size(self):
        return self.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]

    def trim_outbox(self, max_rows):
        """Drop oldest *readings* if the device has been offline for a long time."""
        n = self.outbox_size()
        if n > max_rows:
            self.db.execute(
                "DELETE FROM outbox WHERE id IN (SELECT id FROM outbox WHERE kind='reading' "
                "ORDER BY id LIMIT ?)", (n - max_rows,))
            self.commit()

    # labels -------------------------------------------------------------
    def start_label(self, label, session=None, notes=None, ts=None):
        cur = self.db.execute(
            "INSERT INTO labels (start_ts, label, session, notes) VALUES (?, ?, ?, ?)",
            (ts or time.time(), label, session, notes))
        self.commit()
        return cur.lastrowid

    def end_label(self, label_id, ts=None):
        self.db.execute("UPDATE labels SET end_ts = ? WHERE id = ?", (ts or time.time(), label_id))
        self.commit()

    def labels(self):
        cols = ["id", "start_ts", "end_ts", "label", "session", "notes"]
        return [dict(zip(cols, r)) for r in self.db.execute(
            f"SELECT {','.join(cols)} FROM labels ORDER BY start_ts")]

    # kv -----------------------------------------------------------------
    def get(self, k, default=None):
        r = self.db.execute("SELECT v FROM kv WHERE k = ?", (k,)).fetchone()
        return json.loads(r[0]) if r else default

    def put(self, k, v):
        self.db.execute("INSERT OR REPLACE INTO kv (k, v) VALUES (?, ?)", (k, json.dumps(v)))
        self.commit()
