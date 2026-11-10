# 7. Showing the client the results (report + live demo)

## Before the meeting: make the report

On the Pi:

```bash
voc-detector evidence
```

It writes `~/evidence/report.html` (charts and tables) and `~/evidence/data.csv`
(the raw readings for every compound, perfume, alcohol and clean-air period).
Everything is computed from the readings the device logged. To leave out a
session, list the ones to keep: `voc-detector evidence --only S...,S...`
(`voc-detector sessions` shows the names).

If you moved the sample closer than the planned distance during some rounds,
correct the record first (applies to all sessions), then re-create the report:

```bash
voc-detector placement --change "5 cm" --to "close range"
voc-detector placement --change "10 cm" --to "close range"
voc-detector evidence
```

## Viewing it and the live demo

```bash
voc-detector demo
```

It prints an address like `http://raspberrypi.local:8000`. Open it in a
browser on a laptop or phone **on the same Wi-Fi as the Pi** (if `.local`
doesn't work, use the number address it also prints).

- `/` shows **live readings**: big numbers, the change from baseline, and
  10-minute charts. The buttons mark when you bring each item close. Demo
  runs are kept out of the report (add `--include-demo` to `evidence` if you
  ever want them in).
- `/report` shows the report. Press **Save as PDF / Print** at the top and
  choose **Save as PDF** as the printer. (Android Chrome: if the button does
  nothing, use the menu ⋮ → Share → Print.) `/data.csv` downloads the raw data.

At the client's site, bring your phone and use its hotspot for both the Pi
and the laptop (add the hotspot first with `sudo detector-wifi add ...`).
Keep `voc-detector demo` running in `tmux`.

No Wi-Fi in common? `voc-detector watch` shows the live numbers in the
Raspberry Pi Connect shell instead.

## A 15-minute live demonstration

1. Have the device running for at least 15 minutes beforehand, away from
   perfume, food and cleaning products. Keep all test items sealed and out of
   the room.
2. Open the live page. Wait until the numbers are steady, then press **Reset
   baseline**.
3. **Compound:** open the container and hold it **3-5 cm** from the sensors.
   Press **Compound placed**. Wait 3-5 minutes and let the client watch the numbers.
   Remove it, press **Item removed**, and wait for the readings to settle.
4. **Isopropyl alcohol:** a few drops on a cotton pad, about 30 cm away. Press
   **Isopropyl alcohol placed**. The readings jump within seconds. Remove it, press
   **Item removed**, and air the room. Recovery takes several minutes.
5. **Perfume last:** it lingers for a long time and would spoil anything after it.
   One spray about 1 m away. Press **Perfume placed**.
6. Then open `/report` and walk through the findings from the earlier sessions.

The live demo shows the same thing as the report, happening in front of them.
