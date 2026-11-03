"""Command line: voc-detector {run,collect,status,export}."""

import argparse
import csv
import datetime as dt
import logging
import os
import sys
import time

from . import config as config_mod
from .schema import ALL_CHANNELS, CLEAN_LABEL, POSITIVE_LABEL
from .storage import Storage


def _db(cfg):
    return os.path.join(cfg["device"]["data_dir"], "voc.sqlite")


def cmd_run(args, cfg):
    from .agent import Agent
    if args.simulate:
        cfg["sensors"]["simulate"] = True
    Agent(cfg).run()


def cmd_collect(args, cfg):
    """Guided, labelled data collection.

    The service keeps logging in the background; this command only records
    WHEN the compound / clean air / a confounder was present.
    """
    label = args.label
    if label not in (POSITIVE_LABEL, CLEAN_LABEL) and not label.startswith("confounder:"):
        sys.exit(f"label must be '{POSITIVE_LABEL}', '{CLEAN_LABEL}' or 'confounder:<name>'")
    store = Storage(_db(cfg))
    session = args.session or dt.date.today().isoformat()
    input(f"Place the device for label '{label}' (session {session}). Press Enter to START...")
    lid = store.start_label(label, session, args.notes)
    end = time.time() + args.minutes * 60
    print(f"Recording '{label}' for {args.minutes} min. Ctrl+C to stop early.")
    try:
        while time.time() < end:
            left = int(end - time.time())
            print(f"\r  {left // 60:02d}:{left % 60:02d} remaining ", end="", flush=True)
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    store.end_label(lid)
    print("\nSaved. Tip: after a 'compound' run, record at least 10 min of 'clean' so the "
          "sensors recover before the next run.")


def cmd_status(args, cfg):
    store = Storage(_db(cfg))
    last = list(store.db.execute(
        f"SELECT ts, {','.join(ALL_CHANNELS)}, prob, score, active FROM samples "
        "ORDER BY ts DESC LIMIT 1"))
    n = store.db.execute("SELECT COUNT(*), MIN(ts), MAX(ts) FROM samples").fetchone()
    print(f"samples logged: {n[0]}  from {_fmt(n[1])} to {_fmt(n[2])}")
    if last:
        cols = ["ts"] + ALL_CHANNELS + ["prob", "score", "active"]
        for k, v in zip(cols, last[0]):
            print(f"  {k:>15}: {_fmt(v) if k == 'ts' else v}")
    print(f"upload queue: {store.outbox_size()} items")
    print(f"labelled periods: {len(store.labels())}")


def cmd_export(args, cfg):
    store = Storage(_db(cfg))
    start = time.time() - args.hours * 3600 if args.hours else None
    n = 0
    with open(args.out, "w", newline="") as f:
        w = csv.writer(f)
        cols = ["ts"] + ALL_CHANNELS
        w.writerow(cols)
        for s in store.iter_samples(start):
            w.writerow([s[c] for c in cols])
            n += 1
    print(f"wrote {n} samples to {args.out}")
    labels = store.labels()
    if labels:
        lp = os.path.splitext(args.out)[0] + "_labels.csv"
        with open(lp, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["start", "end", "label", "session", "notes"])
            for lb in labels:
                w.writerow([lb["start_ts"], lb["end_ts"], lb["label"], lb["session"], lb["notes"]])
        print(f"wrote {len(labels)} labelled periods to {lp}")


def _fmt(ts):
    return "-" if ts is None else dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def main(argv=None):
    p = argparse.ArgumentParser(prog="voc-detector")
    p.add_argument("-c", "--config", default=None, help="path to config.toml")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run the detector (what the service runs)")
    r.add_argument("--simulate", action="store_true", help="use fake sensors")

    c = sub.add_parser("collect", help="record a labelled period for training")
    c.add_argument("label", help="compound | clean | confounder:<name>")
    c.add_argument("-m", "--minutes", type=float, default=10)
    c.add_argument("-s", "--session", help="session name (default: today's date)")
    c.add_argument("-n", "--notes", default="")

    sub.add_parser("status", help="show the latest reading and queue size")

    e = sub.add_parser("export", help="export logged samples (+labels) to CSV")
    e.add_argument("out")
    e.add_argument("--hours", type=float, default=None, help="only the last N hours")

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = config_mod.load(args.config)
    {"run": cmd_run, "collect": cmd_collect, "status": cmd_status, "export": cmd_export}[args.cmd](args, cfg)


if __name__ == "__main__":
    main()
