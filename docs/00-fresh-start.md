# Fresh start: new SD card to trained detector

This is the fastest path. You need: the Pi Zero with its sensors and battery
HAT, an 8 GB (or larger) micro-SD card, a laptop with an SD card reader, and
your Wi-Fi name and password. **Keep the old SD card safe and don't
overwrite it.** It still holds the engineer's code and data.

## 1. Flash the card (15 min, on your laptop)

1. Install **Raspberry Pi Imager** from raspberrypi.com/software and open it.
2. **Choose Device**: the model printed on the board.
   - *Raspberry Pi Zero 2 W*, or
   - *Raspberry Pi Zero / Zero W*. A plain "Zero" without a **W** has no Wi-Fi.
     If that's what you have, tell me before going further.
3. **Choose OS** → *Raspberry Pi OS (other)* → **Raspberry Pi OS Lite (32-bit)**.
   Lite has no desktop, fits easily on 8 GB and runs better on a Pi Zero.
4. **Choose Storage**: your SD card (double-check it is not the old card!).
5. **Next → Edit settings** (OS customisation). This is where you create the
   credentials. Nothing is pre-set; you choose them:
   - **Hostname**: `detector`
   - **Username / password**: e.g. `admin` and a password you write down
   - **Wireless LAN**: your Wi-Fi name and password, and your country
     (e.g. NG). The Pi Zero only sees **2.4 GHz** Wi-Fi, not 5 GHz.
   - **Locale**: your time zone
   - **Services** tab: **Enable SSH**, "Use password authentication"
6. Save → Yes → write. Takes about 10 min.

## 2. First boot and log in (5 min)

Put the card in the Pi and power it on. Wait **3-5 minutes** (the first boot
of a Pi Zero is slow). On the laptop (same Wi-Fi), open a terminal
(Windows: *PowerShell*; Mac: *Terminal*):

```bash
ssh admin@detector.local
```

Type `yes` to the fingerprint question, then your password. If
`detector.local` isn't found, get the Pi's IP address from your router's
"connected devices" page (or the Fing phone app) and use `ssh admin@192.168.x.x`.

## 3. Install everything with one command (20-40 min, unattended)

In the SSH window:

```bash
curl -sSL https://raw.githubusercontent.com/Adebookey/illicit_compound_detector/main/pi/bootstrap.sh | sudo bash
```

It downloads the code, installs it, finds the sensors (including which pin
the DHT22 is on) and starts logging. At the end it prints a sensor check. If
it says to reboot, do `sudo reboot`, log in again after a minute, and run:

```bash
voc-detector check
```

Every line should say OK. If a sensor is MISSING, follow the hint printed
under it. The battery HAT: if it's a **PiSugar**, also run
`curl http://cdn.pisugar.com/release/pisugar-power-manager.sh | sudo bash`
(choose your PiSugar model), then `sudo systemctl restart voc-detector`.

**Leave the device running for 1 hour in clean air before the first
session.** A new ENS160 needs this once to stabilise.

## 4. Collect data with guided sessions (about 95 min each)

```bash
voc-detector guided
```

The program tells you exactly when to place and remove the compound, shows
live readings, and labels everything automatically. You don't need to write
anything down. One session = 10 min clean baseline + 4 rounds of (10 min
compound → 3 min recovery → 10 min clean).

- Do **at least 3 sessions**: different times of day, ideally different days
  and, if possible, 2 different rooms. The model is tested on a session it
  didn't see, so 3 is the minimum.
- If the SSH connection drops, the session stops but everything up to that
  point is kept. Start a new one.
- Strongly recommended: add everyday smells to one or two sessions so the
  model learns NOT to alarm on them:

  ```bash
  voc-detector guided --confounders "perfume,hand sanitiser,cooking"
  ```

## 5. Train on the Pi (5-10 min)

```bash
voc-detector train
```

It trains, tests itself on each session it didn't see, installs the model and
restarts the detector. Read the **Verdict** line at the top of the report it
prints. Detection now runs on its own whenever the Pi is on.

Test it: bring the compound close. Within about 10-30 s
`journalctl -u voc-detector -f` should show `DETECTION START`.

## Later

- **More data = better model.** Run more `guided` sessions any time, then
  `voc-detector train` again. The previous model is kept as `model.json.previous`.
- **Updates**: run the same `curl ... | sudo bash` command again.
- **Remote dashboard**: once the Supabase project exists, follow
  [05-dashboard.md](05-dashboard.md) and put the keys in
  `/etc/voc-detector/config.toml` (`sudo nano /etc/voc-detector/config.toml`).
