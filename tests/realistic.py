"""Simulated data shaped like the real sessions: weak compound response, huge confounders."""
import time
from voc_detector.sensors import SimulatedSensorHub

WEAK = {"tvoc": 1.0, "eco2": 1.0, "hp0": 1.0, "hp2": 1.0, "sgp": 1.0, "idx": 1.0}
DIST_GAS = {"5 cm": 0.08, "3 cm": 0.1, "10 cm": 0.02, "30 cm": 0.0, "1 m": 0.0}


def make(store, sessions=3, t=None):
    t = t or time.time() - sessions * 4 * 3600
    for s in range(sessions):
        hub = SimulatedSensorHub(seed=50 + s)
        hub.t0 = t
        name = f"S2026110{s + 7}-1410"
        plan = [("warm", 15, 0, None, ""), ("clean", 10, 0, None, "baseline")]
        for i, d in enumerate(["10 cm", "30 cm", "5 cm", "1 m"]):
            plan += [("compound", 10, DIST_GAS[d], WEAK, f"round {i + 1}: about {d} from the device, container OPEN"),
                     ("recovery", 3, 0, None, ""), ("clean", 10, 0, None, f"after round {i + 1}")]
        if s == sessions - 1:
            plan += [("confounder:perfume", 5, 3.0, {**WEAK, "tvoc": 2.0}, "perfume"), ("recovery", 5, 0, None, ""),
                     ("clean", 10, 0, None, "after perfume"),
                     ("confounder:isopropyl_alcohol", 5, 4.0, {**WEAK, "tvoc": 2.2}, "isopropyl alcohol"),
                     ("recovery", 5, 0, None, ""), ("clean", 10, 0, None, "after alcohol")]
        for label, minutes, gas, prof, notes in plan:
            hub.gas = gas
            if prof:
                hub.profile = prof
            lid = None if label in ("warm", "recovery") else store.start_label(label, name, notes, ts=t)
            for _ in range(int(minutes * 60)):
                store.add_sample(hub.read(now=t))
                t += 1
            if lid:
                store.end_label(lid, ts=t)
        t += 3 * 3600
    store.commit()
