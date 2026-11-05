"""SensorHub.read() against fakes that behave like the Adafruit drivers."""
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from voc_detector.sensors import SensorHub  # noqa: E402


class FakeENS160:
    """Like adafruit_ens160: read_all_sensors() only returns a buffer that is
    refreshed as a side effect of reading new_data_available."""
    data_validity = 0

    def __init__(self):
        self._buf = {"AQI": None, "TVOC": None, "eCO2": None, "Resistances": [None] * 4}
        self.temperature_compensation = 25
        self.humidity_compensation = 50

    @property
    def new_data_available(self):
        self._buf.update(AQI=2, TVOC=123, eCO2=650, Resistances=[1000, 2000, 3000, 4000])
        return True

    def read_all_sensors(self):
        return self._buf


class FakeSGP40:
    def __init__(self):
        self.measurements = 0

    def measure_raw(self, temperature=25, relative_humidity=50):
        self.measurements += 1
        return 30000


class FakeVOCAlgorithm:
    def vocalgorithm_init(self):
        pass

    def vocalgorithm_process(self, raw):
        return 100


def test_read_uses_driver_buffers_and_one_sgp_measurement(monkeypatch):
    mod = types.ModuleType("adafruit_sgp40.voc_algorithm")
    mod.VOCAlgorithm = FakeVOCAlgorithm
    monkeypatch.setitem(sys.modules, "adafruit_sgp40", types.ModuleType("adafruit_sgp40"))
    monkeypatch.setitem(sys.modules, "adafruit_sgp40.voc_algorithm", mod)

    hub = SensorHub.__new__(SensorHub)  # skip hardware init
    hub.cfg, hub.cache_dir = {}, None
    hub.ens, hub.sgp, hub.dht = FakeENS160(), FakeSGP40(), None
    hub._last_env, hub._last_dht_t = (24.0, 55.0), 0.0
    hub._errors = {"ens160": 0, "sgp40": 0, "dht22": 0}
    hub._ens_comp, hub._voc_algo, hub.ens_validity, hub.timings = None, None, None, {}

    s = hub.read()
    assert (s["ens_tvoc"], s["ens_eco2"], s["ens_aqi"]) == (123, 650, 2)
    assert (s["ens_hp0_rs"], s["ens_hp2_rs"]) == (1000, 3000)
    assert (s["sgp_raw"], s["sgp_voc_index"]) == (30000, 100)
    assert hub.sgp.measurements == 1          # not two (each costs ~0.5 s)
    assert hub.ens_validity == "normal"
    assert set(hub.status()["read_seconds"]) == {"dht22", "ens160", "sgp40"}
