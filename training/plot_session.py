"""Plot readings with your labelled periods shaded - the quickest way to check
that the times you wrote down line up with what the sensors saw.

    python training/plot_session.py data/processed/labelled.csv --session day1 -o day1.png
    python training/plot_session.py data/processed/labelled.csv --model models/model.json

If the sensor response is clearly shifted from the shaded 'compound'
periods, your clock offset / timezone is off (see label.py --clock-offset).
"""

import argparse
import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from voc_detector.features import FeatureExtractor  # noqa: E402
from voc_detector.model import Model  # noqa: E402

COLORS = {"compound": "#d62728", "clean": "#2ca02c"}
PANELS = [("ens_tvoc", "TVOC (ppb)"), ("ens_eco2", "eCO2 (ppm)"), ("sgp_voc_index", "VOC index"),
          ("sgp_raw", "SGP40 raw"), ("ens_hp2_rs", "ENS160 Rs hp2"), ("rh", "RH (%)")]


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("labelled")
    p.add_argument("--session", help="only this session")
    p.add_argument("--model", help="also plot this model's score")
    p.add_argument("-o", "--out", default="session.png")
    a = p.parse_args(argv)

    df = pd.read_csv(a.labelled).sort_values("ts")
    if a.session:
        sess = df.session.astype(str)
        keep = sess == a.session
        if not keep.any():
            sys.exit(f"no rows with session {a.session}; sessions: {sorted(sess.dropna().unique())}")
        lo, hi = df.ts[keep].min() - 600, df.ts[keep].max() + 600
        df = df[(df.ts >= lo) & (df.ts <= hi)]
    t = pd.to_datetime(df.ts, unit="s")
    panels = [(c, n) for c, n in PANELS if c in df and df[c].notna().any()]
    if a.model:
        m, fx, sc = Model.load(a.model), FeatureExtractor(), []
        for rec in df.to_dict("records"):
            f = fx.update(rec)
            sc.append(None if f is None else m.predict_proba(f))
        df = df.assign(model_score=sc)
        panels.append(("model_score", "model probability"))

    fig, axes = plt.subplots(len(panels), 1, figsize=(14, 2.2 * len(panels)), sharex=True)
    axes = [axes] if len(panels) == 1 else axes
    for ax, (col, name) in zip(axes, panels):
        ax.plot(t, df[col], lw=0.8, color="#333")
        ax.set_ylabel(name, fontsize=8)
        if "label" in df:
            for iid, g in df[df.label.notna()].groupby("interval_id"):
                lab = g.label.iloc[0]
                ax.axvspan(pd.to_datetime(g.ts.min(), unit="s"), pd.to_datetime(g.ts.max(), unit="s"),
                           color=COLORS.get(lab, "#ff7f0e"), alpha=0.15, lw=0)
    axes[0].set_title("shaded: red = compound, green = clean, orange = confounder")
    fig.tight_layout()
    fig.savefig(a.out, dpi=110)
    print(f"saved {a.out}")


if __name__ == "__main__":
    main()
