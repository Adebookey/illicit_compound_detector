"""Attach labels (from your written-down times) to the imported readings.

Labels file (CSV) - one row per period, times as you wrote them down:

    start,end,label,session,notes
    2026-10-28 14:05,2026-10-28 14:20,clean,day1,device on desk, window closed
    2026-10-28 14:20,2026-10-28 14:35,compound,day1,sample 10 cm from device
    2026-10-28 14:35,2026-10-28 15:00,clean,day1,sample removed
    2026-10-29 10:00,2026-10-29 10:15,confounder:coffee,day2,fresh coffee next to device

  label   : compound | clean | confounder:<what>   (perfume, cooking, alcohol, ...)
  session : groups periods recorded together (default: the date). Validation
            always tests on sessions the model has never seen.

Every reading keeps its row (the feature extractor needs continuous
history); readings outside any period, or inside the settling margin at the
start of a period, get an empty label and are ignored for training.

Pi clock warning: a Pi Zero has no battery-backed clock. If it had no
internet when you collected data, its timestamps can be wrong. Compare a
known moment (e.g. when you powered it on) with the first log time and pass
the difference as --clock-offset (seconds the Pi clock was AHEAD; use a
negative number if it was behind). Then check with training/plot_session.py.
"""

import argparse

import pandas as pd


def parse_times(col, tz):
    if pd.api.types.is_numeric_dtype(col):
        return col.astype(float)
    dt = pd.to_datetime(col, errors="raise")
    if dt.dt.tz is None:
        dt = dt.dt.tz_localize(tz)
    return (dt.dt.tz_convert("UTC") - pd.Timestamp(0, tz="UTC")).dt.total_seconds()


def load_labels(path, tz="UTC", clock_offset=0.0):
    lab = pd.read_csv(path, skipinitialspace=True, comment="#")
    lab.columns = [c.strip().lower() for c in lab.columns]
    for c in ("start", "end", "label"):
        if c not in lab.columns:
            raise SystemExit(f"labels file needs a '{c}' column")
    lab["start_ts"] = parse_times(lab["start"], tz) + clock_offset
    lab["end_ts"] = parse_times(lab["end"], tz) + clock_offset
    lab["label"] = lab["label"].astype(str).str.strip().str.lower()
    if "session" not in lab.columns:
        lab["session"] = None
    lab["session"] = [s if isinstance(s, str) and s.strip() else
                      pd.to_datetime(t, unit="s").strftime("%Y-%m-%d")
                      for s, t in zip(lab["session"], lab["start_ts"])]
    bad = lab[lab.end_ts <= lab.start_ts]
    if len(bad):
        raise SystemExit(f"periods with end before start:\n{bad}")
    return lab.sort_values("start_ts").reset_index(drop=True)


def apply_labels(readings, labels, margin_s=60.0):
    df = readings.copy()
    df["label"] = None
    df["session"] = None
    df["interval_id"] = -1
    for i, r in labels.iterrows():
        m = (df.ts >= r.start_ts + margin_s) & (df.ts < r.end_ts)
        df.loc[m, "label"] = r.label
        df.loc[m, "session"] = r.session
        df.loc[m, "interval_id"] = i
    return df


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("readings", help="CSV from import_data.py")
    p.add_argument("labels", help="your periods CSV (see above)")
    p.add_argument("-o", "--out", default="data/processed/labelled.csv")
    p.add_argument("--tz", default="UTC", help="timezone you wrote the times in, e.g. Africa/Lagos")
    p.add_argument("--clock-offset", type=float, default=0.0,
                   help="seconds the Pi clock was ahead of real time")
    p.add_argument("--margin", type=float, default=60.0,
                   help="ignore the first N seconds of each period while sensors settle")
    args = p.parse_args(argv)

    readings = pd.read_csv(args.readings)
    labels = load_labels(args.labels, args.tz, args.clock_offset)
    out = apply_labels(readings, labels, args.margin)

    print("labelled seconds per label:")
    counts = out.dropna(subset=["label"]).groupby("label").size()
    print(counts.to_string())
    for i, r in labels.iterrows():
        n = (out.interval_id == i).sum()
        flag = "" if n else "   <-- NO READINGS in this period (wrong timezone / clock offset?)"
        print(f"  [{r.session}] {r.label:<22} {pd.to_datetime(r.start_ts, unit='s')} "
              f"+{(r.end_ts - r.start_ts) / 60:.0f} min: {n} readings{flag}")
    out.to_csv(args.out, index=False)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
