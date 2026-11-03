# 3. Training the model (on your laptop)

Training runs on a normal laptop (Windows/macOS/Linux), not on the Pi. The
result is a small `models/model.json` (a few KB) that the Pi runs.

## One-time setup

Install Python 3.10+ from python.org, then in this repo folder:

```bash
python -m venv .venv
```

Activate it: `.venv\Scripts\activate` (Windows) or `source .venv/bin/activate` (mac/Linux), then:

```bash
pip install -e ".[train]"
```

## The four steps

```bash
# 1. Look at the raw files: which columns map to which sensor?
python training/import_data.py data/raw/* --inspect

# 2. Convert to the standard format. --tz = timezone the files' times are in
#    (if they contain no timezone). Use your city, e.g. Africa/Lagos, Europe/London.
python training/import_data.py data/raw/* --tz Africa/Lagos -o data/processed/readings.csv

# 3. Apply your written-down periods
python training/label.py data/processed/readings.csv data/labels.csv \
       --tz Africa/Lagos -o data/processed/labelled.csv

# 4. Train, validate, export
python training/train.py data/processed/labelled.csv -o models/model.json
```

If step 1 shows a column it didn't recognise (e.g. `gas_resistance`), map it:
`--map gas_resistance=ens_hp0_rs`. Canonical names are listed in
`src/voc_detector/schema.py`.

Step 3 prints how many readings fell into each period. **A period with 0
readings means a time mismatch**: wrong `--tz`, or the Pi's clock was off (see
`--clock-offset`). Check visually:

```bash
python training/plot_session.py data/processed/labelled.csv --session day1 -o day1.png
```

Red shading (compound) should sit right where the gas readings jump.

## Reading the report

`models/report.md` is written next to the model. The numbers that matter:

- **Compound periods detected (x/y)** - out of periods on days the model never
  saw during that test.
- **False alarms per hour** in clean air and confounders. The thresholds are
  chosen to keep this under `--max-false-alarms` (default 0.25/h = one per
  4 hours). Lower it for a stricter device.
- **"% of time alarm on" per label** - every `confounder:*` row should be near
  0. If, say, perfume shows 40%, the model can't tell perfume apart yet: collect
  more perfume AND more compound data.
- **ROC-AUC** - 0.5 is a coin flip, 1.0 is perfect. Below ~0.85 the model is not
  ready for a client.

With little data (one or two sessions), the report warns that validation is
optimistic. Treat those numbers as an upper bound.

## Retraining later

Data logged by the device itself (`voc-detector export`) is already in the
standard format, and its `_labels.csv` already has the right columns:

```bash
scp USERNAME@IP:~/export*.csv data/raw/
python training/import_data.py data/raw/export.csv -o data/processed/new.csv
python training/label.py data/processed/new.csv data/raw/export_labels.csv -o data/processed/new_labelled.csv
python training/train.py data/processed/labelled.csv data/processed/new_labelled.csv -o models/model.json
```

(The device export's times are already UTC epoch seconds, so no `--tz` needed.)

## How the model works (for the curious)

- `src/voc_detector/features.py` turns the raw stream into 33 features per
  second: each gas signal's deviation from its own clean-air baseline, its rise
  over 30 s and 120 s, and its noise, plus temperature and humidity. The
  same file runs on the Pi, so training and field features are identical.
- `training/train.py` compares a logistic regression and small boosted trees
  and keeps whichever validates better on held-out days.
- `src/voc_detector/detector.py` smooths the per-second probability and
  requires it to stay high for a few seconds before alarming (and low for 30 s
  before clearing), which removes flicker.
- The Pi evaluates the model in pure Python (~6 ms per reading on a Pi Zero).
