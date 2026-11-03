"""Pure-Python model inference (no numpy / scikit-learn on the Pi).

Models are trained on a laptop with scikit-learn (training/train.py) and
exported to a small JSON file. This module evaluates that JSON.

Supported model types:
  logreg - standardised logistic regression
  gbt    - gradient-boosted decision trees (scikit-learn GradientBoostingClassifier)
"""

import json
import math
import struct


class Model:
    def __init__(self, spec):
        self.spec = spec
        self.type = spec["type"]
        self.features = list(spec["features"])
        self.fill = list(spec["fill"])  # value used when a feature is missing
        self.threshold = float(spec.get("threshold", 0.5))
        self.version = spec.get("version", "unknown")
        self.detector = spec.get("detector", {})
        if self.type == "logreg":
            self.mean = spec["mean"]
            self.scale = spec["scale"]
            self.coef = spec["coef"]
            self.intercept = float(spec["intercept"])
        elif self.type == "gbt":
            self.init = float(spec["init"])
            self.lr = float(spec["learning_rate"])
            self.trees = spec["trees"]
        else:
            raise ValueError(f"unknown model type {self.type!r}")

    @classmethod
    def load(cls, path):
        with open(path) as f:
            return cls(json.load(f))

    def vector(self, feats):
        x = []
        for name, fill in zip(self.features, self.fill):
            v = feats.get(name)
            x.append(fill if v is None else v)
        return x

    def predict_proba(self, feats):
        """Probability (0..1) that the compound is present."""
        x = self.vector(feats)
        if self.type == "logreg":
            z = self.intercept
            for xi, m, s, w in zip(x, self.mean, self.scale, self.coef):
                z += w * (xi - m) / s
        else:
            # scikit-learn compares tree splits in float32; match it exactly.
            x = [_f32(v) for v in x]
            z = self.init
            for tree in self.trees:
                z += self.lr * _tree_value(tree, x)
        return _sigmoid(z)


def _tree_value(tree, x):
    node = 0
    left, right = tree["left"], tree["right"]
    feat, thr, val = tree["feature"], tree["threshold"], tree["value"]
    while left[node] != -1:
        node = left[node] if x[feat[node]] <= thr[node] else right[node]
    return val[node]


def _f32(v):
    return struct.unpack("f", struct.pack("f", v))[0]


def _sigmoid(z):
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)
