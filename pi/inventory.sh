#!/usr/bin/env bash
# Run ON THE PI to find out what the previous engineer left behind:
#   bash inventory.sh > inventory.txt
# It only READS things; it changes nothing. Send inventory.txt back to whoever
# is helping you (it contains no passwords, but does list file names and the
# Wi-Fi network name).

section() { printf '\n==================== %s ====================\n' "$1"; }

section "SYSTEM"
date; echo "uptime: $(uptime -p 2>/dev/null)"
cat /proc/device-tree/model 2>/dev/null; echo
grep PRETTY_NAME /etc/os-release
echo "python: $(python3 --version 2>&1)"
echo "user: $(whoami)   home dirs: $(ls /home)"
timedatectl 2>/dev/null | grep -E "Time zone|synchronized|Local time"
echo "Wi-Fi: $(iwgetid -r 2>/dev/null)   IP: $(hostname -I)"
df -h / | tail -1

section "I2C DEVICES (ENS160 = 52/53, SGP40 = 59, INA219 UPS = 40-43, PiSugar = 57/75)"
if command -v i2cdetect >/dev/null; then sudo i2cdetect -y 1; else echo "i2c-tools not installed (sudo apt install i2c-tools)"; fi

section "AUTO-START: systemd services that are not stock"
systemctl list-unit-files --type=service --state=enabled --no-pager | grep -vE \
  "^(apt|avahi|bluetooth|console|cron|dbus|dphys|e2scrub|fake-hwclock|getty|hciuart|keyboard|ModemManager|networking|NetworkManager|nfs|raspi|rpi|rsync|rsyslog|ssh|sshswitch|systemd|triggerhappy|udisks|wpa|dhcpcd|regenerate|userconfig|serial|cloud|polkit|bthelper|glamor|lightdm|plymouth|alsa|man-db)"
for f in /etc/systemd/system/*.service; do
  [ -f "$f" ] || continue
  echo "--- $f"; cat "$f"
done

section "AUTO-START: crontab / rc.local / autostart"
crontab -l 2>/dev/null; sudo crontab -l 2>/dev/null
for u in /home/*; do sudo crontab -u "$(basename "$u")" -l 2>/dev/null; done
grep -v '^#' /etc/rc.local 2>/dev/null | grep -v '^$'
ls /etc/xdg/autostart ~/.config/autostart 2>/dev/null

section "RUNNING PYTHON PROCESSES"
ps aux | grep -i python | grep -v grep

section "PYTHON SCRIPTS (excluding system/library folders)"
sudo find / -xdev -name "*.py" \
  -not -path "/usr/*" -not -path "/proc/*" -not -path "*/site-packages/*" \
  -not -path "*/dist-packages/*" -not -path "*/.cache/*" -not -path "*/venv/*" \
  -not -path "*/.venv/*" -not -path "/opt/voc-detector/*" -printf "%TY-%Tm-%Td %TH:%TM  %8s  %p\n" 2>/dev/null | sort

section "LIKELY SENSOR SCRIPTS (mention ens160 / sgp40 / dht)"
sudo grep -rlis -E "ens160|sgp40|dht22|adafruit_dht" /home /opt /root /srv 2>/dev/null \
  | grep -vE "site-packages|dist-packages|\.cache"

section "DATA FILES (csv/db/json/log/txt, newest first)"
sudo find /home /root /opt /srv /var/lib /data /mnt /media -xdev -type f \
  \( -iname "*.csv" -o -iname "*.db" -o -iname "*.sqlite*" -o -iname "*.jsonl" -o -iname "*.json" \
     -o -iname "*.log" -o -iname "*.txt" -o -iname "*.tsv" \) \
  -not -path "*/site-packages/*" -not -path "*/dist-packages/*" -not -path "*/.cache/*" \
  -not -path "*/node_modules/*" -not -path "/var/lib/dpkg/*" -not -path "/var/lib/apt/*" \
  -size +1k -printf "%TY-%Tm-%Td %TH:%TM  %10s  %p\n" 2>/dev/null | sort -r | head -60

section "FIRST LINES OF THE 5 LARGEST CSV FILES"
sudo find /home /root /opt /srv -xdev -iname "*.csv" -printf "%s %p\n" 2>/dev/null | sort -rn | head -5 \
  | while read -r _ f; do echo "--- $f ($(wc -l < "$f") lines)"; head -3 "$f"; echo "..."; tail -1 "$f"; done

section "PYTHON PACKAGES (sensor-related)"
pip3 list 2>/dev/null | grep -iE "adafruit|blinka|sensirion|smbus|gpio|ens160|sgp|dht|supabase|requests|numpy|tflite|sklearn|scikit"

section "BATTERY HAT"
systemctl is-active pisugar-server 2>/dev/null && echo "pisugar-server running" || echo "no pisugar-server"
echo "done."
