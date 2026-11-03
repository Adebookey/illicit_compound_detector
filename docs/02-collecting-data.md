# 2. Collecting training data (and why more of it matters)

## What the sensors can and cannot do

The ENS160 and SGP40 are **metal-oxide (MOX) gas sensors**. They react to
almost every volatile organic compound: cannabis terpenes (myrcene, limonene,
pinene, caryophyllene) but also perfume, cleaning products, cooking, alcohol,
fruit, paint, smoke, breath and many more. They do not identify molecules.
What makes detection possible is the **pattern**: how the different signals
(ENS160 TVOC/eCO2, its hot-plate resistances, SGP40 raw signal) rise relative
to each other and how fast. The model learns that pattern from examples.

So a model is only as good as its examples, and it must see:

1. **The compound** - many times, at different distances, amounts, rooms and days.
2. **Clean air** - in the places the device will actually be used.
3. **Confounders** - the everyday smells it must NOT alarm on. Without these the
   model will happily learn "any VOC = marijuana", and the client will get
   false alarms from a coffee or a perfume.

## What to collect (minimum for a first useful model)

| What | How much | Notes |
|---|---|---|
| Compound | ≥ 15 periods of 5-15 min, over ≥ 5 different days | vary distance (5 cm, 30 cm, 1 m), amount, open/closed container |
| Clean air | ≥ 2 h per day of collection | always 10-15 min clean **between** exposures |
| Confounders | ≥ 3 periods each | perfume/deodorant, hand sanitiser/alcohol, cooking, coffee, citrus peel, cleaning spray, cigarette/tobacco, other plants/herbs (oregano, hops, pine) |

Tobacco, hops, citrus and pine share terpenes with cannabis; they are the
hardest cases and the most important confounders to include.

## Labelling - two ways

### A. Written times (what you did so far)

Write a `labels.csv` like `data/labels.example.csv`:

```
start,end,label,session,notes
2026-10-28 14:05,2026-10-28 14:20,clean,day1,desk, window closed
2026-10-28 14:20,2026-10-28 14:35,compound,day1,1 g in open jar, 10 cm away
2026-10-28 14:35,2026-10-28 15:00,clean,day1,jar removed and closed
```

- `label` is `compound`, `clean`, or `confounder:<what>`.
- `session` groups one sitting (usually the date).
- Write down times **when** you place / remove things, from a clock you trust.
- If you only wrote down the compound periods, that's fine: add `clean` rows
  for the quiet times before/after them that you are sure about.

### B. The built-in `collect` command (recommended from now on)

Once the new software is installed on the Pi:

```bash
ssh USERNAME@IP
voc-detector collect clean -m 15 -n "office, window closed"
voc-detector collect compound -m 10 -n "1 g, open jar, 10 cm"
voc-detector collect clean -m 15
voc-detector collect confounder:perfume -m 5 -n "one spray 1 m away"
```

It records exact start/end times in the Pi's own clock (no clock-offset
problems). Export everything later with:

```bash
voc-detector export ~/export.csv        # also writes ~/export_labels.csv
```

## Good practice

- Power the device on in **clean air** and let it warm up **at least 10
  minutes** before the first exposure (the ENS160 needs ~1 h after a long
  power-off to be fully stable; the first day it ever runs, 24 h).
- Don't move the device between rooms in the middle of a period.
- Let readings return to normal (clean) before the next exposure.
- Note anything unusual (window opened, someone cooking) in `notes`.
- Collect on different days and at different temperatures: the validation
  tests each day on a model that never saw that day.
