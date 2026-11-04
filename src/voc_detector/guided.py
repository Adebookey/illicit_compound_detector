"""`voc-detector guided`: a step-by-step data collection session.

The background service keeps logging sensor readings every second. This
command tells you exactly when to place and remove the compound, shows the
live readings, and records the labelled periods automatically, so no times
need to be written down.

One session (default settings, about 95 minutes):
  10 min clean-air baseline
  4 rounds of:  10 min compound  ->  3 min recovery (not used)  ->  10 min clean
  plus optional confounder rounds (perfume, sanitiser, ...)
"""

import datetime as dt
import sqlite3
import sys
import time

from .schema import CLEAN_LABEL, POSITIVE_LABEL
from .storage import Storage

PLACEMENTS = [
    "about 10 cm from the device, container OPEN",
    "about 30 cm from the device, container OPEN",
    "about 5 cm from the device, container OPEN",
    "about 1 m from the device, container OPEN",
    "about 10 cm from the device, inside a CLOSED bag/container",
    "about 50 cm from the device, container OPEN",
]

REMOVE = ("REMOVE the compound: seal it and take it OUT of the room. "
          "Air the room briefly if you can.")


class StoppedEarly(Exception):
    pass


def _beep():
    sys.stdout.write("\a")
    sys.stdout.flush()


def _latest(store):
    try:
        return store.db.execute(
            "SELECT ts, ens_tvoc, ens_eco2, sgp_voc_index, temp_c, rh FROM samples "
            "ORDER BY ts DESC LIMIT 1").fetchone()
    except sqlite3.Error:
        return None


def _fmt(v, nd=0, unit=""):
    return "  -  " if v is None else f"{v:.{nd}f}{unit}"


def _live_wait(store, seconds, title):
    """Count down while showing live readings. Ctrl+C raises StoppedEarly."""
    end = time.time() + seconds
    try:
        while True:
            left = end - time.time()
            if left <= 0:
                break
            r = _latest(store)
            if r and time.time() - r[0] < 10:
                live = (f"TVOC {_fmt(r[1])} ppb | eCO2 {_fmt(r[2])} | VOC idx {_fmt(r[3])} | "
                        f"{_fmt(r[4], 1, 'C')} {_fmt(r[5], 0, '%')}")
            else:
                live = "!! no new readings - is the service running? (sudo systemctl status voc-detector)"
            m, s = divmod(int(left), 60)
            sys.stdout.write(f"\r  {title:<10} {m:02d}:{s:02d} left | {live}   ")
            sys.stdout.flush()
            time.sleep(1)
    except KeyboardInterrupt:
        sys.stdout.write("\n")
        raise StoppedEarly() from None
    sys.stdout.write("\n")


def _period(store, label, minutes, session, notes, title):
    lid = store.start_label(label, session, notes)
    try:
        _live_wait(store, minutes * 60, title)
    finally:
        store.end_label(lid)
    _beep()


def _ask(msg):
    _beep()
    print()
    try:
        input(f">>> {msg}\n    Press Enter when done... ")
    except (KeyboardInterrupt, EOFError):
        raise StoppedEarly() from None


def _running_minutes(store):
    """How long the logger has been running without a gap (minutes)."""
    rows = store.db.execute("SELECT ts FROM samples WHERE ts > ? ORDER BY ts DESC",
                            (time.time() - 3600,)).fetchall()
    if not rows or time.time() - rows[0][0] > 10:
        return None
    start = rows[0][0]
    for (t,) in rows[1:]:
        if start - t > 30:
            break
        start = t
    return (time.time() - start) / 60


def run(db_path, rounds=4, compound_min=10.0, clean_min=10.0, recovery_min=3.0,
        baseline_min=10.0, confounders=(), confounder_min=5.0, session=None, warmup_min=15.0):
    store = Storage(db_path)
    session = session or "S" + dt.datetime.now().strftime("%Y%m%d-%H%M")

    up = _running_minutes(store)
    if up is None:
        sys.exit("The detector service is not logging. Start it first:\n"
                 "  sudo systemctl start voc-detector\n  journalctl -u voc-detector -f")

    total = (baseline_min + rounds * (compound_min + recovery_min + clean_min)
             + len(confounders) * (confounder_min + recovery_min + clean_min))
    print(f"\nGuided session '{session}': {rounds} compound rounds"
          f"{', confounders: ' + ', '.join(confounders) if confounders else ''}")
    print(f"Takes about {total:.0f} minutes. Keep the device in the same place throughout.")
    print("Ctrl+C stops at any time; everything recorded so far is kept.\n")
    print("Before starting: the compound must be SEALED and OUTSIDE this room.")

    try:
        if up < warmup_min:
            print(f"\nSensors have only been running {up:.0f} min. Warming up first "
                  f"(they need ~{warmup_min:.0f} min to settle).")
            _live_wait(store, (warmup_min - up) * 60, "warm-up")
        _ask("Ready? Compound sealed and out of the room, nobody spraying/cooking nearby.")
        print("Recording clean-air baseline...")
        _period(store, CLEAN_LABEL, baseline_min, session, "baseline", "baseline")

        for i in range(rounds):
            where = PLACEMENTS[i % len(PLACEMENTS)]
            _ask(f"Round {i + 1}/{rounds}: PLACE the compound {where}.")
            _period(store, POSITIVE_LABEL, compound_min, session, f"round {i + 1}: {where}", "COMPOUND")
            _ask(REMOVE)
            print("Letting the sensors recover (this part is not used for training)...")
            _live_wait(store, recovery_min * 60, "recovery")
            _period(store, CLEAN_LABEL, clean_min, session, f"after round {i + 1}", "clean")

        for name in confounders:
            label = f"confounder:{name.strip().lower().replace(' ', '_')}"
            _ask(f"OTHER SMELL: put '{name}' near the device (about 20-50 cm), "
                 "the way it would occur normally (e.g. one spray of perfume).")
            _period(store, label, confounder_min, session, name, name[:10])
            _ask(f"Remove '{name}' and air the room if you can.")
            _live_wait(store, recovery_min * 60, "recovery")
            _period(store, CLEAN_LABEL, clean_min, session, f"after {name}", "clean")
    except StoppedEarly:
        print("\nStopped early. Everything recorded up to now is saved.")

    labs = [lb for lb in store.labels() if lb["session"] == session]
    mins = {}
    for lb in labs:
        if lb["end_ts"]:
            mins[lb["label"]] = mins.get(lb["label"], 0) + (lb["end_ts"] - lb["start_ts"]) / 60
    print(f"\nSession '{session}' saved:")
    for k, v in sorted(mins.items()):
        print(f"  {k:<24} {v:5.0f} min")
    n_sessions = len({lb["session"] for lb in store.labels()
                      if lb["label"] == POSITIVE_LABEL and lb["end_ts"]})
    print(f"\nSessions with compound data so far: {n_sessions}.")
    if n_sessions < 3:
        print("Collect at least 3 sessions (ideally on different days/times) before training.")
    else:
        print("You can train now:  voc-detector train")
