#!/usr/bin/env bash
# Install / update the detector on a Raspberry Pi (Zero / Zero W / Zero 2 W)
# running a fresh Raspberry Pi OS Lite. Normally run through bootstrap.sh, which
# runs it as a background job so a closed SSH/Connect window can't interrupt it:
#   curl -sSL https://raw.githubusercontent.com/Adebookey/illicit_compound_detector/main/pi/bootstrap.sh | sudo bash
# or from a checkout:   sudo bash pi/install.sh
set -uo pipefail

if [ "$(id -u)" -ne 0 ]; then echo "run with sudo"; exit 1; fi
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP=/opt/voc-detector
DATA=/var/lib/voc-detector
CONF=/etc/voc-detector
SVC_USER="${SUDO_USER:-$(logname 2>/dev/null || echo pi)}"
step() { printf '\n\033[1m>> %s\033[0m\n' "$1"; }
apt_try() { for p in "$@"; do apt-get install -y -qq "$p" >/dev/null 2>&1 || echo "   (optional package $p not available, skipped)"; done; }

step "Enabling I2C"
raspi-config nonint do_i2c 0 || true
modprobe i2c-dev 2>/dev/null || true

export DEBIAN_FRONTEND=noninteractive
step "Preparing the package manager"
# A fresh Pi often runs automatic updates in the background for the first
# minutes after boot; wait for them instead of failing on the lock.
for i in $(seq 1 60); do
  pgrep -x 'apt|apt-get|dpkg|unattended-upgr' >/dev/null || break
  [ "$i" -eq 1 ] && echo "   waiting for background updates to finish (can take a few minutes)..."
  sleep 10
done
# Finish any install that was interrupted earlier (e.g. a dropped SSH session).
dpkg --force-confdef --force-confold --configure -a || { echo "dpkg repair failed - run 'sudo dpkg --configure -a' and read its error"; exit 1; }

step "Installing system packages (5-15 minutes on a Pi Zero)"
apt-get update -qq || { echo "apt-get update failed - is the Pi online? try: ping -c3 deb.debian.org"; exit 1; }
apt-get install -y -qq git python3-venv python3-pip python3-dev i2c-tools || {
  echo "installing system packages failed (see the error above)"; exit 1; }
apt_try python3-lgpio python3-rpi-lgpio libgpiod2 python3-libgpiod
step "Installing training libraries (pandas, scikit-learn) so you can train on the Pi"
apt_try python3-numpy python3-pandas python3-sklearn

step "Python environment in $APP/venv"
mkdir -p "$APP" "$DATA" "$CONF" "$APP/models"
[ -d "$APP/venv" ] || python3 -m venv --system-site-packages "$APP/venv"
# piwheels (on by default in Raspberry Pi OS) provides prebuilt wheels for the Pi Zero.
"$APP/venv/bin/pip" install -q --upgrade pip
"$APP/venv/bin/pip" install -q --upgrade "$REPO[pi]" || { echo "pip install failed"; exit 1; }
rm -rf "$APP/training" && cp -r "$REPO/training" "$APP/training"

if [ -f "$REPO/models/model.json" ] && [ ! -f "$APP/models/model.json" ]; then
  cp "$REPO/models/model.json" "$APP/models/model.json"
fi
[ -f "$APP/models/model.json" ] || echo "   no model yet: the device logs data only until you run 'voc-detector train'"

if [ ! -f "$CONF/config.toml" ]; then
  cp "$REPO/pi/config.example.toml" "$CONF/config.toml"
  sed -i "s/^id = \"detector-01\"/id = \"$(hostname)\"/" "$CONF/config.toml"
fi
chown -R "$SVC_USER" "$DATA" "$APP/models"
chgrp "$SVC_USER" "$CONF/config.toml"; chmod 640 "$CONF/config.toml"
usermod -aG i2c,gpio "$SVC_USER" 2>/dev/null || true

# Clean shutdown on critical battery, and restarting the service after training.
cat > /etc/sudoers.d/voc-detector <<EOF
$SVC_USER ALL=(root) NOPASSWD: /sbin/shutdown, /usr/bin/systemctl restart voc-detector
EOF
chmod 440 /etc/sudoers.d/voc-detector
rm -f /etc/sudoers.d/voc-detector-shutdown

step "Wi-Fi tools and fallback setup hotspot"
install -m 755 "$REPO/pi/detector-wifi" /usr/local/bin/detector-wifi
mkdir -p /usr/local/lib/voc-detector
install -m 755 "$REPO/pi/wifi-fallback.sh" /usr/local/lib/voc-detector/wifi-fallback.sh
[ -f "$CONF/hotspot-ssid" ] || echo "$(hostname)-setup" > "$CONF/hotspot-ssid"
[ -f "$CONF/hotspot-password" ] || (tr -dc 'a-km-z2-9' </dev/urandom | head -c 10; echo) > "$CONF/hotspot-password"
chmod 600 "$CONF/hotspot-password"
cp "$REPO/pi/wifi-fallback.service" "$REPO/pi/wifi-fallback.timer" /etc/systemd/system/

step "Raspberry Pi Connect (remote shell from anywhere)"
apt_try rpi-connect-lite
loginctl enable-linger "$SVC_USER" 2>/dev/null || true   # keep Connect running without a login

sed "s/__USER__/$SVC_USER/" "$REPO/pi/voc-detector.service" > /etc/systemd/system/voc-detector.service
ln -sf "$APP/venv/bin/voc-detector" /usr/local/bin/voc-detector
systemctl daemon-reload
systemctl enable voc-detector >/dev/null 2>&1
systemctl enable --now wifi-fallback.timer >/dev/null 2>&1 || true
systemctl restart voc-detector

step "Checking sensors"
if [ ! -e /dev/i2c-1 ]; then
  echo "   I2C was just enabled: REBOOT once (sudo reboot), then run: voc-detector check"
else
  i2cdetect -y 1 | sed 's/^/   /'
  echo "   expect 52 or 53 (ENS160) and 59 (SGP40)"
  echo "   waiting 60 s for the first readings (the DHT22 pin is auto-detected on first start)..."
  sleep 60
  sudo -u "$SVC_USER" /usr/local/bin/voc-detector check || true
fi

cat <<EOF

WRITE THIS DOWN - setup hotspot (opens by itself if the Pi can't reach any known Wi-Fi):
  Wi-Fi name: $(cat "$CONF/hotspot-ssid")    password: $(cat "$CONF/hotspot-password")
  then: ssh $SVC_USER@10.42.0.1

Remote access from anywhere (one time):  rpi-connect on && rpi-connect signin

Installed. Next steps:
  voc-detector check                      # are all sensors working?
  voc-detector guided                     # record a labelled session (repeat 3+ times)
  voc-detector train                      # train and install the model on this Pi
  journalctl -u voc-detector -f           # live log (Ctrl+C to leave)
EOF
