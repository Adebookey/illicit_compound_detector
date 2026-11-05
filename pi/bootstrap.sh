#!/usr/bin/env bash
# One-line setup for a freshly flashed Pi:
#   curl -sSL https://raw.githubusercontent.com/Adebookey/illicit_compound_detector/main/pi/bootstrap.sh | sudo bash
#
# The install runs as a background systemd job ("detector-install"), so it keeps
# going even if your SSH / Raspberry Pi Connect window closes (installing system
# packages can restart Raspberry Pi Connect, which ends its shell). This window
# just shows the progress; you can close it at any time and check later with:
#   sudo journalctl -u detector-install -f
set -euo pipefail
REPO_URL=https://github.com/Adebookey/illicit_compound_detector
RAW_URL=https://raw.githubusercontent.com/Adebookey/illicit_compound_detector/main/pi/bootstrap.sh
UNIT=detector-install

if [ "$(id -u)" -ne 0 ]; then echo "run with sudo (see the command above)"; exit 1; fi
U="${SUDO_USER:-$(logname 2>/dev/null || echo pi)}"

# ---------------------------------------------------------------- launcher
if [ -z "${DETECTOR_IN_BACKGROUND:-}" ]; then
  if systemctl is-active --quiet "$UNIT"; then
    echo "An install is already running. Showing its progress (Ctrl+C stops watching, not the install):"
  else
    curl -fsSL "$RAW_URL" -o /root/detector-bootstrap.sh
    systemctl reset-failed "$UNIT" >/dev/null 2>&1 || true
    systemd-run --quiet --unit="$UNIT" --setenv=SUDO_USER="$U" --setenv=DETECTOR_IN_BACKGROUND=1 \
      bash /root/detector-bootstrap.sh
    echo "Install started in the background (20-40 minutes)."
    echo "If this window closes, it keeps going. Reconnect and run:  sudo journalctl -u $UNIT -f"
    echo
  fi
  journalctl -u "$UNIT" -f -n 100 -o cat &
  follower=$!
  trap 'kill $follower 2>/dev/null; exit 0' INT TERM
  sleep 3
  while systemctl is-active --quiet "$UNIT"; do sleep 5; done
  sleep 2; kill "$follower" 2>/dev/null || true
  if [ "$(systemctl show -p Result --value "$UNIT")" = "success" ]; then
    echo; echo "Install finished OK."
  else
    echo; echo "Install FAILED - see the messages above (or: sudo journalctl -u $UNIT -n 80)."
  fi
  exit 0
fi

# ---------------------------------------------------------------- the actual install (background)
export DEBIAN_FRONTEND=noninteractive
DIR="$(getent passwd "$U" | cut -d: -f6)/illicit_compound_detector"
echo ">> repairing any interrupted package install"
dpkg --force-confdef --force-confold --configure -a
if ! command -v git >/dev/null; then
  apt-get update -qq && apt-get install -y -qq git
fi
if [ -d "$DIR/.git" ]; then
  echo ">> updating $DIR"
  sudo -u "$U" git -C "$DIR" pull --ff-only
else
  echo ">> downloading to $DIR"
  sudo -u "$U" git clone --depth 1 "$REPO_URL" "$DIR"
fi
SUDO_USER="$U" bash "$DIR/pi/install.sh"
