import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "training"))

from voc_detector import config as config_mod  # noqa: E402
from voc_detector.agent import Agent  # noqa: E402
from voc_detector.detector import Detector  # noqa: E402
from voc_detector.features import FeatureExtractor  # noqa: E402
from voc_detector.model import Model  # noqa: E402
from voc_detector.sensors import SimulatedSensorHub  # noqa: E402
from voc_detector.storage import Storage  # noqa: E402
from voc_detector.uploader import Uploader  # noqa: E402

import label as label_mod  # noqa: E402
import train as train_mod  # noqa: E402


# ------------------------------------------------------------------ features
def test_feature_extractor_warmup_gas_and_gap():
    hub = SimulatedSensorHub(seed=1)
    fx = FeatureExtractor(warmup_s=60)
    t = 1_000_000.0
    outs = [fx.update(hub.read(now=t + i)) for i in range(300)]
    assert all(o is None for o in outs[:59])
    clean = outs[-1]
    assert set(clean) == set(FeatureExtractor.feature_names())
    hub.gas = 0.8
    for i in range(300, 420):
        f = fx.update(hub.read(now=t + i))
    assert f["ens_tvoc_rel"] > clean["ens_tvoc_rel"] + 0.5
    assert f["ens_hp2_rs_rel"] > 0.1  # resistance drop -> positive deviation
    assert f["sgp_raw_d120"] > 0
    # a long gap restarts warm-up
    assert fx.update(hub.read(now=t + 5000)) is None


def test_missing_channels_are_none():
    fx = FeatureExtractor(warmup_s=0)
    f = fx.update({"ts": 1.0, "ens_tvoc": 50.0, "sgp_raw": None, "temp_c": float("nan")})
    assert f["sgp_raw_rel"] is None and f["temp_c"] is None and f["ens_tvoc_rel"] is not None


# ------------------------------------------------------------------ detector
def test_detector_hysteresis():
    d = Detector(on_threshold=0.6, off_threshold=0.4, smoothing_s=0.001, min_on_s=5, min_off_s=10)
    t, changes = 0.0, []
    for p in [0.1] * 10 + [0.9] * 3 + [0.1] * 3 + [0.9] * 10 + [0.5] * 20 + [0.1] * 15:
        _, active, changed = d.update(t, p)
        if changed:
            changes.append((t, active))
        t += 1
    # the 3-second blip is ignored; one ON, one OFF
    assert [a for _, a in changes] == [True, False]
    assert changes[0][0] == 21  # 16 + 5 s


# ------------------------------------------------------------------ model export parity
@pytest.mark.parametrize("kind", ["logreg", "gbt"])
def test_export_matches_sklearn(kind):
    rng = np.random.default_rng(0)
    X = rng.normal(size=(600, 5))
    y = (X[:, 0] + 0.5 * X[:, 1] ** 2 + rng.normal(scale=0.5, size=600) > 0.5).astype(int)
    m = train_mod.fit(kind, X, y)
    names = [f"f{i}" for i in range(5)]
    spec = train_mod.export(kind, m, names, X.mean(axis=0))
    pm = Model(json.loads(json.dumps(spec)))
    ref = train_mod.predict(kind, m, X)
    got = [pm.predict_proba(dict(zip(names, row))) for row in X]
    assert np.max(np.abs(np.array(got) - ref)) < 1e-9
    # missing feature -> filled with training mean
    p = pm.predict_proba({"f0": 1.0})
    assert 0 <= p <= 1


# ------------------------------------------------------------------ labels
def test_label_timezone_and_offset(tmp_path):
    lp = tmp_path / "labels.csv"
    lp.write_text("start,end,label\n2026-10-28 14:00,2026-10-28 14:10,compound\n")
    lab = label_mod.load_labels(lp, tz="Africa/Lagos", clock_offset=30)
    # 14:00 Lagos (UTC+1) = 13:00 UTC; +30 s Pi clock offset
    assert lab.start_ts[0] == pd.Timestamp("2026-10-28 13:00:30", tz="UTC").timestamp()
    readings = pd.DataFrame({"ts": lab.start_ts[0] + np.arange(0, 700, 10.0)})
    out = label_mod.apply_labels(readings, lab, margin_s=60)
    assert out.label.isna().sum() == 6 + 10  # 60 s settling margin + rows after the end
    assert (out.label == "compound").sum() == 54


# ------------------------------------------------------------------ end to end
def _train(tmp_path):
    py = sys.executable
    run = lambda *a: subprocess.run([py, *a], cwd=ROOT, check=True, capture_output=True, text=True)  # noqa: E731
    run("training/make_synthetic.py", "--out-dir", str(tmp_path), "--sessions", "3")
    run("training/import_data.py", str(tmp_path / "readings.csv"), "-o", str(tmp_path / "imp.csv"))
    run("training/label.py", str(tmp_path / "imp.csv"), str(tmp_path / "labels.csv"),
        "-o", str(tmp_path / "lab.csv"))
    run("training/train.py", str(tmp_path / "lab.csv"), "-o", str(tmp_path / "model.json"),
        "--model", "logreg")
    spec = json.loads((tmp_path / "model.json").read_text())
    assert spec["metrics"]["validation"] == "session"
    assert spec["metrics"]["roc_auc"] > 0.9
    assert (tmp_path / "report.md").exists()
    return tmp_path / "model.json"


def test_full_training_pipeline(tmp_path):
    _train(tmp_path)


def test_agent_detects_and_queues(tmp_path):
    model_path = _train(tmp_path)
    cfg = config_mod.load(str(tmp_path / "missing.toml"))
    cfg["device"].update(data_dir=str(tmp_path / "data"), model_path=str(model_path))
    cfg["sensors"]["simulate"] = True
    cfg["cloud"].update(enabled=True, supabase_url="http://127.0.0.1:9", supabase_anon_key="a",
                        device_key="k")
    from make_synthetic import COMPOUND
    hub = SimulatedSensorHub(seed=3)
    agent = Agent(cfg, hub=hub)
    t = 2_000_000_000.0
    active = []
    for i in range(1500):
        if i == 600:
            hub.profile, hub.gas = COMPOUND, 0.7
        if i == 1100:
            hub.gas = 0.0
        s = hub.read(now=t + i)
        _, _, a = agent.step(s)
        active.append(a)
    assert not any(active[:600])
    assert any(active[600:1100])
    agent.store.commit()
    kinds = [k for _, k, p in agent.store.peek_outbox(1000) if k == "event"]
    events = [p["kind"] for _, k, p in agent.store.peek_outbox(1000) if k == "event"]
    assert "detection_start" in events and kinds
    assert agent.store.db.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 1500


# ------------------------------------------------------------------ uploader
def test_uploader_posts_and_acks(tmp_path):
    received = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append((self.path, self.headers["apikey"], body))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    db = str(tmp_path / "v.sqlite")
    st = Storage(db)
    st.enqueue("reading", {"ts": 1.0, "ens_tvoc": 5})
    st.enqueue("event", {"ts": 2.0, "kind": "boot", "details": {}})
    st.enqueue("heartbeat", {"ts": 3.0, "battery_pct": 80})
    up = Uploader(db, {"supabase_url": f"http://127.0.0.1:{srv.server_port}",
                       "supabase_anon_key": "anon", "device_key": "secret"}, "dev1")
    assert up.flush(st) == 3
    path, key, body = received[0]
    assert path == "/rest/v1/rpc/ingest" and key == "anon"
    assert body["p_device_id"] == "dev1" and body["p_key"] == "secret"
    assert len(body["p_readings"]) == len(body["p_events"]) == len(body["p_heartbeats"]) == 1
    assert st.outbox_size() == 0
    srv.shutdown()
