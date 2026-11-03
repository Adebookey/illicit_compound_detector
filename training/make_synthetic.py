"""Generate SYNTHETIC readings + labels to test the pipeline end to end.

This is NOT real sensor data and a model trained on it is useless in the
field. It exists so the import -> label -> train -> deploy chain can be
exercised (and unit-tested) before/without real data.

    python training/make_synthetic.py --out-dir data/synthetic
"""

import argparse
import csv
import datetime as dt
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from voc_detector.schema import ALL_CHANNELS  # noqa: E402
from voc_detector.sensors import DEFAULT_PROFILE, SimulatedSensorHub  # noqa: E402

# Target compound: strong response on hot-plate 2 and SGP40 relative to TVOC.
COMPOUND = {"tvoc": 0.7, "eco2": 0.6, "hp0": 0.9, "hp2": 1.6, "sgp": 1.3, "idx": 1.0}
CONFOUNDERS = {
    "confounder:alcohol": {"tvoc": 1.6, "eco2": 1.2, "hp0": 1.5, "hp2": 0.3, "sgp": 0.8, "idx": 1.0},
    "confounder:cooking": {"tvoc": 1.0, "eco2": 1.5, "hp0": 0.8, "hp2": 0.5, "sgp": 0.6, "idx": 0.8},
}


def session_plan(rng):
    plan = [("clean", 15)]
    events = ["compound", "compound"] + rng.sample(list(CONFOUNDERS), 1)
    rng.shuffle(events)
    for e in events:
        plan += [(e, rng.randint(8, 15)), ("clean", rng.randint(15, 25))]
    return plan


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--out-dir", default="data/synthetic")
    p.add_argument("--sessions", type=int, default=4)
    p.add_argument("--seed", type=int, default=1)
    args = p.parse_args(argv)
    os.makedirs(args.out_dir, exist_ok=True)
    rng = random.Random(args.seed)
    t = dt.datetime(2026, 10, 1, 9, 0, tzinfo=dt.timezone.utc).timestamp()
    readings_path = os.path.join(args.out_dir, "readings.csv")
    labels_path = os.path.join(args.out_dir, "labels.csv")
    with open(readings_path, "w", newline="") as rf, open(labels_path, "w", newline="") as lf:
        rw, lw = csv.writer(rf), csv.writer(lf)
        rw.writerow(["ts"] + ALL_CHANNELS + ["source"])
        lw.writerow(["start", "end", "label", "session", "notes"])
        for s in range(args.sessions):
            hub = SimulatedSensorHub(seed=rng.randint(0, 10**6))
            hub.t0 = t
            name = f"day{s + 1}"
            for label, minutes in session_plan(rng):
                hub.profile = COMPOUND if label == "compound" else CONFOUNDERS.get(label, DEFAULT_PROFILE)
                hub.gas = rng.uniform(0.4, 0.9) if label != "clean" else 0.0
                start = t
                for _ in range(int(minutes * 60)):
                    r = hub.read(now=t)
                    rw.writerow([round(t, 2)] + [r[c] for c in ALL_CHANNELS] + [name])
                    t += 1.0
                fmt = lambda x: dt.datetime.fromtimestamp(x, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")  # noqa: E731
                lw.writerow([fmt(start), fmt(t), label, name, "synthetic"])
            t += 20 * 3600  # next day
    print(f"wrote {readings_path} and {labels_path} (SYNTHETIC)")


if __name__ == "__main__":
    main()
