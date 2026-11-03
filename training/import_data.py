"""Convert the engineer's raw log files into one standard CSV.

We don't yet know exactly how the existing code logs data, so this tool is
deliberately forgiving: it accepts CSV / TSV / JSON-lines / SQLite files,
guesses which column is which from common names, and tells you what it
could and could not map.

Examples
  # see what's in the files without writing anything
  python training/import_data.py data/raw/*.csv --inspect

  # convert; times in the files are local Lagos time (no timezone written)
  python training/import_data.py data/raw/*.csv --tz Africa/Lagos -o data/processed/readings.csv

  # a column name the tool doesn't recognise
  python training/import_data.py data/raw/log.csv --map gas_res=ens_hp0_rs --tz UTC
"""

import argparse
import os
import re
import sqlite3
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from voc_detector.schema import ALL_CHANNELS  # noqa: E402

ALIASES = {
    "ts": ["ts", "timestamp", "time", "datetime", "date_time", "epoch", "unix_time", "unixtime",
           "time_stamp", "logged_at", "created_at"],
    "ens_tvoc": ["tvoc", "ens_tvoc", "ens160_tvoc", "tvoc_ppb", "ens160tvoc"],
    "ens_eco2": ["eco2", "co2", "ens_eco2", "ens160_eco2", "eco2_ppm", "co2_ppm"],
    "ens_aqi": ["aqi", "ens_aqi", "ens160_aqi", "aqi_uba"],
    "ens_hp0_rs": ["hp0_rs", "ens_hp0_rs", "resistance0", "rs0", "res0"],
    "ens_hp2_rs": ["hp2_rs", "ens_hp2_rs", "resistance2", "rs2", "res2"],
    "sgp_raw": ["sgp_raw", "sraw", "raw", "voc_raw", "sgp40_raw", "raw_gas", "sgp40raw", "gas_raw"],
    "sgp_voc_index": ["voc_index", "vocindex", "sgp_voc_index", "voc", "sgp40_voc", "voc_idx",
                      "sgp40_index"],
    "temp_c": ["temp", "temperature", "temp_c", "temperature_c", "dht_temp", "dht22_temp", "t"],
    "rh": ["humidity", "rh", "hum", "relative_humidity", "dht_humidity", "dht22_humidity", "h"],
}


def norm(name):
    return re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower()).strip("_")


def read_any(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".db", ".sqlite", ".sqlite3"):
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        best = None
        for t in tables:
            df = pd.read_sql_query(f'SELECT * FROM "{t}"', con)
            score = len(map_columns(df.columns, {})[0]) * 1_000_000 + len(df)
            if best is None or score > best[0]:
                best = (score, t, df)
        if best is None:
            raise ValueError(f"{path}: no tables")
        print(f"  {path}: using table '{best[1]}' (tables: {tables})")
        return best[2]
    if ext in (".jsonl", ".ndjson"):
        return pd.read_json(path, lines=True)
    if ext == ".json":
        try:
            return pd.read_json(path)
        except ValueError:
            return pd.read_json(path, lines=True)
    # CSV/TSV/TXT/LOG: let pandas sniff the delimiter
    return pd.read_csv(path, sep=None, engine="python")


def map_columns(columns, manual):
    mapping, used = {}, set()
    normed = {norm(c): c for c in columns}
    for src, dst in manual.items():
        if src in columns:
            mapping[dst] = src
            used.add(src)
    for dst, names in ALIASES.items():
        if dst in mapping:
            continue
        for n in names:
            if n in normed and normed[n] not in used:
                mapping[dst] = normed[n]
                used.add(normed[n])
                break
    unmapped = [c for c in columns if c not in used]
    return mapping, unmapped


def parse_time(df, mapping, tz):
    if "ts" not in mapping:
        # Separate date + time columns?
        n = {norm(c): c for c in df.columns}
        if "date" in n and ("time" in n or "clock" in n):
            s = df[n["date"]].astype(str) + " " + df[n.get("time", n.get("clock"))].astype(str)
            return _to_epoch(pd.to_datetime(s, errors="coerce"), tz)
        raise ValueError("could not find a timestamp column; use --map <column>=ts")
    col = df[mapping["ts"]]
    if pd.api.types.is_numeric_dtype(col):
        v = col.astype(float)
        if v.median() > 1e11:  # milliseconds
            v = v / 1000.0
        return v
    return _to_epoch(pd.to_datetime(col, errors="coerce"), tz)


def _to_epoch(dt, tz):
    if dt.dt.tz is None:
        dt = dt.dt.tz_localize(tz, ambiguous="NaT", nonexistent="NaT")
    return (dt.dt.tz_convert("UTC") - pd.Timestamp(0, tz="UTC")).dt.total_seconds()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("files", nargs="+")
    p.add_argument("-o", "--out", default="data/processed/readings.csv")
    p.add_argument("--tz", default="UTC",
                   help="timezone of timestamps that have none written, e.g. Africa/Lagos")
    p.add_argument("--map", action="append", default=[], metavar="COLUMN=CANONICAL",
                   help=f"force a mapping; canonical names: ts, {', '.join(ALL_CHANNELS)}")
    p.add_argument("--inspect", action="store_true", help="only show what would be mapped")
    args = p.parse_args(argv)

    manual = dict(m.split("=", 1) for m in args.map)
    frames = []
    for path in args.files:
        df = read_any(path)
        mapping, unmapped = map_columns(df.columns, manual)
        print(f"\n{path}: {len(df)} rows")
        for k, v in mapping.items():
            print(f"  {v!r:>28} -> {k}")
        if unmapped:
            print(f"  not used: {unmapped}")
        if args.inspect:
            print(df.head(3).to_string())
            continue
        out = pd.DataFrame({"ts": parse_time(df, mapping, args.tz)})
        for ch in ALL_CHANNELS:
            out[ch] = pd.to_numeric(df[mapping[ch]], errors="coerce") if ch in mapping else None
        # keep an existing source/session column (one continuous recording each)
        src_col = next((c for c in df.columns if norm(c) in ("source", "run", "recording")), None)
        out["source"] = (os.path.basename(path) + ":" + df[src_col].astype(str)) if src_col \
            else os.path.basename(path)
        bad = out["ts"].isna().sum()
        if bad:
            print(f"  WARNING: {bad} rows with unreadable timestamps dropped")
        out = out.dropna(subset=["ts"])
        if len(out):
            print(f"  time range (UTC): {pd.to_datetime(out.ts.min(), unit='s')} .. "
                  f"{pd.to_datetime(out.ts.max(), unit='s')}")
            med = out.ts.sort_values().diff().median()
            print(f"  median sample interval: {med:.2f} s")
        frames.append(out)

    if args.inspect or not frames:
        return
    allf = pd.concat(frames).sort_values(["source", "ts"]).drop_duplicates(["source", "ts"])
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    allf.to_csv(args.out, index=False)
    print(f"\nwrote {len(allf)} rows to {args.out}")


if __name__ == "__main__":
    main()
