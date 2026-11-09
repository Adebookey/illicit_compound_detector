"""`voc-detector review`: how much did each sensor react in each recorded period?

For every labelled compound / other-smell period it compares the readings in
the last 5 minutes of the period with the 3 minutes just before it started,
and prints the change. A clean-air "wobble" line shows how much the readings
drift on their own, for comparison. Narrow output so it fits a phone screen.

If the compound's changes are no bigger than the clean-air wobble, the
sensors are not picking it up and no model can learn it: bring the sample
closer / use more of it / test in a smaller enclosed space.
"""

import re
import statistics as st

from .storage import Storage

COLS = [("ens_tvoc", "TVOC", "abs"), ("sgp_voc_index", "VOCi", "abs"),
        ("sgp_raw", "SGPraw", "pct"), ("ens_hp2_rs", "ENS-Rs", "pct")]


def _mean(rows, col):
    v = [r[col] for r in rows if r.get(col) is not None]
    return st.fmean(v) if v else None


def _change(before, during, kind):
    if before is None or during is None:
        return None
    if kind == "pct":
        return 100.0 * (during - before) / before if before else None
    return during - before


def _fmt(v, kind):
    if v is None:
        return "    -"
    return f"{v:+5.1f}%" if kind == "pct" else f"{v:+6.0f}"


def _short(label, notes):
    if label == "compound":
        m = re.search(r"(\d+\s*c?m)", notes or "")
        where = m.group(1).replace(" ", "") if m else ""
        closed = " bag" if "CLOSED" in (notes or "") else ""
        return f"cmp {where}{closed}"[:13]
    return label.split(":", 1)[-1][:13]


def run(db_path, session=None):
    store = Storage(db_path)
    labels = [lb for lb in store.labels() if lb["end_ts"]]
    if session:
        labels = [lb for lb in labels if lb["session"] == session]
    if not labels:
        print("No labelled periods yet.")
        return

    # clean-air wobble: spread of 5-minute means within clean periods
    wobble = {c: [] for c, _, _ in COLS}
    for lb in labels:
        if lb["label"] != "clean":
            continue
        rows = list(store.iter_samples(lb["start_ts"], lb["end_ts"]))
        for i in range(0, max(0, len(rows) - 300), 150):
            a, b = rows[i:i + 180], rows[i + 120:i + 420]
            for c, _, kind in COLS:
                d = _change(_mean(a, c), _mean(b, c), kind)
                if d is not None:
                    wobble[c].append(abs(d))

    print(f"{'period':<13} " + " ".join(f"{n:>6}" for _, n, _ in COLS))
    current = None
    for lb in labels:
        if lb["label"] == "clean":
            continue
        if lb["session"] != current:
            current = lb["session"]
            print(f"-- {current}")
        before = list(store.iter_samples(lb["start_ts"] - 180, lb["start_ts"]))
        during = list(store.iter_samples(max(lb["start_ts"], lb["end_ts"] - 300), lb["end_ts"]))
        cells = [_fmt(_change(_mean(before, c), _mean(during, c), kind), kind) for c, _, kind in COLS]
        print(f"{_short(lb['label'], lb['notes']):<13} " + " ".join(f"{x:>6}" for x in cells))

    def typical(v):
        return st.median(v) if v else None
    print("-" * 42)
    print(f"{'clean wobble':<13} " + " ".join(
        f"{('~' + _fmt(typical(wobble[c]), k).strip()) if wobble[c] else '-':>6}" for c, _, k in COLS))
    print("\nCompound rows should change clearly MORE than the clean wobble")
    print("(TVOC/VOCi up, SGPraw/ENS-Rs down). If they don't, the sensors")
    print("aren't detecting the sample at that distance/amount.")
