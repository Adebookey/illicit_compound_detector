"""Sensor drivers: ENS160, SGP40, DHT22 (plus a simulator for testing).

Uses Adafruit's CircuitPython libraries (via Blinka) which are well
maintained and work on Raspberry Pi OS. Every sensor is optional: if one
fails to initialise or a read fails, its values come back as None and the
rest of the system carries on (the model fills in missing values).
"""

import logging
import math
import random
import time

log = logging.getLogger(__name__)


class SensorHub:
    """Reads all sensors and returns one canonical sample dict."""

    def __init__(self, cfg, cache_dir=None):
        self.cfg = cfg
        self.cache_dir = cache_dir
        self.ens = self.sgp = self.dht = None
        self._last_env = (None, None)
        self._last_dht_t = 0.0
        self._errors = {"ens160": 0, "sgp40": 0, "dht22": 0}
        self._init()

    # ------------------------------------------------------------------
    def _init(self):
        import board  # noqa: imported lazily so the package works off-Pi
        import busio

        i2c = busio.I2C(board.SCL, board.SDA, frequency=100_000)

        try:
            import adafruit_ens160
            addr = self.cfg.get("ens160_address")
            addrs = [addr] if addr else [0x53, 0x52]
            for a in addrs:
                try:
                    self.ens = adafruit_ens160.ENS160(i2c, address=a)
                    log.info("ENS160 found at 0x%02x", a)
                    break
                except (ValueError, OSError, RuntimeError):
                    continue
            if self.ens is None:
                log.warning("ENS160 not found on I2C")
        except ImportError:
            log.warning("adafruit_ens160 not installed")

        try:
            import adafruit_sgp40
            self.sgp = adafruit_sgp40.SGP40(i2c)
            log.info("SGP40 found")
        except ImportError:
            log.warning("adafruit_sgp40 not installed")
        except (ValueError, OSError, RuntimeError) as e:
            log.warning("SGP40 not found: %s", e)

        try:
            import adafruit_dht  # noqa: F401
            self.dht = self._find_dht(board)
        except ImportError:
            log.warning("adafruit_dht not installed")

    # Pins tried when dht22_pin = "auto" (BCM numbers; 2/3 are I2C, skipped).
    DHT_CANDIDATES = (4, 17, 27, 22, 18, 23, 24, 25, 5, 6, 12, 13, 16, 26, 19, 20, 21)

    def _find_dht(self, board):
        """Open the DHT22. With dht22_pin = "auto", probe the usual GPIO pins
        until one answers, and remember the result so later starts are fast."""
        import os
        setting = str(self.cfg.get("dht22_pin", "auto")).strip().lower()
        cache = os.path.join(self.cache_dir, "dht22_pin") if self.cache_dir else None
        if setting != "auto":
            candidates = [int(setting)]
        else:
            candidates = list(self.DHT_CANDIDATES)
            try:
                with open(cache) as f:
                    cached = int(f.read().strip())
                candidates.remove(cached)
                candidates.insert(0, cached)
            except (OSError, TypeError, ValueError):
                pass
        for pin in candidates:
            dev = self._open_dht(board, pin)
            if dev is None:
                continue
            if setting != "auto":
                log.info("DHT22 configured on GPIO%d", pin)
                return dev
            if self._dht_answers(dev):
                log.info("DHT22 found on GPIO%d", pin)
                if cache:
                    try:
                        with open(cache, "w") as f:
                            f.write(str(pin))
                    except OSError:
                        pass
                return dev
            try:
                dev.exit()
            except Exception:  # noqa: BLE001
                pass
        log.warning("DHT22 not found on any GPIO pin (temperature/humidity will be missing)")
        return None

    @staticmethod
    def _open_dht(board, pin):
        import adafruit_dht
        for pulseio in (True, False):
            try:
                return adafruit_dht.DHT22(getattr(board, f"D{pin}"), use_pulseio=pulseio)
            except Exception:  # noqa: BLE001 - library raises many types
                continue
        return None

    @staticmethod
    def _dht_answers(dev, tries=3):
        for _ in range(tries):
            try:
                if dev.temperature is not None and dev.humidity is not None:
                    return True
            except RuntimeError:
                pass  # checksum / timing errors are normal, retry
            except Exception:  # noqa: BLE001
                return False
            time.sleep(2.2)
        return False

    def status(self):
        return {
            "ens160": self.ens is not None,
            "sgp40": self.sgp is not None,
            "dht22": self.dht is not None,
            "errors": dict(self._errors),
        }

    # ------------------------------------------------------------------
    def read(self):
        now = time.time()
        temp, rh = self._read_dht(now)
        s = {"ts": now, "temp_c": temp, "rh": rh}
        comp_t = temp if temp is not None else 25.0
        comp_rh = rh if rh is not None else 50.0

        for k in ("ens_tvoc", "ens_eco2", "ens_aqi", "ens_hp0_rs", "ens_hp2_rs",
                  "sgp_raw", "sgp_voc_index"):
            s[k] = None

        if self.ens is not None:
            try:
                self.ens.temperature_compensation = comp_t
                self.ens.humidity_compensation = comp_rh
                data = self.ens.read_all_sensors()
                s["ens_aqi"] = data.get("AQI")
                s["ens_tvoc"] = data.get("TVOC")
                s["ens_eco2"] = data.get("eCO2")
                res = data.get("Resistances") or []
                if len(res) >= 3:
                    s["ens_hp0_rs"] = res[0]
                    s["ens_hp2_rs"] = res[2]
            except Exception as e:  # noqa: BLE001
                self._errors["ens160"] += 1
                log.debug("ENS160 read failed: %s", e)

        if self.sgp is not None:
            try:
                # measure_index runs Sensirion's VOC algorithm and must be
                # called about once per second to stay calibrated.
                s["sgp_voc_index"] = self.sgp.measure_index(temperature=comp_t,
                                                             relative_humidity=comp_rh)
                s["sgp_raw"] = self.sgp.raw
            except Exception as e:  # noqa: BLE001
                self._errors["sgp40"] += 1
                log.debug("SGP40 read failed: %s", e)
        return s

    def _read_dht(self, now):
        # The DHT22 must not be polled faster than every ~2 s, and it
        # frequently returns checksum errors; keep the last good value.
        if self.dht is None:
            return self._last_env
        if now - self._last_dht_t >= 2.5:
            self._last_dht_t = now
            try:
                t, h = self.dht.temperature, self.dht.humidity
                if t is not None and h is not None and -20 < t < 70 and 0 <= h <= 100:
                    self._last_env = (float(t), float(h))
            except RuntimeError:
                pass  # normal for DHT sensors
            except Exception as e:  # noqa: BLE001
                self._errors["dht22"] += 1
                log.debug("DHT22 read failed: %s", e)
        return self._last_env


DEFAULT_PROFILE = {"tvoc": 1.0, "eco2": 1.0, "hp0": 1.0, "hp2": 1.0, "sgp": 1.0, "idx": 1.0}


class SimulatedSensorHub:
    """Fake sensors for testing without hardware.

    `gas` (0..1) can be changed at runtime to simulate a source nearby.
    """

    def __init__(self, cfg=None, seed=None):
        self.rng = random.Random(seed)
        self.gas = 0.0
        # How strongly each channel reacts. Different sources (the target
        # compound vs. e.g. alcohol or cooking) have different "fingerprints";
        # tests use this to check the model can tell them apart.
        self.profile = dict(DEFAULT_PROFILE)
        self._level = 0.0
        self._voc_index = 100.0
        self.t0 = time.time()

    def status(self):
        return {"ens160": True, "sgp40": True, "dht22": True, "errors": {}, "simulated": True}

    def read(self, now=None):
        now = time.time() if now is None else now
        r = self.rng
        # sensor responds with a lag
        self._level += 0.08 * (self.gas - self._level)
        lvl = self._level
        w = self.profile
        drift = math.sin((now - self.t0) / 3600.0) * 0.05
        temp = 24 + 1.5 * math.sin((now - self.t0) / 5000.0) + r.gauss(0, 0.1)
        rh = 45 + 5 * math.sin((now - self.t0) / 4000.0) + r.gauss(0, 0.3)
        self._voc_index += 0.02 * (100 + 300 * w["idx"] * lvl - self._voc_index)
        return {
            "ts": now,
            "ens_tvoc": max(0.0, 40 + 900 * w["tvoc"] * lvl + 20 * drift + r.gauss(0, 5)),
            "ens_eco2": max(400.0, 420 + 700 * w["eco2"] * lvl + r.gauss(0, 10)),
            "ens_aqi": 1 + min(4, int(w["tvoc"] * lvl * 5)),
            "ens_hp0_rs": 120000 * math.exp(-1.2 * w["hp0"] * lvl + drift) * (1 + r.gauss(0, 0.005)),
            "ens_hp2_rs": 40000 * math.exp(-0.8 * w["hp2"] * lvl + drift) * (1 + r.gauss(0, 0.005)),
            "sgp_raw": 30000 - 4000 * w["sgp"] * lvl + 300 * drift + r.gauss(0, 15),
            "sgp_voc_index": self._voc_index + r.gauss(0, 1),
            "temp_c": temp,
            "rh": rh,
        }
