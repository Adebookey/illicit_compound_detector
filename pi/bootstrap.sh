#!/usr/bin/env bash
# One-line setup for a freshly flashed Pi:
#   curl -sSL https://raw.githubusercontent.com/Adebookey/illicit_compound_detector/main/pi/bootstrap.sh | sudo bash
# Downloads (or updates) the code into ~/illicit_compound_detector and installs it.
set -euo pipefail
REPO_URL=https://github.com/Adebookey/illicit_compound_detector
U="${SUDO_USER:-$(logname 2>/dev/null || echo pi)}"
DIR="$(getent passwd "$U" | cut -d: -f6)/illicit_compound_detector"

if [ "$(id -u)" -ne 0 ]; then echo "run with sudo (see the command above)"; exit 1; fi
in_tmux() {  # sudo hides $TMUX, so look for a tmux ancestor process instead
  local pid=$$
  while [ "${pid:-1}" -gt 1 ]; do
    case "$(ps -o comm= -p "$pid" 2>/dev/null)" in tmux*) return 0 ;; esac
    pid="$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')"
  done
  return 1
}
if ! in_tmux; then
  echo "TIP: run this inside 'tmux' so a dropped SSH connection can't interrupt the install."
  echo "     (Ctrl+C now, then: sudo apt install -y tmux && tmux new -s install)  - continuing in 10 s..."
  sleep 10
fi
if ! command -v git >/dev/null; then
  export DEBIAN_FRONTEND=noninteractive
  dpkg --configure -a
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
