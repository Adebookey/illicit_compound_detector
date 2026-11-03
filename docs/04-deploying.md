# 4. Installing on the Pi

Prerequisites: you can SSH into the Pi ([01](01-accessing-the-pi.md)), your
data is copied off, and the old logger is stopped.

## Install

On the Pi:

```bash
sudo apt install -y git
git clone https://github.com/OWNER/REPO.git voc-detector
cd voc-detector
sudo bash pi/install.sh
```

(If the Pi has no GitHub access, copy the folder from your laptop instead:
`scp -r voc-detector USERNAME@IP:~`.) The install takes 10-20 minutes on a Pi
Zero. It installs into `/opt/voc-detector`, creates a background service that
starts on boot, and **logs data even before a model exists**.

Edit the settings, then restart:

```bash
sudo nano /etc/voc-detector/config.toml
sudo systemctl restart voc-detector
```

Check it:

```bash
journalctl -u voc-detector -f      # live log (Ctrl+C to leave)
voc-detector status                # latest readings
```

In the log you should see `ENS160 found`, `SGP40 found`, `DHT22 configured`
and `battery monitor: pisugar` (or `ina219`). If a sensor is missing, check its
wiring and `sudo i2cdetect -y 1`. If the DHT22 is on a different pin than
GPIO4, set `dht22_pin` (look at the engineer's code for the pin number).

## Install / update the model

After training on your laptop:

```bash
scp models/model.json USERNAME@IP:~
ssh USERNAME@IP "sudo cp ~/model.json /opt/voc-detector/models/model.json && sudo systemctl restart voc-detector"
```

The dashboard shows the model version each device runs.

## Battery HAT

`type = "auto"` tries a PiSugar first, then an INA219-based HAT (Waveshare
UPS HAT and similar). For a **PiSugar** install its server first, as PiSugar
documents: `curl http://cdn.pisugar.com/release/pisugar-power-manager.sh | sudo bash`.
For INA219 HATs nothing extra is needed. If you tell me the exact HAT model I
can tune the voltage-to-% curve.

At `low_pct` the device sends a low-battery alert; at `shutdown_pct` it
shuts down cleanly (pulling power from a running Pi can corrupt the SD card).

## Optional: a local LED or buzzer

Wire an LED (with a 330 Ω resistor) or an active buzzer between a free GPIO
and GND, set `[alarm] gpio_pin = 17` (for example) and restart.

## Updating the software

```bash
cd ~/voc-detector && git pull && sudo bash pi/install.sh
```
