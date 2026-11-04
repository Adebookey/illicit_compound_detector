"""The fresh-start flow: guided collection on the device -> `voc-detector train` on the device."""
import json
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "training"))

from voc_detector import cli, guided  # noqa: E402
from voc_detector.sensors import SimulatedSensorHub  # noqa: E402
from voc_detector.storage import Storage  # noqa: E402
from make_synthetic import COMPOUND, CONFOUNDERS  # noqa: E402


def _simulate_guided_sessions(store, n_sessions=3, confounder=True):
    """Write samples + labels exactly as the service + `guided` would."""
    t = 1_790_000_000.0
    for s in range(n_sessions):
        hub = SimulatedSensorHub(seed=10 + s)
        hub.t0 = t
        session = f"S{s}"
        plan = [("warm", 15, None), ("clean", 10, None)]
        for _ in range(4):
            plan += [("compound", 10, COMPOUND), ("recovery", 3, None), ("clean", 10, None)]
        if confounder and s == 0:
            plan += [("confounder:perfume", 5, CONFOUNDERS["confounder:alcohol"]),
                     ("recovery", 3, None), ("clean", 10, None)]
        for label, minutes, profile in plan:
            hub.gas = 0.6 if profile else 0.0
            if profile:
                hub.profile = profile
            lid = store.start_label(label, session, "", ts=t) if label not in ("warm", "recovery") else None
            for _ in range(int(minutes * 60)):
                store.add_sample(hub.read(now=t))
                t += 1
            if lid:
                store.end_label(lid, ts=t)
        t += 6 * 3600
    store.commit()


def _cfg(tmp_path):
    cfg = cli.config_mod.load(str(tmp_path / "none.toml"))
    cfg["device"].update(data_dir=str(tmp_path), model_path=str(tmp_path / "installed" / "model.json"))
    return cfg


def test_train_on_device_from_guided_sessions(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    _simulate_guided_sessions(Storage(cli._db(cfg)))
    args = cli.argparse.Namespace(model="logreg", max_false_alarms=0.25, no_install=False, force=False,
                                  training_dir=os.path.join(ROOT, "training"))
    cli.cmd_train(args, cfg)
    spec = json.load(open(cfg["device"]["model_path"]))
    assert spec["metrics"]["validation"] == "session"
    assert spec["metrics"]["detected_periods"] >= 10
    report = open(tmp_path / "training" / "report.md").read()
    assert "Verdict:" in report


def test_train_refuses_with_too_few_sessions(tmp_path):
    cfg = _cfg(tmp_path)
    _simulate_guided_sessions(Storage(cli._db(cfg)), n_sessions=1)
    args = cli.argparse.Namespace(model="logreg", max_false_alarms=0.25, no_install=True, force=False,
                                  training_dir=os.path.join(ROOT, "training"))
    try:
        cli.cmd_train(args, cfg)
        raise AssertionError("should have refused")
    except SystemExit as e:
        assert "at least 3" in str(e)


def test_guided_records_labels(tmp_path, monkeypatch):
    db = str(tmp_path / "voc.sqlite")
    store = Storage(db)
    stop = threading.Event()

    def logger():  # stands in for the background service
        st, hub = Storage(db), SimulatedSensorHub(seed=1)
        while not stop.is_set():
            st.add_sample(hub.read())
            st.commit()
            time.sleep(0.2)

    # pretend the logger has been running for a while (skips warm-up)
    hub = SimulatedSensorHub(seed=2)
    for i in range(1200):
        store.add_sample(hub.read(now=time.time() - 1200 + i))
    store.commit()
    th = threading.Thread(target=logger, daemon=True)
    th.start()
    monkeypatch.setattr("builtins.input", lambda *_: "")
    monkeypatch.setattr(guided, "_beep", lambda: None)
    m = 2 / 60  # 2-second periods
    guided.run(db, rounds=2, compound_min=m, clean_min=m, recovery_min=m, baseline_min=m,
               confounders=["perfume"], confounder_min=m, session="T1")
    stop.set()
    labels = [(lb["label"], lb["session"]) for lb in Storage(db).labels()]
    assert labels == [("clean", "T1"), ("compound", "T1"), ("clean", "T1"), ("compound", "T1"),
                      ("clean", "T1"), ("confounder:perfume", "T1"), ("clean", "T1")]
    assert all(lb["end_ts"] for lb in Storage(db).labels())
