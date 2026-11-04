#!/usr/bin/env bash
# One-line setup for a freshly flashed Pi:
#   curl -sSL https://raw.githubusercontent.com/Adebookey/illicit_compound_detector/main/pi/bootstrap.sh | sudo bash
# Downloads (or updates) the code into ~/illicit_compound_detector and installs it.
set -euo pipefail
REPO_URL=https://github.com/Adebookey/illicit_compound_detector
U="${SUDO_USER:-$(logname 2>/dev/null || echo pi)}"
DIR="$(getent passwd "$U" | cut -d: -f6)/illicit_compound_detector"

if [ "$(id -u)" -ne 0 ]; then echo "run with sudo (see the command above)"; exit 1; fi
command -v git >/dev/null || { apt-get update -qq && apt-get install -y -qq git >/dev/null; }
if [ -d "$DIR/.git" ]; then
  echo ">> updating $DIR"
  sudo -u "$U" git -C "$DIR" pull --ff-only
else
  echo ">> downloading to $DIR"
  sudo -u "$U" git clone --depth 1 "$REPO_URL" "$DIR"
fi
SUDO_USER="$U" bash "$DIR/pi/install.sh"
