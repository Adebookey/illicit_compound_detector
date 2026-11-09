# VOC Detector

A Raspberry Pi Zero device (ENS160 + SGP40 + DHT22) that learns the volatile
compound signature of a target substance (here: cannabis) from labelled
examples, alarms when it is nearby, and reports to a remote dashboard with
battery and online/offline monitoring.

```
 ENS160 ─┐
 SGP40  ─┼─▶ features ─▶ model ─▶ alarm logic ─▶ local log (SQLite) ─▶ Supabase ─▶ dashboard (Vercel)
 DHT22  ─┘   (same code in training and on the Pi)                        ▲
 UPS HAT ─────────────────────────── battery / heartbeat ─────────────────┘
```

## Read this first: what to expect

The ENS160 and SGP40 are general-purpose metal-oxide VOC sensors. They respond
to cannabis terpenes, but also to perfume, alcohol, cooking, cleaning products,
citrus, pine, tobacco and so on. A trained model can learn the *pattern* that
distinguishes the target from those, **but only from data that includes those
other smells**. Expect a useful screening indicator ("something with this
signature is close"), not an identification. It is not evidence and should not
be presented to the client as such. How well it works will show in the
held-out validation report (`models/report.md`) after each training run.
Report those numbers honestly to the client.

## Guide

**Start here: [docs/00-fresh-start.md](docs/00-fresh-start.md)**. Flash a new SD card, install
with one command, record guided sessions and train on the Pi itself. No laptop
tools needed.

```bash
curl -sSL https://raw.githubusercontent.com/Adebookey/illicit_compound_detector/main/pi/bootstrap.sh | sudo bash
voc-detector check      # sensors OK?
voc-detector guided     # labelled collection session (3+ times)
voc-detector train      # train + install the model on the Pi
voc-detector review     # how much each sensor reacted, per period
voc-detector evidence   # results report (HTML) + raw data (CSV)
voc-detector demo       # live readings web page for a demonstration
```

Showing results to the client: [docs/07-client-demo.md](docs/07-client-demo.md).

Reference docs (for the old SD card, laptop training and the dashboard):

| Step | Doc |
|---|---|
| 1. Back up the SD card, SSH into the Pi, find the engineer's code and data | [docs/01-accessing-the-pi.md](docs/01-accessing-the-pi.md) |
| 2. Collect (more) labelled data, including confounders | [docs/02-collecting-data.md](docs/02-collecting-data.md) |
| 3. Train and validate the model on a laptop | [docs/03-training.md](docs/03-training.md) |
| 4. Install the software and model on the Pi | [docs/04-deploying.md](docs/04-deploying.md) |
| 5. Set up the remote dashboard | [docs/05-dashboard.md](docs/05-dashboard.md) |

## Repo layout

```
src/voc_detector/      runs on the Pi (pure Python inference, no numpy needed)
  sensors.py           ENS160 / SGP40 / DHT22 drivers + simulator
  features.py          streaming feature extraction (shared with training)
  model.py             evaluates the exported JSON model
  detector.py          smoothing + hysteresis -> stable alarm
  battery.py           PiSugar / INA219 UPS HAT, auto-detected
  storage.py           local SQLite log + offline upload queue
  uploader.py          sends to Supabase
  guided.py            step-by-step labelled data collection
  agent.py, cli.py     main loop; `voc-detector run|check|guided|train|status|export`
training/              runs on a laptop
  import_data.py       converts the engineer's logs (CSV/JSON/SQLite) to a standard CSV
  label.py             applies your written-down times
  plot_session.py      visual check of labels vs. sensor response
  train.py             trains, validates per day, tunes alarm thresholds, exports model.json
  pipeline.py          import + label + train in one command
  make_synthetic.py    fake data for testing the pipeline only
pi/                    bootstrap.sh (one-line setup), install.sh, systemd service, config, inventory.sh
dashboard/             Supabase SQL + static web dashboard
tests/                 pytest suite (runs without hardware)
```

## Quick checks without hardware

```bash
pip install -e ".[dev]"
pytest
python training/make_synthetic.py --out-dir data/synthetic   # fake data, pipeline test only
```
