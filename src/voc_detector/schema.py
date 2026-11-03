"""Canonical names for sensor readings.

Every part of the system (device logger, importer, training, dashboard
upload) uses these names, so data from the engineer's old logs, from the
new logger and from the cloud all line up.
"""

# Gas channels. "direction" says which way the value moves when VOCs are
# present: +1 means it goes UP with gas (concentrations / indices), -1 means
# it goes DOWN (raw sensor resistances / SGP40 raw ticks).
GAS_CHANNELS = {
    "ens_tvoc": +1,       # ENS160 total VOC, ppb
    "ens_eco2": +1,       # ENS160 equivalent CO2, ppm
    "ens_aqi": +1,        # ENS160 UBA air quality index, 1..5
    "ens_hp0_rs": -1,     # ENS160 hot-plate 0 raw resistance, ohm (optional)
    "ens_hp2_rs": -1,     # ENS160 hot-plate 2 raw resistance, ohm (optional)
    "sgp_raw": -1,        # SGP40 SRAW ticks
    "sgp_voc_index": +1,  # SGP40 Sensirion VOC index, 0..500 (100 = average)
}

# Channels that are resistances / ticks and behave better on a log scale.
LOG_CHANNELS = {"ens_hp0_rs", "ens_hp2_rs"}

ENV_CHANNELS = ["temp_c", "rh"]

ALL_CHANNELS = list(GAS_CHANNELS) + ENV_CHANNELS

# Label values used in training.
POSITIVE_LABEL = "compound"
CLEAN_LABEL = "clean"
CONFOUNDER_PREFIX = "confounder"  # e.g. "confounder:coffee"


def is_positive(label):
    return str(label).strip().lower() == POSITIVE_LABEL
