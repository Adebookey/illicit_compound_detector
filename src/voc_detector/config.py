"""Configuration loading (TOML)."""

import os

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover
    import tomli as tomllib

DEFAULT_PATH = os.environ.get("VOC_DETECTOR_CONFIG", "/etc/voc-detector/config.toml")

DEFAULTS = {
    "device": {
        "id": "detector-01",
        "data_dir": "/var/lib/voc-detector",
        "model_path": "/opt/voc-detector/models/model.json",
        "sample_interval_s": 1.0,
        "keep_days": 60,
    },
    "sensors": {"dht22_pin": 4, "simulate": False},
    "battery": {"type": "auto", "cells": 1, "low_pct": 20, "shutdown_pct": 5,
                "auto_shutdown": True},
    "alarm": {"gpio_pin": 0},
    "cloud": {"enabled": False, "supabase_url": "", "supabase_anon_key": "", "device_key": "",
              "reading_interval_s": 10, "heartbeat_interval_s": 60, "upload_interval_s": 15},
}


def load(path=None):
    path = path or DEFAULT_PATH
    cfg = {k: dict(v) for k, v in DEFAULTS.items()}
    if path and os.path.exists(path):
        with open(path, "rb") as f:
            user = tomllib.load(f)
        for section, values in user.items():
            cfg.setdefault(section, {}).update(values)
    return cfg
