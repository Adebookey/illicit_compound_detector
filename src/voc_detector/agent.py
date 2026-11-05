"""The main loop that runs on the Pi as a systemd service."""

import logging
import os
import shutil
import signal
import socket
import subprocess
import time

from . import battery as battery_mod
from .detector import Detector
from .features import FeatureExtractor
from .model import Model
from .storage import Storage

log = logging.getLogger("voc_detector")

UPLOAD_FIELDS = ("ens_tvoc", "ens_eco2", "ens_aqi", "sgp_raw", "sgp_voc_index", "temp_c", "rh")


class Agent:
    def __init__(self, cfg, hub=None):
        self.cfg = cfg
        dev = cfg["device"]
        self.device_id = dev["id"]
        os.makedirs(dev["data_dir"], exist_ok=True)
        self.db_path = os.path.join(dev["data_dir"], "voc.sqlite")
        self.store = Storage(self.db_path)

        if hub is None:
            if cfg["sensors"].get("simulate"):
                from .sensors import SimulatedSensorHub
                hub = SimulatedSensorHub(cfg["sensors"])
            else:
                from .sensors import SensorHub
                hub = SensorHub(cfg["sensors"], cache_dir=dev["data_dir"])
        self.hub = hub

        self.model = None
        mp = dev.get("model_path")
        if mp and os.path.exists(mp):
            self.model = Model.load(mp)
            log.info("loaded model %s (%s, %d features)", self.model.version,
                     self.model.type, len(self.model.features))
        else:
            log.warning("no model at %s: running in data-logging-only mode", mp)

        self.features = FeatureExtractor()
        self.detector = Detector.from_config(self.model.detector if self.model else {})
        self.battery = (battery_mod.NoBattery() if cfg["sensors"].get("simulate")
                        else battery_mod.detect(cfg["battery"]))
        self.alarm = self._init_alarm(int(cfg["alarm"].get("gpio_pin") or 0))

        self.uploader = None
        cloud = cfg["cloud"]
        if cloud.get("enabled") and cloud.get("supabase_url") and cloud.get("device_key"):
            from .uploader import Uploader
            self.uploader = Uploader(self.db_path, cloud, self.device_id)

        self.running = True
        self.started = time.time()
        self._interval_max = {"prob": None, "score": None}
        self._low_battery_sent = False
        self._last_battery = {}

    # ------------------------------------------------------------------
    def _init_alarm(self, pin):
        if not pin:
            return None
        try:
            from gpiozero import LED
            return LED(pin)
        except Exception as e:  # noqa: BLE001
            log.warning("alarm output on GPIO%s unavailable: %s", pin, e)
            return None

    def stop(self, *_):
        self.running = False

    def run(self):
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        if self.uploader:
            self.uploader.start()
        self._event("boot", {"model": self.model.version if self.model else None,
                             "sensors": self.hub.status()})
        dev, cloud = self.cfg["device"], self.cfg["cloud"]
        period = float(dev.get("sample_interval_s", 1.0))
        next_reading = next_hb = 0.0
        next_prune = time.time() + 3600
        try:
            while self.running:
                t0 = time.time()
                sample = self.hub.read()
                prob, score, active = self.step(sample)
                if t0 >= next_reading:
                    self._queue_reading(sample, prob, score, active)
                    next_reading = t0 + float(cloud.get("reading_interval_s", 10))
                if t0 >= next_hb:
                    self._heartbeat()
                    next_hb = t0 + float(cloud.get("heartbeat_interval_s", 60))
                if t0 >= next_prune:
                    self.store.prune(float(dev.get("keep_days", 60)))
                    next_prune = t0 + 3600
                time.sleep(max(0.0, period - (time.time() - t0)))
        finally:
            self._event("shutdown", {})
            self.store.commit()
            if self.uploader:
                self.uploader.stop_event.set()

    def step(self, sample):
        feats = self.features.update(sample)
        prob = score = None
        active = self.detector.active
        if self.model is not None and feats is not None:
            prob = self.model.predict_proba(feats)
            score, active, changed = self.detector.update(sample["ts"], prob)
            if changed:
                self._event("detection_start" if active else "detection_end",
                            {"score": round(score, 3)})
                log.warning("DETECTION %s (score %.2f)", "START" if active else "END", score)
                if self.alarm:
                    self.alarm.on() if active else self.alarm.off()
        self.store.add_sample(sample, prob, score, active if self.model else None)
        for k, v in (("prob", prob), ("score", score)):
            if v is not None:
                cur = self._interval_max[k]
                self._interval_max[k] = v if cur is None else max(cur, v)
        return prob, score, active

    # ------------------------------------------------------------------
    def _queue_reading(self, s, prob, score, active):
        if not self.uploader:
            return
        payload = {"ts": s["ts"], **{k: _r(s.get(k)) for k in UPLOAD_FIELDS},
                   "score": _r(self._interval_max["score"], 3),
                   "prob": _r(self._interval_max["prob"], 3),
                   "detected": bool(active) if self.model else None}
        self._interval_max = {"prob": None, "score": None}
        self.store.enqueue("reading", payload)

    def _event(self, kind, details):
        log.info("event %s %s", kind, details)
        if self.uploader:
            self.store.enqueue("event", {"ts": time.time(), "kind": kind, "details": details})

    def _heartbeat(self):
        try:
            b = self.battery.read()
        except Exception as e:  # noqa: BLE001
            log.warning("battery read failed: %s", e)
            b = {"battery_pct": None, "charging": None, "battery_v": None}
        self._last_battery = b
        try:
            self.store.put("battery", {**b, "source": self.battery.kind, "ts": time.time()})
            self.store.put("sensor_status", {**self.hub.status(), "ts": time.time()})
        except Exception:  # noqa: BLE001
            pass
        self._check_battery(b)
        if not self.uploader:
            return
        disk = shutil.disk_usage(self.cfg["device"]["data_dir"])
        self.store.enqueue("heartbeat", {
            "ts": time.time(), **b,
            "battery_source": self.battery.kind,
            "cpu_temp_c": _cpu_temp(),
            "uptime_s": int(time.time() - self.started),
            "wifi_rssi": _wifi_rssi(),
            "free_disk_mb": int(disk.free / 1e6),
            "model_version": self.model.version if self.model else None,
            "alarm_threshold": self.detector.on_threshold if self.model else None,
            "sensors": self.hub.status(),
            "outbox": self.store.outbox_size(),
            "ip": _local_ip(),
            "upload_error": self.uploader.last_error,
        })

    def _check_battery(self, b):
        pct, charging = b.get("battery_pct"), b.get("charging")
        if pct is None:
            return
        bc = self.cfg["battery"]
        if charging:
            self._low_battery_sent = False
            return
        if pct <= float(bc.get("low_pct", 20)) and not self._low_battery_sent:
            self._low_battery_sent = True
            self._event("low_battery", {"battery_pct": pct})
        if pct <= float(bc.get("shutdown_pct", 5)) and bc.get("auto_shutdown", True):
            self._event("battery_shutdown", {"battery_pct": pct})
            log.error("battery critical (%.0f%%): shutting down cleanly to protect the SD card", pct)
            self.store.commit()
            if self.uploader:
                try:
                    self.uploader.flush(Storage(self.db_path))
                except Exception:  # noqa: BLE001
                    pass
            subprocess.call(["sudo", "/sbin/shutdown", "-h", "now"])


def _r(x, nd=2):
    return None if x is None else round(float(x), nd)


def _cpu_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read()) / 1000, 1)
    except OSError:
        return None


def _wifi_rssi():
    try:
        with open("/proc/net/wireless") as f:
            lines = f.readlines()[2:]
        if lines:
            return float(lines[0].split()[3].rstrip("."))
    except (OSError, IndexError, ValueError):
        pass
    return None


def _local_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return None
