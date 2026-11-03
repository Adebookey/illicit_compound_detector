# 1. Getting into the Pi and finding the engineer's code and data

You need: the Pi, its power supply, a laptop on the **same Wi-Fi** as the Pi,
and ideally a micro-SD card reader.

## Step 0 - Back up the SD card first (10 minutes, strongly recommended)

Everything the engineer did lives on the micro-SD card. Copy it before you
touch anything, so nothing can be lost.

1. Shut the Pi down (unplug only once the green LED has stopped blinking).
2. Put the micro-SD card in your laptop's card reader.
3. Make an image:
   - **Windows**: install *Win32 Disk Imager*, select the card's drive letter,
     type a file name like `pi-backup.img`, click **Read**.
   - **macOS / Linux**: run `diskutil list` (mac) or `lsblk` (Linux) to find
     the card, then
     `sudo dd if=/dev/diskN of=pi-backup.img bs=4m` (mac: use `/dev/rdiskN`).
4. Keep `pi-backup.img` somewhere safe. Put the card back into the Pi.

## Step 1 - Find the Pi on the network

Power it on and wait 2 minutes. Then try, in order:

1. From a laptop terminal: `ping raspberrypi.local` (also try the hostname the
   engineer may have used, e.g. `ping sensor.local`). If it answers, note the IP.
2. Log in to your **Wi-Fi router's admin page** → "connected devices" / "DHCP
   clients". Look for `raspberrypi` or a device made by "Raspberry Pi
   Foundation/Trading".
3. Phone app **Fing** (free): scan the network; it labels Raspberry Pis.

If the Pi is not on your network at all, it is probably configured for the
engineer's Wi-Fi. Either ask the engineer for the network name/password, or
take the SD card out and on its small `bootfs` partition follow
[Raspberry Pi's headless Wi-Fi guide](https://www.raspberrypi.com/documentation/computers/configuration.html#connect-to-a-wireless-network)
(for Bookworm: create the connection with Raspberry Pi Imager's settings or
add a `custom.toml`). As a last resort plug in a mini-HDMI screen and a USB
keyboard (via a micro-USB OTG adapter).

## Step 2 - Log in with SSH

```bash
ssh USERNAME@IP_ADDRESS
```

- Windows 10/11 has `ssh` built into PowerShell. (PuTTY also works.)
- Try the username `pi` first; on new Raspberry Pi OS versions there is no
  default user, so it is whatever the engineer created. **Ask the engineer for
  the username and password** - it is the quickest path.
- "Connection refused" means SSH is off: put the SD card in your laptop and
  create an empty file named `ssh` on the `bootfs` partition, then retry.
- Lost password? With the SD card in your laptop, open `cmdline.txt` on
  `bootfs`, add ` init=/bin/sh` to the end of the (single) line, boot the Pi
  with a screen + keyboard, then run `mount -o remount,rw /`, `passwd USERNAME`,
  `sync`, and power off. Remove ` init=/bin/sh` again afterwards.

## Step 3 - Run the inventory script

From your laptop (in this repo's folder), copy the script over and run it:

```bash
scp pi/inventory.sh USERNAME@IP_ADDRESS:~
ssh USERNAME@IP_ADDRESS "bash ~/inventory.sh > ~/inventory.txt 2>&1"
scp USERNAME@IP_ADDRESS:~/inventory.txt .
```

`inventory.txt` lists: the engineer's Python scripts, what starts
automatically on boot, which sensors are detected on I2C, the data files
(newest first, with sizes) and the first lines of the biggest CSVs. It only
reads; it changes nothing. **Send it to me** and I can tell you exactly which
files are the data and adapt the importer if needed.

Key things to look for:

| In the report | What it means |
|---|---|
| `I2C DEVICES` shows `52`/`53` and `59` | ENS160 and SGP40 are wired and alive |
| a service under `AUTO-START` you don't recognise | the engineer's logger; note its name |
| `.csv` / `.db` files that grew on the days you collected | your data |
| `Time zone` / `synchronized: no` | see "clock warning" below |

## Step 4 - Copy the data and code to your laptop

```bash
mkdir -p data/raw engineer-code
scp -r USERNAME@IP_ADDRESS:/path/to/the/data/folder  data/raw/
scp -r USERNAME@IP_ADDRESS:/path/to/the/engineer/scripts  engineer-code/
```

(Windows alternative with a GUI: **WinSCP**, connect with the same
user/IP/password and drag the folders across.)

Keep the engineer's code: it shows which pins/addresses they used and how
their data columns are defined.

## Clock warning (important for your labels)

A Pi Zero has no clock battery. If it had **no internet** while you collected
data, its timestamps may be off - sometimes by days. On the Pi run `date` and
compare with your phone. If it's wrong now, it was likely wrong then too.
`training/label.py --clock-offset` corrects for it, and
`training/plot_session.py` lets you check visually that the sensor jumps line
up with the times you wrote down.

## Step 5 - Stop the old logger before installing the new one

Only one program can talk to the sensors at a time. Once your data is safely
copied:

```bash
sudo systemctl stop NAME_OF_ENGINEER_SERVICE
sudo systemctl disable NAME_OF_ENGINEER_SERVICE   # don't start on boot anymore
```

(If it was started from `crontab` or `rc.local` instead, comment out that line
with `crontab -e` / `sudo nano /etc/rc.local`.) Nothing is deleted; you can
re-enable it any time. Then continue with [04-deploying.md](04-deploying.md).
