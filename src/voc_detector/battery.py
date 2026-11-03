"""Battery / UPS HAT readers with auto-detection.

Supported:
  pisugar  - PiSugar 2 / 3 (via the pisugar-server daemon on TCP 8423)
  ina219   - Waveshare UPS HAT (C) / UPS HAT for Pi Zero and similar boards
             using an INA219 power monitor at I2C 0x43 / 0x42 / 0x40
  none     - no HAT (mains or plain power bank); dashboard relies on
             heartbeats to detect the device going down

Set `battery.type` in config.toml to force one; "auto" tries each in turn.
"""

import logging
import socket

log = logging.getLogger(__name__)


class NoBattery:
    kind = "none"

    def read(self):
        return {"battery_pct": None, "charging": None, "battery_v": None}


class PiSugar:
    kind = "pisugar"

    def __init__(self, host="127.0.0.1", port=8423):
        self.addr = (host, port)
        if self._cmd("get battery") is None:
            raise OSError("pisugar-server not reachable")

    def _cmd(self, cmd):
        try:
            with socket.create_connection(self.addr, timeout=2) as s:
                s.sendall((cmd + "\n").encode())
                data = s.recv(256).decode(errors="replace").strip()
            # replies look like "battery: 87.5"
            return data.split(":", 1)[1].strip() if ":" in data else None
        except OSError:
            return None

    def read(self):
        pct = _float(self._cmd("get battery"))
        volts = _float(self._cmd("get battery_v"))
        charging = self._cmd("get battery_power_plugged")
        if charging is None:
            charging = self._cmd("get battery_charging")
        return {
            "battery_pct": pct,
            "battery_v": volts,
            "charging": None if charging is None else charging.lower() == "true",
        }


class INA219Battery:
    """Reads bus voltage/current from an INA219 and maps voltage to %.

    The voltage curve below is for a single 1S Li-ion/LiPo cell (3.0-4.2 V),
    which is what Pi Zero UPS HATs use. If your HAT uses 2 cells in series
    (e.g. Waveshare UPS HAT for full-size Pis, 6.0-8.4 V) set
    battery.cells = 2 in the config.
    """

    kind = "ina219"
    _CURVE = [(3.00, 0), (3.45, 5), (3.60, 15), (3.70, 30), (3.78, 45),
              (3.85, 60), (3.95, 75), (4.05, 88), (4.15, 97), (4.20, 100)]

    def __init__(self, address=None, cells=1, bus=1):
        from smbus2 import SMBus
        self.bus = SMBus(bus)
        self.cells = cells
        candidates = [address] if address else [0x43, 0x42, 0x40, 0x41]
        self.addr = None
        for a in candidates:
            try:
                self.bus.read_word_data(a, 0x00)
                self.addr = a
                break
            except OSError:
                continue
        if self.addr is None:
            raise OSError("no INA219 found")
        # 32V range, gain /8 (320mV), 12-bit, continuous; calibration for 0.1 ohm shunt
        self._write(0x05, 4096)
        self._write(0x00, 0x399F)

    def _write(self, reg, val):
        self.bus.write_i2c_block_data(self.addr, reg, [(val >> 8) & 0xFF, val & 0xFF])

    def _read(self, reg):
        d = self.bus.read_i2c_block_data(self.addr, reg, 2)
        return (d[0] << 8) | d[1]

    def read(self):
        bus_v = (self._read(0x02) >> 3) * 0.004
        raw_shunt = self._read(0x01)
        if raw_shunt > 32767:
            raw_shunt -= 65536
        shunt_mv = raw_shunt * 0.01
        current_ma = shunt_mv / 0.1  # 0.1 ohm shunt
        cell_v = bus_v / max(1, self.cells)
        return {
            "battery_pct": round(voltage_to_pct(cell_v, self._CURVE), 1),
            "battery_v": round(bus_v, 3),
            # On Waveshare HATs current is positive when charging.
            "charging": current_ma > 20,
        }


def voltage_to_pct(v, curve):
    if v <= curve[0][0]:
        return 0.0
    for (v0, p0), (v1, p1) in zip(curve, curve[1:]):
        if v <= v1:
            return p0 + (p1 - p0) * (v - v0) / (v1 - v0)
    return 100.0


def detect(cfg):
    kind = (cfg.get("type") or "auto").lower()
    attempts = {
        "pisugar": lambda: PiSugar(cfg.get("pisugar_host", "127.0.0.1"),
                                   int(cfg.get("pisugar_port", 8423))),
        "ina219": lambda: INA219Battery(cfg.get("ina219_address"), int(cfg.get("cells", 1))),
    }
    order = list(attempts) if kind == "auto" else [kind]
    for k in order:
        if k == "none":
            break
        try:
            b = attempts[k]()
            log.info("battery monitor: %s", k)
            return b
        except Exception as e:  # noqa: BLE001
            log.info("battery monitor %s not available: %s", k, e)
    log.warning("no battery monitor found; battery level will not be reported")
    return NoBattery()


def _float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None
