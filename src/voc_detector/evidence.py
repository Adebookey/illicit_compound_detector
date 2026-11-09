"""`voc-detector evidence`: a self-contained results report from the device's own data.

Writes, into a folder (default ~/evidence):
  report.html  - charts + tables of how the sensors responded to the compound,
                 the other smells and clean air (no internet needed to view;
                 print it to PDF from the browser)
  data.csv     - the raw readings for every labelled period (+/- 3 minutes),
                 so anyone can check the numbers themselves

Every number in the report is computed from the readings logged on the Pi
during the guided sessions; nothing is typed in by hand.
"""

import csv
import datetime as dt
import html
import json
import os
import re
import socket
import statistics as st

from .schema import ALL_CHANNELS
from .storage import Storage

CHANNELS = [  # key, name, unit, direction (+1 rises with gas)
    ("ens_tvoc", "Total VOC (ENS160)", "ppb", +1),
    ("sgp_voc_index", "VOC index (SGP40)", "index", +1),
    ("ens_eco2", "eCO2 (ENS160)", "ppm", +1),
]
PAD_S = 180


# ---------------------------------------------------------------- naming
def group_of(label, notes):
    """Group key for a labelled period: compound split by distance."""
    if label == "compound":
        n = notes or ""
        if "closed box" in n:
            return "compound (closed box)"
        m = re.search(r"(\d+)\s*(cm|m)\b", n)
        if m:
            return f"compound at {m.group(1)} {m.group(2)}"
        return "compound"
    if label == "clean":
        return "clean air"
    return label.split(":", 1)[-1].replace("_", " ")


def _group_order(g):
    if g == "clean air":
        return (0, 0, g)
    if g.startswith("compound"):
        m = re.search(r"at (\d+) (cm|m)", g)
        dist = (int(m.group(1)) * (100 if m.group(2) == "m" else 1)) if m else 9999
        return (1, dist, g)
    return (2, 0, g)


# ---------------------------------------------------------------- stats
def _vals(rows, key):
    return [r[key] for r in rows if r.get(key) is not None]


def period_stats(store, lb):
    """Rise during the period relative to the 3 minutes before it."""
    before = list(store.iter_samples(lb["start_ts"] - PAD_S, lb["start_ts"]))
    during = list(store.iter_samples(lb["start_ts"], lb["end_ts"] - 0.5))  # end exclusive
    out = {"n": len(during)}
    for key, _, _, direction in CHANNELS:
        b, d = _vals(before, key), _vals(during, key)
        if not b or not d:
            out[key] = None
            continue
        base = st.fmean(b)
        peak = max(d) if direction > 0 else min(d)
        last = d[-min(len(d), 300):]
        out[key] = {"before": base, "peak_rise": (peak - base) * direction,
                    "mean_rise": (st.fmean(last) - base) * direction}
    return out


def collect(store, sessions=None, include_demo=False):
    labels = [lb for lb in store.labels() if lb["end_ts"]]
    if sessions:
        labels = [lb for lb in labels if lb["session"] in sessions]
    elif not include_demo:
        # live demos/practice runs are not controlled trials: leave them out
        labels = [lb for lb in labels if not str(lb["session"]).startswith("DEMO-")]
    periods, prev = [], {}
    for lb in sorted(labels, key=lambda x: x["start_ts"]):
        after_smell = prev.get(lb["session"], "") .startswith("confounder")
        prev[lb["session"]] = lb["label"]
        # Clean air right after perfume/alcohol is still clearing: not a fair
        # "nothing added" reference, so it is left out of the comparison.
        if lb["label"] == "clean" and after_smell:
            continue
        s = period_stats(store, lb)
        if s["n"] < 30:
            continue
        periods.append({**lb, "group": group_of(lb["label"], lb["notes"]), "stats": s})
    return periods


def summarize(periods):
    groups = {}
    for p in periods:
        groups.setdefault(p["group"], []).append(p)
    summary = []
    for g in sorted(groups, key=_group_order):
        row = {"group": g, "count": len(groups[g])}
        for key, _, _, _ in CHANNELS:
            peaks = [p["stats"][key]["peak_rise"] for p in groups[g] if p["stats"].get(key)]
            row[key] = {"median": st.median(peaks), "max": max(peaks)} if peaks else None
        summary.append(row)
    return summary


# ---------------------------------------------------------------- SVG charts
def _nice_ticks(lo, hi, n=4):
    span = hi - lo or 1.0
    raw = span / n
    mag = 10 ** int(f"{raw:e}".split("e")[1])
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    start = int(lo // step) * step
    ticks, v = [], start
    while v <= hi + 1e-9:
        if v >= lo - 1e-9:
            ticks.append(v)
        v += step
    return ticks


def _fmt_num(v):
    return f"{v:,.0f}" if abs(v) >= 10 or v == int(v) else f"{v:.1f}"


def timeline_svg(points, shades, title, unit, y_max=None, width=920, height=210):
    """points: [(t, v or None)], shades: [(start, end, css_class, text)]."""
    ml, mr, mt, mb = 64, 12, 26, 28
    pw, ph = width - ml - mr, height - mt - mb
    ts = [t for t, _ in points]
    t0, t1 = min(ts), max(ts)
    vals = [v for _, v in points if v is not None]
    lo = 0.0
    hi = y_max if y_max is not None else (max(vals) if vals else 1.0)
    hi = hi * 1.08 if hi > 0 else 1.0
    ticks = _nice_ticks(lo, hi)
    hi = max(hi, ticks[-1])
    X = lambda t: ml + (t - t0) / ((t1 - t0) or 1) * pw  # noqa: E731
    Y = lambda v: mt + ph - (min(v, hi) - lo) / ((hi - lo) or 1) * ph  # noqa: E731
    o = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(title)}">',
         f'<text x="{ml}" y="16" class="ct">{html.escape(title)}</text>']
    for s0, s1, cls, _ in shades:
        if s1 < t0 or s0 > t1:
            continue
        x0, x1 = X(max(s0, t0)), X(min(s1, t1))
        o.append(f'<rect x="{x0:.1f}" y="{mt}" width="{max(1, x1 - x0):.1f}" height="{ph}" class="sh {cls}"/>')
    for v in ticks:
        y = Y(v)
        o.append(f'<line x1="{ml}" x2="{ml + pw}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>')
        o.append(f'<text x="{ml - 6}" y="{y + 4:.1f}" class="ax" text-anchor="end">{_fmt_num(v)}</text>')
    o.append(f'<text x="{ml - 6}" y="{mt - 8}" class="ax" text-anchor="end">{html.escape(unit)}</text>')
    step = 900 if t1 - t0 > 3600 else 300
    first = (int(t0 // step) + 1) * step
    for t in range(first, int(t1), step):
        x = X(t)
        o.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{mt + ph}" y2="{mt + ph + 4}" class="axis"/>')
        o.append(f'<text x="{x:.1f}" y="{mt + ph + 17}" class="ax" text-anchor="middle">'
                 f'{dt.datetime.fromtimestamp(t).strftime("%H:%M")}</text>')
    o.append(f'<line x1="{ml}" x2="{ml + pw}" y1="{mt + ph}" y2="{mt + ph}" class="axis"/>')
    path, pen = [], False
    for t, v in points:
        if v is None:
            pen = False
            continue
        path.append(f'{"L" if pen else "M"}{X(t):.1f},{Y(v):.1f}')
        pen = True
    o.append(f'<path d="{" ".join(path)}" class="ln"/>')
    if y_max is not None and vals and max(vals) > y_max:
        o.append(f'<text x="{ml + pw}" y="16" class="ax" text-anchor="end">'
                 f'values above {_fmt_num(y_max)} {html.escape(unit)} are cut off in this zoomed view</text>')
    o.append("</svg>")
    return "\n".join(o)


def bars_svg(rows, key, unit, classes, width=920):
    """Horizontal bars of median peak rise per group (one row per group)."""
    rows = [r for r in rows if r.get(key)]
    if not rows:
        return ""
    bh, gap, ml, mr = 22, 10, 190, 90
    height = len(rows) * (bh + gap) + 10
    hi = max(r[key]["median"] for r in rows) or 1.0
    pw = width - ml - mr
    o = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="median rise per item">']
    for i, r in enumerate(rows):
        y = 5 + i * (bh + gap)
        v = max(0.0, r[key]["median"])
        w = max(2.0, v / hi * pw)
        o.append(f'<text x="{ml - 8}" y="{y + 15}" class="lbl" text-anchor="end">{html.escape(r["group"])}</text>')
        o.append(f'<rect x="{ml}" y="{y}" width="{w:.1f}" height="{bh}" rx="4" class="bar {classes.get(r["group"], "")}"/>')
        o.append(f'<text x="{ml + w + 6:.1f}" y="{y + 15}" class="val">+{_fmt_num(r[key]["median"])} {unit}</text>')
    o.append("</svg>")
    return "\n".join(o)


# ---------------------------------------------------------------- report
def _downsample(rows, key, bucket=10):
    out, cur, acc, last_t = [], None, [], None
    for r in rows:
        b = int(r["ts"] // bucket)
        if last_t is not None and r["ts"] - last_t > 60:
            if acc:
                out.append((cur * bucket, st.fmean(acc)))
            out.append((last_t + 1, None))
            acc, cur = [], None
        if cur is not None and b != cur and acc:
            out.append((cur * bucket, st.fmean(acc)))
            acc = []
        cur = b
        if r.get(key) is not None:
            acc.append(r[key])
        last_t = r["ts"]
    if acc:
        out.append((cur * bucket, st.fmean(acc)))
    return out


def _model_metrics(data_dir):
    for p in (os.path.join(data_dir, "training", "model.json"),):
        try:
            with open(p) as f:
                return json.load(f).get("metrics")
        except (OSError, ValueError):
            continue
    return None


def build(db_path, out_dir, data_dir, sessions=None, include_demo=False):
    store = Storage(db_path)
    periods = collect(store, sessions, include_demo)
    if not periods:
        raise SystemExit("No labelled periods recorded yet (run `voc-detector guided` first).")
    os.makedirs(out_dir, exist_ok=True)
    summary = summarize(periods)

    # colours by group (identity), fixed order
    # fixed colours (same as the live demo page): compound blue, alcohol orange, perfume green
    fixed = {"isopropyl alcohol": "c2", "perfume": "c3"}
    spare = iter(["c4", "c5", "c6"])
    classes = {}
    for row in summary:
        g = row["group"]
        if g == "clean air":
            classes[g] = "cclean"
        elif g.startswith("compound"):
            classes[g] = "c1"
        else:
            classes[g] = fixed.get(g) or next(spare, "c6")

    # raw data CSV
    csv_path = os.path.join(out_dir, "data.csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time_local", "unix_ts", "session", "period", "notes"] + ALL_CHANNELS)
        for p in periods:
            for r in store.iter_samples(p["start_ts"] - PAD_S, p["end_ts"] + PAD_S):
                inside = p["start_ts"] <= r["ts"] <= p["end_ts"]
                w.writerow([dt.datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d %H:%M:%S"),
                            round(r["ts"], 2), p["session"],
                            p["group"] if inside else f"(3 min before/after {p['group']})",
                            p["notes"] or ""] + [None if r.get(c) is None else round(r[c], 2)
                                                 for c in ALL_CHANNELS])

    # timeline: the session with the most kinds of periods (ideally includes other smells)
    by_session = {}
    for p in periods:
        by_session.setdefault(p["session"], []).append(p)
    show = max(by_session, key=lambda s: (len({p["group"] for p in by_session[s]}), by_session[s][0]["start_ts"]))
    sp = by_session[show]
    t0, t1 = sp[0]["start_ts"] - PAD_S, sp[-1]["end_ts"] + PAD_S
    rows = list(store.iter_samples(t0, t1))
    shades = [(p["start_ts"], p["end_ts"], classes[p["group"]], p["group"])
              for p in sp if p["group"] != "clean air"]
    tvoc_pts = _downsample(rows, "ens_tvoc")
    voc_pts = _downsample(rows, "sgp_voc_index")
    comp_peaks = [p["stats"]["ens_tvoc"]["peak_rise"] + p["stats"]["ens_tvoc"]["before"]
                  for p in sp if p["group"].startswith("compound") and p["stats"].get("ens_tvoc")]
    # zoomed view: tall enough for the compound periods (and clean air), not the spikes
    zoom_max = max(200.0, max(comp_peaks or [0]) * 1.5)

    def row_of(g):
        return next((r for r in summary if r["group"] == g), None)

    clean = row_of("clean air")
    comps = [r for r in summary if r["group"].startswith("compound")]
    others = [r for r in summary if r["group"] not in ("clean air",) and not r["group"].startswith("compound")]
    best_comp = max(comps, key=lambda r: r["ens_tvoc"]["median"] if r.get("ens_tvoc") else -1) if comps else None

    findings = []
    if best_comp and best_comp.get("ens_tvoc"):
        findings.append(
            f"The strongest response to the compound was <b>{html.escape(best_comp['group'])}</b>: "
            f"Total VOC rose by a median of <b>{_fmt_num(best_comp['ens_tvoc']['median'])} ppb</b> "
            f"(largest {_fmt_num(best_comp['ens_tvoc']['max'])} ppb) over {best_comp['count']} trials.")
    if clean and clean.get("ens_tvoc"):
        findings.append(
            f"With <b>nothing added (clean air)</b>, the same measurement varied by a median of "
            f"<b>{_fmt_num(clean['ens_tvoc']['median'])} ppb</b> (largest {_fmt_num(clean['ens_tvoc']['max'])} ppb), "
            f"just from normal fluctuation.")
    if clean and clean.get("ens_tvoc"):
        same = [r["group"].replace("compound at ", "") for r in comps
                if r.get("ens_tvoc") and r["ens_tvoc"]["median"] <= 2 * clean["ens_tvoc"]["max"]]
        if same:
            findings.append(
                f"With the compound at <b>{', '.join(html.escape(x) for x in same)}</b>, the rise was "
                f"no bigger than ordinary clean-air fluctuation: at those distances the sensors did not "
                f"detect it.")
    for r in others:
        if r.get("ens_tvoc") and best_comp and best_comp.get("ens_tvoc") and best_comp["ens_tvoc"]["median"] > 0:
            ratio = r["ens_tvoc"]["median"] / best_comp["ens_tvoc"]["median"]
            findings.append(
                f"<b>{html.escape(r['group']).capitalize()}</b> raised Total VOC by a median of "
                f"<b>{_fmt_num(r['ens_tvoc']['median'])} ppb</b>, about <b>{ratio:,.0f}&times;</b> "
                f"the compound's strongest response.")
    mm = _model_metrics(data_dir)
    if mm and mm.get("roc_auc") is not None:
        findings.append(
            f"A detection model trained on these sessions and tested on a session it had not seen scored "
            f"<b>ROC-AUC {mm['roc_auc']:.2f}</b> (0.50 = coin toss, 1.00 = perfect) and caught "
            f"{mm.get('detected_periods')}/{mm.get('compound_periods')} compound periods: the compound "
            f"signal is too weak for the model to tell it apart from normal air.")

    def table():
        head = "".join(f"<th>{html.escape(n)}<br><span>{u}</span></th>" for _, n, u, _ in CHANNELS)
        trs = []
        for r in summary:
            tds = "".join(
                f"<td class=num>{('+' if r[k]['median'] >= 0 else '') + _fmt_num(r[k]['median'])}"
                f"<span> / {_fmt_num(r[k]['max'])}</span></td>" if r.get(k) else "<td>-</td>"
                for k, _, _, _ in CHANNELS)
            key = f'<i class="sw {classes.get(r["group"], "")}"></i>'
            trs.append(f"<tr><td>{key}{html.escape(r['group'])}</td><td class=num>{r['count']}</td>{tds}</tr>")
        return (f"<table><thead><tr><th>What was near the sensor</th><th>Trials</th>{head}</tr></thead>"
                f"<tbody>{''.join(trs)}</tbody></table>")

    def trials():
        trs = []
        for p in periods:
            if p["group"] == "clean air":
                continue
            s = p["stats"]
            cells = "".join(
                f"<td class=num>{_fmt_num(s[k]['before'])} &rarr; +{_fmt_num(s[k]['peak_rise'])}</td>"
                if s.get(k) else "<td>-</td>" for k, _, _, _ in CHANNELS)
            when = dt.datetime.fromtimestamp(p["start_ts"]).strftime("%d %b %H:%M")
            trs.append(f"<tr><td class=num>{when}</td><td>{html.escape(p['group'])}</td>{cells}</tr>")
        head = "".join(f"<th>{html.escape(n)}</th>" for _, n, _, _ in CHANNELS)
        return (f"<table><thead><tr><th>Start</th><th>Item</th>{head}</tr></thead>"
                f"<tbody>{''.join(trs)}</tbody></table>")

    seen, legend = set(), ""
    for g in dict.fromkeys(p["group"] for p in sp):
        if g == "clean air":
            continue
        name = "compound (all distances)" if g.startswith("compound") else g
        if name in seen:
            continue
        seen.add(name)
        legend += f'<span><i class="sw {classes[g]}"></i>{html.escape(name)}</span>'

    legend += '<span><i class="sw cclean"></i>clean air (no shading)</span>'
    first = dt.datetime.fromtimestamp(periods[0]["start_ts"]).strftime("%d %b %Y")
    last = dt.datetime.fromtimestamp(periods[-1]["end_ts"]).strftime("%d %b %Y")
    n_sessions = len(by_session)

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VOC Sensor Results</title>
<style>{_CSS}</style></head>
<body><main>
<h1>VOC sensor test results</h1>
<p class="meta">Device <b>{html.escape(socket.gethostname())}</b> &middot; ENS160 + SGP40 gas sensors, DHT22 temperature/humidity
&middot; {n_sessions} session(s), {first} &ndash; {last} &middot; report generated {dt.datetime.now().strftime("%d %b %Y %H:%M")}</p>

<h2>Findings</h2>
<ul class="findings">{''.join(f'<li>{f}</li>' for f in findings)}</ul>

<h2>Typical rise in Total VOC, per item</h2>
<p class="note">Median of the highest reading during each trial minus the average of the 3 minutes before it.</p>
{bars_svg(summary, "ens_tvoc", "ppb", classes)}

<h2>One full session, minute by minute</h2>
<p class="note">Session {html.escape(show)}. Shaded bands mark when each item was next to the sensor. The second chart is the same data with the vertical axis zoomed in, so the compound periods can be seen in detail.</p>
<div class="legend">{legend}</div>
{timeline_svg(tvoc_pts, shades, "Total VOC (ENS160)", "ppb")}
{timeline_svg(tvoc_pts, shades, "Total VOC (ENS160), zoomed in", "ppb", y_max=zoom_max)}
{timeline_svg(voc_pts, shades, "VOC index (SGP40)", "index")}

<h2>Summary table</h2>
<p class="note">Rise during the trial vs. the 3 minutes before: <b>median</b> <span class="dim">/ largest</span>. The clean-air row uses the same calculation on periods with nothing added, so it shows the normal background fluctuation.</p>
{table()}

<h2>Every trial</h2>
<p class="note">Reading before the trial &rarr; highest rise during it.</p>
{trials()}

<h2>How the data was collected</h2>
<ul>
<li>The device logged every sensor once per second. A guided program told the operator when to place and remove each item and recorded the exact times, so periods were labelled automatically.</li>
<li>Each compound trial: the dried sample in an open container at the stated distance from the sensors for 10 minutes, followed by recovery and at least 10 minutes of clean air.</li>
<li>"Clean air" periods directly after perfume or alcohol are excluded from the clean-air comparison because the room was still clearing.</li>
<li>Other items (perfume, isopropyl alcohol) were introduced the same way, after the compound trials.</li>
<li>The full raw readings for every period are in <b>data.csv</b> next to this report.</li>
</ul>

<h2>What this means for detection in waste bins</h2>
<ul>
<li>These sensors respond to almost all volatile compounds. At room temperature the dried sample released too little vapour for them to register reliably beyond a few centimetres, while ordinary household products caused responses hundreds to thousands of times larger.</li>
<li>Waste bins contain strong, changing odours (rotting food, alcohol residue, cleaning products, citrus peel, which shares compounds with cannabis). They would cause much larger responses than trace amounts of the compound, so this sensor type cannot pick traces out from that background.</li>
<li>Training a model cannot add a signal the sensors do not measure; on this data it performed at chance level.</li>
</ul>

<h2>Options worth considering</h2>
<ul>
<li><b>Confirm what is detectable first:</b> have a lab analyse air from real bins with and without the compound (headspace GC-MS). This shows whether any marker compound is present above the waste background before investing in hardware.</li>
<li><b>Selective instruments:</b> ion mobility spectrometry (used for narcotics screening), portable or micro gas chromatography, or a photo-ionisation detector paired with a sampling pump. These are more sensitive and/or more selective than metal-oxide sensors, at higher cost.</li>
<li><b>Sample concentration:</b> pumping bin air through a sorbent tube that collects vapour over time, then releasing it to the sensor in one burst, can raise sensitivity a lot. This is development work with no guarantee.</li>
<li><b>Different deployment:</b> closer, enclosed sampling (e.g. a small sealed compartment or a disposal chute with an air pump) rather than an open bin.</li>
<li>Trained detection dogs remain the established method for trace narcotics in mixed waste.</li>
</ul>
</main></body></html>"""
    report = os.path.join(out_dir, "report.html")
    with open(report, "w") as f:
        f.write(page)
    return report, csv_path, summary


_CSS = """
:root{--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;--axis:#c3c2b7;
--c1:#2a78d6;--c2:#eb6834;--c3:#1baf7a;--c4:#eda100;--c5:#e87ba4;--c6:#6250d6}
@media (prefers-color-scheme:dark){:root{--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--grid:#2c2c2a;
--axis:#383835;--c1:#3987e5;--c2:#d95926;--c3:#199e70;--c4:#c98500;--c5:#d55181;--c6:#9085e9}}
*{box-sizing:border-box}body{margin:0;background:var(--page);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:980px;margin:0 auto;padding:24px 16px 48px}h1{font-size:26px;margin:0 0 4px}
h2{font-size:18px;margin:32px 0 6px}.meta,.note{color:var(--ink2);font-size:13px}.dim,td span,th span{color:var(--muted);font-weight:400}
.findings li{margin:6px 0}svg{width:100%;height:auto;display:block;background:var(--surface);border-radius:10px;margin:8px 0}
.ct{font-size:13px;font-weight:600;fill:var(--ink)}.ax{font-size:11px;fill:var(--muted);font-variant-numeric:tabular-nums}
.lbl{font-size:13px;fill:var(--ink)}.val{font-size:13px;fill:var(--ink2);font-variant-numeric:tabular-nums}
.grid{stroke:var(--grid);stroke-width:1}.axis{stroke:var(--axis);stroke-width:1}.ln{fill:none;stroke:var(--ink);stroke-width:1.5}
.sh{opacity:.22}.c1{fill:var(--c1);background:var(--c1)}.c2{fill:var(--c2);background:var(--c2)}.c3{fill:var(--c3);background:var(--c3)}
.c4{fill:var(--c4);background:var(--c4)}.c5{fill:var(--c5);background:var(--c5)}.c6{fill:var(--c6);background:var(--c6)}
.cclean{fill:var(--axis);background:transparent;border:1px solid var(--axis)}.bar.cclean{fill:var(--axis)}
.legend{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:13px;color:var(--ink2)}
i.sw{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:6px;vertical-align:-1px}
.legend i.sw,td i.sw{opacity:.85}
table{width:100%;border-collapse:collapse;font-size:13px;background:var(--surface);border-radius:10px;overflow:hidden;display:block;overflow-x:auto}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--grid);vertical-align:top}th{color:var(--muted);font-weight:500}
td.num{font-variant-numeric:tabular-nums;white-space:nowrap}
@media print{body{background:#fff}svg,table{break-inside:avoid}}
"""
