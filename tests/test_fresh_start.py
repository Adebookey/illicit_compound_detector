"""The fresh-start flow: guided collection on the device -> `voc-detector train` on the device."""
import json
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "training"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

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
                                  install_anyway=False, only=None, skip=None, training_dir=os.path.join(ROOT, "training"))
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
                                  only=None, skip=None, training_dir=os.path.join(ROOT, "training"))
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


def test_review_shows_sensor_response(tmp_path, capsys):
    from voc_detector import review
    db = str(tmp_path / "voc.sqlite")
    _simulate_guided_sessions(Storage(db), n_sessions=1)
    review.run(db)
    out = capsys.readouterr().out
    assert "clean wobble" in out and out.count("cmp") == 4 and "perfume" in out


def test_train_does_not_install_a_failing_model(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    store = Storage(cli._db(cfg))
    _simulate_guided_sessions(store)
    # sabotage: the "compound" periods get no gas at all -> nothing to learn
    store.db.execute("UPDATE samples SET ens_tvoc=40, ens_eco2=420, ens_aqi=1, "
                     "ens_hp0_rs=120000, ens_hp2_rs=40000, sgp_raw=30000, sgp_voc_index=100")
    store.commit()
    args = cli.argparse.Namespace(model="logreg", max_false_alarms=0.25, no_install=False, force=False,
                                  install_anyway=False, only=None, skip=None, training_dir=os.path.join(ROOT, "training"))
    cli.cmd_train(args, cfg)
    assert not os.path.exists(cfg["device"]["model_path"])
    assert "NOT INSTALLED" in capsys.readouterr().out


def test_guided_box_setup_and_session_filter(tmp_path, monkeypatch, capsys):
    db = str(tmp_path / "voc.sqlite")
    store = Storage(db)
    hub = SimulatedSensorHub(seed=2)
    for i in range(1200):
        store.add_sample(hub.read(now=time.time() - 1200 + i))
    store.commit()
    stop = threading.Event()

    def logger():
        st, h = Storage(db), SimulatedSensorHub(seed=1)
        while not stop.is_set():
            st.add_sample(h.read())
            st.commit()
            time.sleep(0.2)
    threading.Thread(target=logger, daemon=True).start()
    prompts = []
    monkeypatch.setattr("builtins.input", lambda msg="": prompts.append(msg) or "")
    monkeypatch.setattr(guided, "_beep", lambda: None)
    m = 2 / 60
    guided.run(db, rounds=1, compound_min=m, clean_min=m, recovery_min=m, baseline_min=m,
               session="B1", setup="box")
    stop.set()
    printed = capsys.readouterr().out
    assert "box setup" in printed
    assert any("CLOSE the lid again" in p for p in prompts)
    assert any("inside the box" in p for p in prompts)
    cfg = {"device": {"data_dir": str(tmp_path)}}
    cli.cmd_sessions(None, cfg)
    assert "B1: clean" in capsys.readouterr().out


def test_evidence_report_from_weak_compound_data(tmp_path):
    import realistic
    from voc_detector import evidence
    db = str(tmp_path / "voc.sqlite")
    realistic.make(Storage(db))
    report, data, summary = evidence.build(db, str(tmp_path / "ev"), str(tmp_path))
    groups = {r["group"]: r for r in summary}
    assert groups["isopropyl alcohol"]["ens_tvoc"]["median"] > 20 * groups["compound at 5 cm"]["ens_tvoc"]["median"]
    page = open(report).read()
    assert "did not detect it" in page and "<svg" in page
    assert open(data).readline().startswith("time_local,unix_ts,session,period")


def test_demo_server_live_and_marks(tmp_path):
    import json
    import urllib.request
    from voc_detector import demo
    db = str(tmp_path / "voc.sqlite")
    st = Storage(db)
    hub = SimulatedSensorHub(seed=1)
    for i in range(30):
        st.add_sample(hub.read(now=time.time() - 30 + i))
    st.commit()
    th = threading.Thread(target=demo.serve, args=(db, str(tmp_path), 8799), daemon=True)
    th.start()
    time.sleep(0.5)
    live = json.load(urllib.request.urlopen("http://127.0.0.1:8799/api/live?since=0"))
    assert len(live["rows"]) >= 25
    req = urllib.request.Request("http://127.0.0.1:8799/api/mark?what=perfume", method="POST")
    assert json.load(urllib.request.urlopen(req))["open"]["what"] == "perfume"
    req = urllib.request.Request("http://127.0.0.1:8799/api/mark?what=", method="POST")
    assert json.load(urllib.request.urlopen(req))["open"] is None
    labs = Storage(db).labels()
    assert labs[-1]["label"] == "confounder:perfume" and labs[-1]["end_ts"]
    assert "Live sensor readings" in urllib.request.urlopen("http://127.0.0.1:8799/").read().decode()


def test_evidence_skips_demo_sessions(tmp_path):
    import realistic
    from voc_detector import evidence
    db = str(tmp_path / "voc.sqlite")
    st = Storage(db)
    realistic.make(st)
    lid = st.start_label("compound", "DEMO-20261110-0900", "live demo: compound")
    st.end_label(lid)
    assert all(not p["session"].startswith("DEMO-") for p in evidence.collect(st))


def test_placement_relabel_groups_close_range(tmp_path, capsys):
    import realistic
    from voc_detector import evidence
    cfg = _cfg(tmp_path)
    realistic.make(Storage(cli._db(cfg)))
    for d in ("5 cm", "10 cm"):
        cli.cmd_placement(cli.argparse.Namespace(change=d, to="close range", session=None), cfg)
    groups = {p["group"] for p in evidence.collect(Storage(cli._db(cfg)))}
    assert "compound at close range" in groups
    assert not {"compound at 5 cm", "compound at 10 cm"} & groups
    report, _, _ = evidence.build(cli._db(cfg), str(tmp_path / "ev"), str(tmp_path))
    page = open(report).read()
    assert "Options worth considering" not in page and "Close range:" in page and "window.print()" in page
