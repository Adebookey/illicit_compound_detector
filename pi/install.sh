#!/usr/bin/env bash
# Install / update the detector on a Raspberry Pi (Zero / Zero W / Zero 2 W).
# Run from the repo folder on the Pi:   sudo bash pi/install.sh
#
# It does NOT delete or stop the previous engineer's code. If their script
# is also reading the sensors, stop it first (see docs/01-accessing-the-pi.md).
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then echo "run with sudo"; exit 1; fi
REPO="$(cd "$(dirname "$0")/.." && pwd)"
APP=/opt/voc-detector
DATA=/var/lib/voc-detector
CONF=/etc/voc-detector
SVC_USER="${SUDO_USER:-pi}"

echo ">> enabling I2C"
raspi-config nonint do_i2c 0 || true

echo ">> system packages"
apt-get update -qq
apt-get install -y -qq python3-venv python3-pip i2c-tools libgpiod2 python3-libgpiod >/dev/null || \
  apt-get install -y -qq python3-venv python3-pip i2c-tools libgpiod2 >/dev/null

echo ">> python environment in $APP/venv"
mkdir -p "$APP" "$DATA" "$CONF" "$APP/models"
[ -d "$APP/venv" ] || python3 -m venv --system-site-packages "$APP/venv"
# piwheels (enabled by default on Raspberry Pi OS) provides prebuilt ARMv6 wheels.
"$APP/venv/bin/pip" install -q --upgrade pip
"$APP/venv/bin/pip" install -q "$REPO[pi]"

if [ -f "$REPO/models/model.json" ]; then
  cp "$REPO/models/model.json" "$APP/models/model.json"
  echo ">> installed model $(python3 -c "import json;print(json.load(open('$APP/models/model.json'))['version'])")"
else
  echo ">> no models/model.json yet: the device will log data only (no detection)"
fi

if [ ! -f "$CONF/config.toml" ]; then
  cp "$REPO/pi/config.example.toml" "$CONF/config.toml"
  echo ">> created $CONF/config.toml  <-- EDIT THIS (device id, cloud keys)"
fi
chown -R "$SVC_USER" "$DATA"
chmod 640 "$CONF/config.toml"; chgrp "$SVC_USER" "$CONF/config.toml"
usermod -aG i2c,gpio "$SVC_USER" || true

# Allow the service user to shut down cleanly on critical battery.
echo "$SVC_USER ALL=(root) NOPASSWD: /sbin/shutdown" > /etc/sudoers.d/voc-detector-shutdown
chmod 440 /etc/sudoers.d/voc-detector-shutdown

sed "s/__USER__/$SVC_USER/" "$REPO/pi/voc-detector.service" > /etc/systemd/system/voc-detector.service
ln -sf "$APP/venv/bin/voc-detector" /usr/local/bin/voc-detector
systemctl daemon-reload
systemctl enable voc-detector >/dev/null
systemctl restart voc-detector
sleep 3
systemctl --no-pager --lines=15 status voc-detector || true
echo
echo "Installed. Useful commands:"
echo "  journalctl -u voc-detector -f        # live log"
echo "  voc-detector status                  # latest reading"
echo "  voc-detector collect compound -m 10  # record labelled training data"
