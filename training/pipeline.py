"""One command: readings + labels -> trained model + report.

    # data exported from the device (voc-detector export / voc-detector train)
    python training/pipeline.py export.csv export_labels.csv --device-export -o models/model.json

    # any other log files (e.g. the old SD card): times you wrote down, in your timezone
    python training/pipeline.py old_log.csv my_labels.csv --tz Africa/Lagos -o models/model.json

It runs import_data.py (skipped for device exports), label.py and train.py
in turn, keeping the intermediate files next to the output.
"""

import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def step(name, *args):
    print(f"\n=== {name} ===", flush=True)
    rc = subprocess.call([sys.executable, os.path.join(HERE, name), *args])
    if rc != 0:
        sys.exit(f"{name} failed")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("readings")
    p.add_argument("labels")
    p.add_argument("-o", "--out", default="models/model.json")
    p.add_argument("--device-export", action="store_true",
                   help="readings come from the device's own log (already in the standard format)")
    p.add_argument("--tz", default="UTC", help="timezone of written-down times / raw log times")
    p.add_argument("--clock-offset", type=float, default=0.0)
    p.add_argument("--model", default="auto", choices=["auto", "logreg", "gbt"])
    p.add_argument("--max-false-alarms", type=float, default=0.25)
    a = p.parse_args(argv)

    work = os.path.dirname(os.path.abspath(a.out))
    os.makedirs(work, exist_ok=True)
    readings = a.readings
    if not a.device_export:
        readings = os.path.join(work, "imported.csv")
        step("import_data.py", a.readings, "--tz", a.tz, "-o", readings)
    labelled = os.path.join(work, "labelled.csv")
    step("label.py", readings, a.labels, "--tz", a.tz, "--clock-offset", str(a.clock_offset),
         "-o", labelled)
    step("train.py", labelled, "-o", a.out, "--model", a.model,
         "--max-false-alarms", str(a.max_false_alarms))


if __name__ == "__main__":
    main()
