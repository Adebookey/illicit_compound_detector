"""Streaming feature extraction.

This one class is used BOTH on the Pi (live, sample by sample) and during
training (replaying logged CSVs sample by sample). Using the same code in
both places is what guarantees the model sees identical features in the
field as it did when it was trained.

Pure Python on purpose: it runs on a Raspberry Pi Zero without numpy.

Why these features?
Metal-oxide gas sensors (ENS160, SGP40) drift with temperature, humidity,
age and time of day, so absolute readings are unreliable. What carries the
signal is how the reading moves *relative to its own recent clean-air
baseline* and how fast it moves. For each gas channel we compute:

  rel     - deviation from a slowly-adapting clean-air baseline
  d30/d120 - change over the last 30 s / 120 s (rise speed)
  sd60    - noise / fluctuation over the last 60 s

The baseline follows the reading quickly when the air gets cleaner and
very slowly when it gets dirtier, so a sustained source does not get
absorbed into the baseline within minutes.
"""

import math
from collections import deque

from .schema import GAS_CHANNELS, LOG_CHANNELS

# Typical magnitude of each channel, used to normalise deviations so all
# channels are on comparable scales.
CHANNEL_SCALE = {
    "ens_tvoc": 50.0,
    "ens_eco2": 400.0,
    "ens_aqi": 1.0,
    "ens_hp0_rs": 1.0,  # log scale already
    "ens_hp2_rs": 1.0,
    "sgp_raw": 1000.0,
    "sgp_voc_index": 100.0,
}

# Channels whose absolute level is meaningful on its own (already
# self-normalising on the sensor).
LEVEL_CHANNELS = ("sgp_voc_index", "ens_aqi")


class _Channel:
    __slots__ = ("hist", "baseline", "last_t")

    def __init__(self):
        self.hist = deque()  # (t, value)
        self.baseline = None
        self.last_t = None


class FeatureExtractor:
    def __init__(self, warmup_s=180.0, tau_fast_s=120.0, tau_slow_s=3600.0,
                 gap_reset_s=90.0, history_s=130.0):
        self.warmup_s = float(warmup_s)
        self.tau_fast_s = float(tau_fast_s)
        self.tau_slow_s = float(tau_slow_s)
        self.gap_reset_s = float(gap_reset_s)
        self.history_s = float(history_s)
        self.reset()

    # ------------------------------------------------------------------
    @staticmethod
    def feature_names():
        names = []
        for ch in GAS_CHANNELS:
            names += [f"{ch}_rel", f"{ch}_d30", f"{ch}_d120", f"{ch}_sd60"]
        names += [f"{ch}_level" for ch in LEVEL_CHANNELS]
        names += ["temp_c", "rh", "rh_d60"]
        return names

    def reset(self):
        self.start_t = None
        self.last_t = None
        self.channels = {ch: _Channel() for ch in GAS_CHANNELS}
        self.rh_hist = deque()

    @property
    def warm(self):
        return (self.start_t is not None and self.last_t is not None
                and self.last_t - self.start_t >= self.warmup_s)

    def state(self):
        """Serializable baselines (so a restart does not lose them)."""
        return {ch: c.baseline for ch, c in self.channels.items()}

    def load_state(self, state):
        for ch, b in (state or {}).items():
            if ch in self.channels and b is not None:
                self.channels[ch].baseline = float(b)

    # ------------------------------------------------------------------
    def update(self, sample):
        """Feed one reading (dict with 'ts' + channel values).

        Returns a dict of features (values may be None when a channel is
        missing), or None while the extractor is still warming up.
        """
        t = float(sample["ts"])
        if self.last_t is not None and (t - self.last_t > self.gap_reset_s or t < self.last_t):
            # Data gap or clock jump: start over (baselines re-learn too).
            self.reset()
        if self.start_t is None:
            self.start_t = t
        self.last_t = t

        feats = {}
        for ch, direction in GAS_CHANNELS.items():
            feats.update(self._update_channel(ch, direction, sample.get(ch), t))

        for ch in LEVEL_CHANNELS:
            v = _num(sample.get(ch))
            feats[f"{ch}_level"] = v

        temp = _num(sample.get("temp_c"))
        rh = _num(sample.get("rh"))
        feats["temp_c"] = temp
        feats["rh"] = rh
        feats["rh_d60"] = None
        if rh is not None:
            self.rh_hist.append((t, rh))
            _trim(self.rh_hist, t - self.history_s)
            past = _value_ago(self.rh_hist, t, 60.0)
            if past is not None:
                feats["rh_d60"] = rh - past

        return feats if self.warm else None

    def _update_channel(self, ch, direction, raw, t):
        c = self.channels[ch]
        out = {f"{ch}_rel": None, f"{ch}_d30": None, f"{ch}_d120": None, f"{ch}_sd60": None}
        v = _num(raw)
        if v is None:
            return out
        if ch in LOG_CHANNELS:
            if v <= 0:
                return out
            v = math.log(v)
        scale = CHANNEL_SCALE[ch]

        # Asymmetric baseline update.
        if c.baseline is None:
            c.baseline = v
        else:
            dt = max(0.0, t - (c.last_t if c.last_t is not None else t))
            dirtier = (v - c.baseline) * direction > 0
            tau = self.tau_slow_s if dirtier else self.tau_fast_s
            alpha = 1.0 - math.exp(-dt / tau) if dt > 0 else 0.0
            c.baseline += alpha * (v - c.baseline)
        c.last_t = t

        dev = (v - c.baseline) * direction
        if ch in LOG_CHANNELS:
            out[f"{ch}_rel"] = dev
        else:
            out[f"{ch}_rel"] = dev / (abs(c.baseline) + scale)

        c.hist.append((t, v))
        _trim(c.hist, t - self.history_s)
        for w in (30.0, 120.0):
            past = _value_ago(c.hist, t, w)
            if past is not None:
                out[f"{ch}_d{int(w)}"] = (v - past) * direction / scale
        vals = [x for (tt, x) in c.hist if tt >= t - 60.0]
        if len(vals) >= 5:
            m = sum(vals) / len(vals)
            out[f"{ch}_sd60"] = math.sqrt(sum((x - m) ** 2 for x in vals) / len(vals)) / scale
        return out


def _num(x):
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _trim(hist, t_min):
    while hist and hist[0][0] < t_min:
        hist.popleft()


def _value_ago(hist, t, window):
    """Value from roughly `window` seconds ago, or None if history is too short."""
    target = t - window
    if not hist or hist[0][0] > target + window * 0.25:
        return None
    best = None
    for tt, x in hist:
        if tt <= target:
            best = x
        else:
            if best is None:
                best = x
            break
    return best
