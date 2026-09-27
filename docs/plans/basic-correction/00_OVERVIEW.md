# Plan: Basic Correction

Implements [docs/concepts/BASIC_CORRECTION.md](../../concepts/BASIC_CORRECTION.md). Each step is a separate,
reviewable change. Each one ends with tests or a check in Resolve, and leaves davigen working.

| # | Step | Needs Resolve | Depends on | Status |
|---|---|---|---|---|
| 01 | [API spike in Resolve](01_SPIKE.md) | yes | – | script ready, waiting for a run |
| 02 | [Pipeline simulator](02_PIPELINE.md) | no | – | open |
| 03 | [Frame sampling and cache](03_SAMPLING.md) | yes | 01 | open |
| 04 | [Measurements](04_MEASUREMENTS.md) | no | 02 | open |
| 05 | [Corrections per node](05_CORRECTIONS.md) | no | 02, 04 | open |
| 06 | [Scenes and matching](06_SCENES.md) | no | 05 | open |
| 07 | [Writing into Resolve](07_WRITE.md) | yes | 01, 05 | open |
| 08 | [UI, entry points, config](08_UI.md) | yes | 03, 07 | open |
| 09 | [Evaluation and tuning](09_EVALUATION.md) | yes | 08 | open |

Steps 02, 04, 05 and 06 are pure Python with numpy and colour-science. They can be built and tested without
Resolve while step 01 is still waiting. Step 01 decides the details of 03 and 07. If the spike shows that
`SetCDL` doesn't work in Free, the plan stops there and gets revised.

**New module layout** (all under `davigen/basic/`, so the feature stays in one place):

```
davigen/basic/
├── __init__.py
├── pipeline.py      DaVinci Intermediate math, LUT application, CDL model, display simulation   (02)
├── sampling.py      analysis timeline, render, TIFF reading, cache                              (03)
├── measure.py       metering, WB estimators, percentiles, chroma, skin mask, flags             (04)
├── correct.py       stops/illuminant/contrast/sat → CDL per node, limits, confidence           (05)
├── scenes.py        scene grouping, hero, pulling to the scene                                 (06)
├── write.py         versions, nodes by label, SetCDL, markers, JSON record                     (07)
└── run.py           the flow used by the UI and the menu entry                                 (08)
```

**New dependency:** `tifffile` (pure Python, needs only numpy) in `requirements.txt`, added in step 03.
