#!/usr/bin/env bash
# Fallback setup hotspot. Runs every 2 minutes (systemd timer).
#  - Connected to a normal Wi-Fi: do nothing.
#  - Not connected: open the hotspot so someone on site can connect with a
#    phone/laptop, SSH to 10.42.0.1 and add the right Wi-Fi.
#  - Hotspot open for 10+ minutes and nobody logged in through it: close it for
#    a minute so the Pi can retry known networks (e.g. the router came back).
set -uo pipefail
CON=detector-setup
SSID="$(cat /etc/voc-detector/hotspot-ssid 2>/dev/null || echo detector-setup)"
PASS="$(cat /etc/voc-detector/hotspot-password 2>/dev/null || true)"
STATE=/run/detector-hotspot-since
log() { logger -t wifi-fallback "$*"; }

[ -n "$PASS" ] || exit 0
active="$(nmcli -t -f NAME,DEVICE connection show --active | awk -F: '$2=="wlan0"{print $1}')"

if [ -n "$active" ] && [ "$active" != "$CON" ]; then
  rm -f "$STATE"; exit 0                     # on a normal Wi-Fi network
fi

if [ "$active" = "$CON" ]; then
  since="$(cat "$STATE" 2>/dev/null || date +%s)"
  users="$(ss -Htn state established '( sport = :22 )' | wc -l)"
  if [ $(( $(date +%s) - since )) -ge 600 ] && [ "$users" -eq 0 ]; then
    log "hotspot idle for 10 min, retrying known networks"
    nmcli connection down "$CON" >/dev/null 2>&1
    rm -f "$STATE"
    sleep 60                                 # NetworkManager autoconnects if a known network is in range
    active="$(nmcli -t -f NAME,DEVICE connection show --active | awk -F: '$2=="wlan0"{print $1}')"
    [ -n "$active" ] && { log "connected to $active"; exit 0; }
  else
    exit 0
  fi
fi

# Not connected: make sure we have been offline a little while (boot / roaming), then open the hotspot.
sleep 45
active="$(nmcli -t -f NAME,DEVICE connection show --active | awk -F: '$2=="wlan0"{print $1}')"
[ -n "$active" ] && exit 0
log "no Wi-Fi connection: opening setup hotspot '$SSID'"
nmcli connection delete "$CON" >/dev/null 2>&1 || true
nmcli connection add type wifi ifname wlan0 con-name "$CON" ssid "$SSID" \
  802-11-wireless.mode ap 802-11-wireless.band bg ipv4.method shared \
  wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$PASS" connection.autoconnect no >/dev/null \
  && nmcli connection up "$CON" >/dev/null && date +%s > "$STATE"
