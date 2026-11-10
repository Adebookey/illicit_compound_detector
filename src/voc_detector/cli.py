"""Command line: voc-detector {run,guided,collect,status,export,train}."""

import argparse
import csv
import datetime as dt
import logging
import os
import shutil
import subprocess
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


def cmd_check(args, cfg):
    """Quick health check after installing: service, each sensor, battery, model."""
    ok = True
    active = subprocess.run(["systemctl", "is-active", "voc-detector"], capture_output=True,
                            text=True).stdout.strip() if shutil.which("systemctl") else "unknown"
    print(f"service ............ {active}")
    if active not in ("active", "unknown"):
        print("   start it: sudo systemctl start voc-detector ; log: journalctl -u voc-detector -n 50")
        ok = False
    if not os.path.exists(_db(cfg)):
        print("no data yet - wait a minute and run again")
        sys.exit(1)
    store = Storage(_db(cfg))
    newest = store.db.execute("SELECT MAX(ts) FROM samples").fetchone()[0]
    if newest is None or time.time() - newest > 30:
        print("readings ........... NONE recently (service starting, or crashed: see "
              "journalctl -u voc-detector -n 50)")
        sys.exit(1)
    # Rate over the last minute of saved data (saved every few seconds).
    rows = list(store.iter_samples(newest - 60, newest))
    span = rows[-1]["ts"] - rows[0]["ts"] if len(rows) > 1 else 0
    rate = (len(rows) - 1) / span if span > 0 else 0
    st = store.get("sensor_status") or {}
    slow = span >= 20 and rate < 0.8
    print(f"readings ........... {rate:.1f} per second"
          f"{'  (expected ~1.0: sampling is slow)' if slow else ''}")
    if slow and st.get("read_seconds"):
        print("   time per reading: " + ", ".join(f"{k} {v:.2f} s" for k, v in st["read_seconds"].items()))
    rows = [r for r in rows if r["ts"] > newest - 30]
    def seen(*cols):
        return any(r.get(c) is not None for r in rows for c in cols)
    last = rows[-1]
    checks = [
        ("ENS160", seen("ens_tvoc"), f"TVOC {last.get('ens_tvoc')} ppb, eCO2 {last.get('ens_eco2')}",
         "check wiring (3.3V, GND, SDA=GPIO2, SCL=GPIO3); `sudo i2cdetect -y 1` should show 52/53"),
        ("SGP40", seen("sgp_raw"), f"raw {last.get('sgp_raw')}, VOC index {last.get('sgp_voc_index')}",
         "check wiring; `sudo i2cdetect -y 1` should show 59"),
        ("DHT22", seen("temp_c"), f"{last.get('temp_c')} C, {last.get('rh')} %RH",
         "check its data wire and pull-up; DHT22s often fail a few reads, wait 1 min and retry"),
    ]
    for name, good, detail, hint in checks:
        print(f"{name:<19}{'OK   ' if good else 'MISSING'} {detail if good else ''}")
        if not good:
            print(f"   {hint}")
            ok = False
    if st.get("ens160_state") and st["ens160_state"] != "normal":
        print(f"   ENS160 state: {st['ens160_state']} - readings are not reliable until 'normal'")
    pin_file = os.path.join(cfg["device"]["data_dir"], "dht22_pin")
    if os.path.exists(pin_file):
        print(f"   (DHT22 detected on GPIO{open(pin_file).read().strip()})")
    b = store.get("battery")
    if b and b.get("battery_pct") is not None:
        print(f"battery ............ {b['battery_pct']:.0f}% via {b.get('source')}"
              f"{' (charging)' if b.get('charging') else ''}")
    else:
        print("battery ............ not detected (no UPS HAT found, or pisugar-server not installed)")
    mp = cfg["device"]["model_path"]
    if os.path.exists(mp):
        import json
        print(f"model .............. {json.load(open(mp)).get('version')}")
    else:
        print("model .............. none yet (collect with `voc-detector guided`, then `voc-detector train`)")
    print("\nALL GOOD" if ok else "\nSome checks failed - see hints above.")
    sys.exit(0 if ok else 1)


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


def cmd_guided(args, cfg):
    from . import guided
    confs = [c.strip() for c in (args.confounders or "").split(",") if c.strip()]
    guided.run(_db(cfg), rounds=args.rounds, compound_min=args.compound_min,
               clean_min=args.clean_min, recovery_min=args.recovery_min,
               baseline_min=args.baseline_min, confounders=confs,
               confounder_min=args.confounder_min, session=args.session, setup=args.setup)


def cmd_sessions(args, cfg):
    store = Storage(_db(cfg))
    per = {}
    for lb in store.labels():
        if not lb["end_ts"]:
            continue
        d = per.setdefault(lb["session"], {"start": lb["start_ts"], "mins": {}})
        d["mins"][lb["label"]] = d["mins"].get(lb["label"], 0) + (lb["end_ts"] - lb["start_ts"]) / 60
    if not per:
        print("No sessions yet.")
        return
    for name, d in sorted(per.items(), key=lambda kv: kv[1]["start"]):
        parts = ", ".join(f"{k.split(':')[-1]} {v:.0f}m" for k, v in sorted(d["mins"].items()))
        print(f"{name}: {parts}")
    print("\nB... = box setup, S... = open setup. Train on chosen ones with:\n"
          "  voc-detector train --only NAME1,NAME2,NAME3")


def cmd_placement(args, cfg):
    """List compound trials' placements, or correct one placement for all sessions."""
    store = Storage(_db(cfg))
    if args.change:
        if not args.to:
            sys.exit("use --to, e.g.  voc-detector placement --change \"10 cm\" --to \"close range\"")
        old, new = f"about {args.change}", ("at close range" if args.to == "close range" else f"about {args.to}")
        cur = store.db.execute(
            "UPDATE labels SET notes = REPLACE(notes, ?, ?) WHERE label = 'compound' "
            "AND notes LIKE ? AND session NOT LIKE 'DEMO-%'" + (" AND session = ?" if args.session else ""),
            (old, new, f"%{old}%") + ((args.session,) if args.session else ()))
        store.commit()
        print(f"changed {cur.rowcount} compound trial(s): '{old}' -> '{new}'")
    for lb in store.labels():
        if lb["label"] == "compound" and not str(lb["session"]).startswith("DEMO-"):
            print(f"{lb['session']:<18} {lb['notes']}")
    print("\nThen re-create the report:  voc-detector evidence")


def cmd_evidence(args, cfg):
    from . import evidence
    only = {x.strip() for x in (args.only or "").split(",") if x.strip()} or None
    out = os.path.expanduser(args.out)
    report, data, _ = evidence.build(_db(cfg), out, cfg["device"]["data_dir"], only, args.include_demo)
    print(f"report:   {report}\nraw data: {data}")
    print("\nTo view it on a laptop/phone on the same Wi-Fi, run:  voc-detector demo")
    print("then open the address it prints and add /report  (print to PDF from the browser).")


def cmd_demo(args, cfg):
    from . import demo, evidence
    out = os.path.expanduser(args.evidence_dir)
    try:
        evidence.build(_db(cfg), out, cfg["device"]["data_dir"])
    except SystemExit:
        pass  # no labelled data yet: live page still works
    demo.serve(_db(cfg), out, args.port)


def cmd_watch(args, cfg):
    from . import demo
    demo.watch(_db(cfg))


def cmd_review(args, cfg):
    from . import review
    review.run(_db(cfg), args.session)


def cmd_train(args, cfg):
    """Train on the Pi itself from everything collected with `guided`/`collect`."""
    try:
        import pandas  # noqa: F401
        import sklearn  # noqa: F401
    except ImportError:
        sys.exit("Training needs pandas and scikit-learn. Install them once with:\n"
                 "  sudo apt install -y python3-pandas python3-sklearn")
    store = Storage(_db(cfg))
    labels = [lb for lb in store.labels() if lb["end_ts"]]
    only = {x.strip() for x in (args.only or "").split(",") if x.strip()}
    skip = {x.strip() for x in (args.skip or "").split(",") if x.strip()}
    if only:
        labels = [lb for lb in labels if lb["session"] in only]
    labels = [lb for lb in labels if lb["session"] not in skip]
    if only or skip:
        print("training on sessions: " + ", ".join(sorted({lb["session"] for lb in labels})))
    sessions = {lb["session"] for lb in labels if lb["label"] == POSITIVE_LABEL}
    if not sessions:
        sys.exit("No labelled compound data yet. Run:  voc-detector guided")
    if len(sessions) < 3 and not args.force:
        sys.exit(f"Only {len(sessions)} session(s) with compound data. Collect at least 3 so the "
                 "model can be tested on a session it never saw (or use --force).")

    work = os.path.join(cfg["device"]["data_dir"], "training")
    os.makedirs(work, exist_ok=True)
    readings, labels_csv = os.path.join(work, "readings.csv"), os.path.join(work, "labels.csv")
    # Only export readings around the labelled periods (keeps it fast on a Pi Zero).
    windows = sorted((lb["start_ts"] - 900, lb["end_ts"] + 60) for lb in labels)
    merged = []
    for a, b in windows:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    cols = ["ts"] + ALL_CHANNELS
    n = 0
    with open(readings, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols + ["source"])
        for k, (a, b) in enumerate(merged):
            for smp in store.iter_samples(a, b):
                w.writerow([smp[c] for c in cols] + [f"block{k}"])
                n += 1
    with open(labels_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["start", "end", "label", "session", "notes"])
        for lb in labels:
            w.writerow([lb["start_ts"], lb["end_ts"], lb["label"], lb["session"], lb["notes"]])
    print(f"exported {n} readings and {len(labels)} labelled periods from "
          f"{len(sessions)} sessions to {work}")

    pipeline = os.path.join(args.training_dir, "pipeline.py")
    if not os.path.exists(pipeline):
        sys.exit(f"training scripts not found at {args.training_dir} (re-run pi/install.sh)")
    out = os.path.join(work, "model.json")
    cmd = [sys.executable, pipeline, readings, labels_csv, "-o", out, "--model", args.model,
           "--max-false-alarms", str(args.max_false_alarms), "--device-export"]
    print("training (a few minutes on a Pi Zero)...")
    if subprocess.call(cmd) != 0:
        sys.exit("training failed (see messages above)")
    if args.no_install:
        print(f"model saved to {out} (not installed)")
        return
    import json
    m = json.load(open(out)).get("metrics", {})
    if (m.get("roc_auc", 0) < 0.8 or m.get("recall", 0) < 0.5) and not args.install_anyway:
        print(f"\nNOT INSTALLED: the model did not pass its test on unseen sessions "
              f"(ROC-AUC {m.get('roc_auc', 0):.2f}, caught {m.get('detected_periods')}/"
              f"{m.get('compound_periods')} compound periods).")
        print("Run `voc-detector review` to see whether the sensors react to the compound.")
        print("(To install it anyway: voc-detector train --install-anyway)")
        return
    dest = cfg["device"]["model_path"]
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.exists(dest):
        shutil.copy(dest, dest + ".previous")
    shutil.copy(out, dest)
    print(f"installed {dest}; restarting the detector...")
    if subprocess.call(["sudo", "-n", "/usr/bin/systemctl", "restart", "voc-detector"]) != 0:
        print("could not restart automatically; run:  sudo systemctl restart voc-detector")


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

    g = sub.add_parser("guided", help="step-by-step labelled data collection session")
    g.add_argument("--rounds", type=int, default=4, help="compound exposures (default 4)")
    g.add_argument("--compound-min", type=float, default=10)
    g.add_argument("--clean-min", type=float, default=10)
    g.add_argument("--recovery-min", type=float, default=3)
    g.add_argument("--baseline-min", type=float, default=10)
    g.add_argument("--confounders", default="",
                   help="comma-separated other smells to record, e.g. 'perfume,hand sanitiser'")
    g.add_argument("--confounder-min", type=float, default=5)
    g.add_argument("-s", "--session", help="session name (default: date and time)")
    g.add_argument("--setup", choices=["open", "box"], default="open",
                   help="open = sample placed near the device in the room; "
                        "box = device and sample together in a closed box")

    t = sub.add_parser("train", help="train a model on this Pi from the collected sessions")
    t.add_argument("--model", choices=["logreg", "gbt", "auto"], default="logreg",
                   help="logreg is fast on a Pi Zero; auto also tries boosted trees (slow)")
    t.add_argument("--max-false-alarms", type=float, default=0.25)
    t.add_argument("--training-dir", default="/opt/voc-detector/training")
    t.add_argument("--no-install", action="store_true", help="train but don't install the model")
    t.add_argument("--force", action="store_true", help="train even with fewer than 3 sessions")
    t.add_argument("--only", help="comma-separated session names to train on (see: voc-detector sessions)")
    t.add_argument("--skip", help="comma-separated session names to leave out")
    t.add_argument("--install-anyway", action="store_true",
                   help="install the model even if it failed its validation test")

    sub.add_parser("sessions", help="list recorded sessions")

    pl = sub.add_parser("placement", help="list or correct the recorded distance of compound trials")
    pl.add_argument("--change", help='distance as recorded, e.g. "10 cm"')
    pl.add_argument("--to", help='what it really was, e.g. "close range" or "3 cm"')
    pl.add_argument("-s", "--session", help="only this session (default: all)")

    ev = sub.add_parser("evidence", help="results report (HTML) + raw data (CSV) from recorded sessions")
    ev.add_argument("--out", default="~/evidence")
    ev.add_argument("--only", help="comma-separated session names to include (default: all)")
    ev.add_argument("--include-demo", action="store_true",
                    help="also include live-demo (DEMO-...) sessions")

    dm = sub.add_parser("demo", help="live readings web page for a demonstration (+ the report)")
    dm.add_argument("--port", type=int, default=8000)
    dm.add_argument("--evidence-dir", default="~/evidence")

    sub.add_parser("watch", help="live readings in the terminal")

    rv = sub.add_parser("review", help="how much each sensor reacted in each recorded period")
    rv.add_argument("-s", "--session", help="only this session")

    sub.add_parser("check", help="health check: service, sensors, battery, model")
    sub.add_parser("status", help="show the latest reading and queue size")

    e = sub.add_parser("export", help="export logged samples (+labels) to CSV")
    e.add_argument("out")
    e.add_argument("--hours", type=float, default=None, help="only the last N hours")

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = config_mod.load(args.config)
    {"run": cmd_run, "guided": cmd_guided, "check": cmd_check, "collect": cmd_collect, "status": cmd_status,
     "export": cmd_export, "train": cmd_train, "review": cmd_review,
     "sessions": cmd_sessions, "evidence": cmd_evidence, "placement": cmd_placement, "demo": cmd_demo, "watch": cmd_watch}[args.cmd](args, cfg)


if __name__ == "__main__":
    main()
