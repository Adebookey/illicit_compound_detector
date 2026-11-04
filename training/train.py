"""Train the detector and export it for the Pi.

    python training/train.py data/processed/labelled.csv -o models/model.json

What it does
  1. Replays every reading through the SAME FeatureExtractor the Pi uses.
  2. Trains two small model types (logistic regression, boosted trees).
  3. Validates honestly: each session (day) is held out in turn and the
     model is tested on data it never saw. Random row splits would look
     great and mean nothing, because neighbouring seconds are near-identical.
  4. Simulates the on-device alarm logic on those held-out predictions and
     picks alarm thresholds that keep false alarms under --max-false-alarms
     per hour while catching as many compound periods as possible.
  5. Retrains on everything and writes models/model.json + models/report.md.
"""

import argparse
import datetime as dt
import json
import os
import sys
import warnings

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.model_selection import GroupKFold, LeaveOneGroupOut
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from voc_detector.detector import Detector  # noqa: E402
from voc_detector.features import FeatureExtractor  # noqa: E402
from voc_detector.model import Model  # noqa: E402
from voc_detector.schema import POSITIVE_LABEL  # noqa: E402

GAP_S = 120.0


# ---------------------------------------------------------------- features
def build_dataset(df):
    if "source" not in df.columns:
        df = df.assign(source="all")
    for c in ("label", "session"):
        if c not in df.columns:
            df[c] = None
    if "interval_id" not in df.columns:
        df["interval_id"] = -1
    rows = []
    for source, g in df.sort_values("ts").groupby("source", sort=False):
        fx = FeatureExtractor()
        for rec in g.to_dict("records"):
            f = fx.update(rec)
            if f is None:
                continue
            lab = rec.get("label")
            f.update(ts=rec["ts"], source=source,
                     label=lab if isinstance(lab, str) and lab else None,
                     session=rec.get("session"), interval_id=int(rec.get("interval_id", -1)))
            rows.append(f)
    out = pd.DataFrame(rows)
    return out[out.label.notna()].reset_index(drop=True)


def usable_features(data):
    names = []
    for n in FeatureExtractor.feature_names():
        col = pd.to_numeric(data[n], errors="coerce")
        if col.notna().mean() > 0.5 and col.std(skipna=True) > 1e-9:
            names.append(n)
    return names


# ---------------------------------------------------------------- models
def make_model(kind):
    if kind == "logreg":
        return "logreg", (StandardScaler(), LogisticRegression(C=0.5, class_weight="balanced",
                                                               max_iter=2000))
    return "gbt", GradientBoostingClassifier(n_estimators=150, max_depth=3, learning_rate=0.1,
                                             subsample=0.8, min_samples_leaf=40, random_state=0)


def fit(kind, X, y):
    _, m = make_model(kind)
    if kind == "logreg":
        sc, lr = m
        Xs = sc.fit_transform(X)
        lr.fit(Xs, y)
        return (sc, lr)
    w = np.where(y == 1, (y == 0).sum() / max(1, (y == 1).sum()), 1.0)
    m.fit(X, y, sample_weight=w)
    return m


def predict(kind, m, X):
    if kind == "logreg":
        sc, lr = m
        return lr.predict_proba(sc.transform(X))[:, 1]
    return m.predict_proba(X)[:, 1]


def export(kind, m, features, fill):
    spec = {"type": kind, "features": features, "fill": [float(v) for v in fill]}
    if kind == "logreg":
        sc, lr = m
        spec.update(mean=sc.mean_.tolist(), scale=[float(s) if s > 0 else 1.0 for s in sc.scale_],
                    coef=lr.coef_[0].tolist(), intercept=float(lr.intercept_[0]))
    else:
        trees = []
        for est in m.estimators_[:, 0]:
            t = est.tree_
            trees.append({"feature": t.feature.tolist(), "threshold": t.threshold.tolist(),
                          "left": t.children_left.tolist(), "right": t.children_right.tolist(),
                          "value": t.value[:, 0, 0].tolist()})
        x0 = np.asarray([fill])
        raw = float(m.decision_function(x0)[0])
        tree_sum = sum(float(e.predict(x0)[0]) for e in m.estimators_[:, 0])
        spec.update(learning_rate=float(m.learning_rate), trees=trees,
                    init=raw - m.learning_rate * tree_sum)
    return spec


# ---------------------------------------------------------------- evaluation
def cv_groups(data):
    sessions = data.session.astype(str)
    if sessions.nunique() >= 3:
        return sessions.values, "session"
    warnings.warn("fewer than 3 sessions: validating per labelled period instead. Results will be "
                  "OPTIMISTIC. Collect data on more days.")
    return data.interval_id.astype(str).values, "period"


def oof_predictions(kind, X, y, groups):
    n_groups = len(set(groups))
    splitter = LeaveOneGroupOut() if n_groups <= 12 else GroupKFold(n_splits=8)
    oof = np.full(len(y), np.nan)
    for tr, te in splitter.split(X, y, groups):
        if len(set(y[tr])) < 2:
            continue
        m = fit(kind, X[tr], y[tr])
        oof[te] = predict(kind, m, X[te])
    return oof


def simulate_alarm(data, probs, det_cfg):
    """Run the on-device Detector over held-out predictions. Returns per-row alarm state."""
    active = np.zeros(len(data), dtype=bool)
    order = np.lexsort((data.ts.values, data.source.values))
    det, last_src, last_t = None, None, None
    for i in order:
        s, t, p = data.source.values[i], data.ts.values[i], probs[i]
        if det is None or s != last_src or t - last_t > GAP_S:
            det = Detector.from_config(det_cfg)
        last_src, last_t = s, t
        if not np.isnan(p):
            det.update(t, float(p))
        active[i] = det.active
    return active, order


def event_metrics(data, active, order):
    pos = (data.label.values == POSITIVE_LABEL)
    res = {"compound_periods": 0, "detected_periods": 0, "time_to_detect_s": [],
           "false_alarms": 0, "negative_hours": 0.0}
    for iid, g in data[pos].groupby("interval_id"):
        res["compound_periods"] += 1
        idx = g.index.values
        hit = active[idx]
        if hit.any():
            res["detected_periods"] += 1
            res["time_to_detect_s"].append(float(g.ts.values[hit.argmax()] - g.ts.values.min()))
    prev, last_src, last_t = False, None, None
    dts = []
    for i in order:
        s, t = data.source.values[i], data.ts.values[i]
        if s != last_src or (last_t is not None and t - last_t > GAP_S):
            prev = False
        elif not pos[i]:
            dts.append(min(t - last_t, 10.0))
        if active[i] and not prev and not pos[i]:
            res["false_alarms"] += 1
        prev, last_src, last_t = active[i], s, t
    res["negative_hours"] = float(sum(dts)) / 3600.0
    res["recall"] = res["detected_periods"] / max(1, res["compound_periods"])
    res["false_alarms_per_hour"] = res["false_alarms"] / max(1e-9, res["negative_hours"])
    res["median_time_to_detect_s"] = (float(np.median(res["time_to_detect_s"]))
                                      if res["time_to_detect_s"] else None)
    del res["time_to_detect_s"]
    return res


def tune_detector(data, probs, max_fa):
    results = []
    for on in np.arange(0.3, 0.951, 0.05):
        cfg = {"on_threshold": round(float(on), 2), "off_threshold": round(max(0.1, on - 0.2), 2),
               "smoothing_s": 10.0, "min_on_s": 5.0, "min_off_s": 30.0}
        active, order = simulate_alarm(data, probs, cfg)
        results.append((cfg, event_metrics(data, active, order), active))
    ok = [r for r in results if r[1]["false_alarms_per_hour"] <= max_fa]
    if ok:
        # most periods caught, then fewest false alarms, then (within ~10 s of
        # detection speed) the higher, more conservative threshold
        best = max(ok, key=lambda r: (r[1]["recall"], -r[1]["false_alarms_per_hour"],
                                      -round((r[1]["median_time_to_detect_s"] or 1e9) / 10),
                                      r[0]["on_threshold"]))
        met_target = True
    else:
        best = min(results, key=lambda r: (r[1]["false_alarms_per_hour"], -r[1]["recall"]))
        met_target = False
    return best, met_target, results


# ---------------------------------------------------------------- main
def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("labelled", nargs="+", help="CSV(s) from label.py")
    p.add_argument("-o", "--out", default="models/model.json")
    p.add_argument("--model", choices=["auto", "logreg", "gbt"], default="auto")
    p.add_argument("--max-false-alarms", type=float, default=0.25,
                   help="target false alarms per hour of non-compound air (default 0.25 = 1 per 4 h)")
    args = p.parse_args(argv)

    frames = []
    for i, f in enumerate(args.labelled):
        part = pd.read_csv(f)
        if "interval_id" in part.columns:  # keep period ids unique across files
            part["interval_id"] = np.where(part.interval_id >= 0, part.interval_id + i * 1_000_000, -1)
        if "source" in part.columns and len(args.labelled) > 1:
            part["source"] = f"{i}:" + part.source.astype(str)
        frames.append(part)
    df = pd.concat(frames, ignore_index=True)
    print(f"replaying {len(df)} readings through the feature extractor...")
    data = build_dataset(df)
    if data.empty:
        sys.exit("no labelled, warmed-up readings. Check label.py output.")
    y = (data.label == POSITIVE_LABEL).astype(int).values
    if y.sum() == 0 or y.sum() == len(y):
        sys.exit("need both compound and non-compound periods to train.")
    features = usable_features(data)
    X = data[features].apply(pd.to_numeric, errors="coerce")
    fill = X.mean().values
    X = X.fillna(X.mean()).values
    groups, gkind = cv_groups(data)
    print(f"{len(data)} labelled samples, {y.sum()} compound, {len(features)} features, "
          f"{len(set(groups))} validation groups ({gkind})")
    print(data.groupby(["session", "label"]).size().rename("samples").to_string())

    kinds = ["logreg", "gbt"] if args.model == "auto" else [args.model]
    scores = {}
    for k in kinds:
        oof = oof_predictions(k, X, y, groups)
        m = ~np.isnan(oof)
        ap = average_precision_score(y[m], oof[m]) if len(set(y[m])) == 2 else float("nan")
        auc = roc_auc_score(y[m], oof[m]) if len(set(y[m])) == 2 else float("nan")
        scores[k] = (ap, auc, oof)
        print(f"  {k:6s}: held-out ROC-AUC {auc:.3f}  avg-precision {ap:.3f}")
    kind = max(scores, key=lambda k: np.nan_to_num(scores[k][0], nan=-1))
    ap, auc, oof = scores[kind]
    (det_cfg, ev, active), met_target, grid = tune_detector(data, oof, args.max_false_alarms)

    per_label = (pd.DataFrame({"label": data.label, "alarm": active})
                 .groupby("label").alarm.mean().mul(100).round(1).rename("% of time alarm on"))

    final = fit(kind, X, y)
    spec = export(kind, final, features, fill)
    version = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    spec.update(version=version, detector=det_cfg, trained_on={
        "samples": int(len(data)), "compound_samples": int(y.sum()),
        "sessions": sorted(set(map(str, data.session))), "labels": per_label.index.tolist()},
        metrics={"model": kind, "validation": gkind, "roc_auc": round(float(auc), 4),
                 "avg_precision": round(float(ap), 4), **ev})

    # Sanity check: pure-Python inference must match scikit-learn.
    pm = Model(spec)
    feats_df = data[features]
    for i in np.linspace(0, len(data) - 1, min(50, len(data))).astype(int):
        a = pm.predict_proba({k: (None if pd.isna(v) else v) for k, v in feats_df.iloc[i].items()})
        b = predict(kind, final, X[i:i + 1])[0]
        assert abs(a - b) < 1e-6, f"export mismatch {a} vs {b}"

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(spec, f)
    report = render_report(spec, scores, per_label, met_target, args.max_false_alarms, grid, data)
    rp = os.path.join(os.path.dirname(os.path.abspath(args.out)), "report.md")
    with open(rp, "w") as f:
        f.write(report)
    print("\n" + report)
    print(f"model written to {args.out} ({os.path.getsize(args.out) / 1024:.0f} KB), report {rp}")


def assess(spec, data, met_target):
    """Plain-language verdict for non-specialists."""
    m = spec["metrics"]
    n_sessions = data.session.astype(str).nunique()
    confounders = sorted(l for l in set(data.label) if str(l).startswith("confounder"))
    notes = []
    if n_sessions < 3:
        head = "NOT ENOUGH DATA TO JUDGE - collect more sessions"
        notes.append(f"Only {n_sessions} session(s). The scores below are optimistic.")
    elif m["roc_auc"] >= 0.95 and m["recall"] >= 0.9 and met_target:
        head = "GOOD ENOUGH FOR A PILOT / DEMO"
    elif m["roc_auc"] >= 0.85 and m["recall"] >= 0.7:
        head = "PROMISING - needs more data before giving it to the client"
    else:
        head = "NOT RELIABLE YET - collect more (and more varied) data"
    if not confounders:
        notes.append("No other smells (perfume, sanitiser, cooking...) were recorded, so the "
                     "model has only learned 'compound vs. clean air'. Expect false alarms from "
                     "other strong smells until confounders are collected "
                     "(voc-detector guided --confounders \"perfume,hand sanitiser\").")
    if m["compound_periods"] < 10:
        notes.append(f"Only {m['compound_periods']} compound exposures were tested; "
                     "aim for 12+ across several days.")
    return [head] + notes


def render_report(spec, scores, per_label, met_target, max_fa, grid, data):
    m = spec["metrics"]
    verdict = assess(spec, data, met_target)
    lines = [f"# Model report {spec['version']}", "", f"**Verdict: {verdict[0]}**", ""]
    lines += [f"- {v}" for v in verdict[1:]]
    lines += ["",
             f"- Model: **{m['model']}** using {len(spec['features'])} features",
             f"- Validation: held out one {m['validation']} at a time "
             f"({len(set(data.session))} sessions)",
             f"- Held-out ROC-AUC: **{m['roc_auc']:.3f}**, average precision {m['avg_precision']:.3f}",
             "", "## Alarm performance on held-out data", "",
             f"- Compound periods detected: **{m['detected_periods']}/{m['compound_periods']}**",
             f"- Median time to detect: {m['median_time_to_detect_s']} s",
             f"- False alarms: **{m['false_alarms']}** in {m['negative_hours']:.1f} h of "
             f"non-compound air ({m['false_alarms_per_hour']:.2f}/h, target {max_fa}/h)",
             f"- Alarm thresholds: on {spec['detector']['on_threshold']}, off "
             f"{spec['detector']['off_threshold']}"]
    if not met_target:
        lines += ["", "> **Target false-alarm rate NOT met.** Thresholds were set to the lowest "
                      "false-alarm option. More (and more varied) data is needed."]
    lines += ["", "## How often the alarm was on, per label (held-out)", "",
              "| label | % of time alarm on |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in per_label.items()]
    lines += ["", "For `compound` this should be high; for everything else, near 0.", "",
              "## Model comparison (held-out)", "", "| model | ROC-AUC | avg precision |", "|---|---|---|"]
    lines += [f"| {k} | {v[1]:.3f} | {v[0]:.3f} |" for k, v in scores.items()]
    lines += ["", "## Threshold trade-off", "", "| on threshold | periods caught | false alarms/h |",
              "|---|---|---|"]
    lines += [f"| {c['on_threshold']} | {e['detected_periods']}/{e['compound_periods']} | "
              f"{e['false_alarms_per_hour']:.2f} |" for c, e, _ in grid]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
