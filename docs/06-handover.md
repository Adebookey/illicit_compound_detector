# 6. Handing the device to the client (their Wi-Fi + your remote access)

Two separate things are needed:

1. **The Pi must get onto the client's Wi-Fi.** It joins any Wi-Fi it already
   knows that's in range, so add theirs before you leave (or on site).
2. **You need to reach it from anywhere.** Raspberry Pi Connect gives you a
   terminal in your web browser, from any network, with no router setup.

## A. Set up Raspberry Pi Connect (once, before handover)

Connect is Raspberry Pi's own free remote-access service. On a Lite system
it gives a **remote shell** (a terminal in your browser), which is all this
device needs. The installer already installed it (`rpi-connect-lite`).

1. Create a free **Raspberry Pi ID** at id.raspberrypi.com and turn on
   two-factor authentication (this account can open the client's device).
2. On the Pi:

   ```bash
   rpi-connect on
   rpi-connect signin
   ```

   It prints a link. Open it on your laptop/phone, sign in, and name the
   device (e.g. `detector-clientname`).
3. From now on, go to **connect.raspberrypi.com** → your device → **Connect
   via remote shell**. It works whenever the Pi has internet, from any network.
4. Check that it survives a reboot: `sudo reboot`, wait 2 minutes, and see
   the device come back online in Connect. (The installer ran
   `loginctl enable-linger` so Connect runs without anyone logged in.)

If `rpi-connect` is "command not found", install it manually:
`sudo apt update && sudo apt install -y rpi-connect-lite`.

Raspberry Pi's documentation is the reference for Connect's current features
and terms: raspberrypi.com/documentation/services/connect.html

## B. Add the client's Wi-Fi

### Best: before you leave (ask the client for the Wi-Fi name and password)

```bash
sudo detector-wifi add "ClientWiFiName" 'their-password' 20
sudo detector-wifi list
```

This only saves it; your current connection is unchanged. At the client's
site the Pi finds their network and joins it automatically. Higher priority
wins when several known networks are in range.

Also add your **phone's hotspot** as a backup, so you can always get the device
online on site:

```bash
sudo detector-wifi add "MyPhoneHotspot" 'hotspot-password' 5
```

Things to check with the client:
- It must be **2.4 GHz** Wi-Fi (the Zero 2 W can't see 5 GHz-only networks).
- "Guest" networks with a login web page (captive portals) won't work. Use a
  normal WPA2 password network.
- Spell the name exactly, including capitals and spaces.

### On site, if it doesn't connect: the setup hotspot

If the Pi can't join any known Wi-Fi for about 3 minutes, it opens its own
Wi-Fi network:

- **Name**: `detector-setup` (hostname + `-setup`)
- **Password**: printed at the end of the install. Show it again any time
  with `sudo detector-wifi hotspot-info`. **Put it on a sticker on the device.**

Connect your phone or laptop to that network, then:

```bash
ssh admin@10.42.0.1
sudo detector-wifi add "ClientWiFiName" 'their-password' 20
sudo reboot
```

(On a phone, use an SSH app such as Termius.) After the reboot it joins the
client's Wi-Fi. The setup hotspot closes by itself whenever a normal
connection is working. If nobody logs in through it, it also turns itself
off every 10 minutes to retry the known networks (e.g. after a router
restart).

## C. Before you leave the client

```bash
voc-detector check            # sensors, battery and model all OK
detector-wifi list            # connected to the client's network
```

Then confirm that the device shows online in connect.raspberrypi.com from
your phone, on mobile data rather than the client's Wi-Fi.

## Security

- Change the default password if you used a simple one: `passwd`.
- Your Raspberry Pi ID can open a shell on the client's device: keep 2FA on.
- To give the client their own remote access later, Raspberry Pi Connect has
  options for sharing devices with other accounts/organisations; check their
  current documentation.
