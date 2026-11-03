"""Turns a noisy per-second probability into a stable alarm.

Raw model output flickers. The detector smooths it (exponential moving
average) and applies hysteresis: the alarm turns ON only after the smoothed
score stays above `on_threshold` for `min_on_s` seconds, and turns OFF only
after it stays below `off_threshold` for `min_off_s` seconds.

The same class is used in training to report realistic event-level results
(detections, time-to-detect, false alarms per hour).
"""

import math


class Detector:
    def __init__(self, on_threshold=0.6, off_threshold=0.4, smoothing_s=10.0,
                 min_on_s=5.0, min_off_s=30.0):
        self.on_threshold = on_threshold
        self.off_threshold = min(off_threshold, on_threshold)
        self.smoothing_s = smoothing_s
        self.min_on_s = min_on_s
        self.min_off_s = min_off_s
        self.reset()

    @classmethod
    def from_config(cls, cfg):
        keys = ("on_threshold", "off_threshold", "smoothing_s", "min_on_s", "min_off_s")
        return cls(**{k: cfg[k] for k in keys if k in cfg})

    def reset(self):
        self.score = None
        self.active = False
        self._last_t = None
        self._cross_since = None

    def update(self, t, prob):
        """Returns (smoothed_score, active, changed)."""
        if prob is None:
            return self.score, self.active, False
        if self.score is None or self._last_t is None:
            self.score = prob
        else:
            dt = max(0.0, t - self._last_t)
            a = 1.0 - math.exp(-dt / self.smoothing_s) if self.smoothing_s > 0 else 1.0
            self.score += a * (prob - self.score)
        self._last_t = t

        changed = False
        if not self.active:
            if self.score >= self.on_threshold:
                self._cross_since = t if self._cross_since is None else self._cross_since
                if t - self._cross_since >= self.min_on_s:
                    self.active, changed, self._cross_since = True, True, None
            else:
                self._cross_since = None
        else:
            if self.score < self.off_threshold:
                self._cross_since = t if self._cross_since is None else self._cross_since
                if t - self._cross_since >= self.min_off_s:
                    self.active, changed, self._cross_since = False, True, None
            else:
                self._cross_since = None
        return self.score, self.active, changed
