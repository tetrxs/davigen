# 05 · Corrections per node

**Goal:** turn a `Measurement` into CDL values for 01–04, with limits, partial strengths and a confidence per
node.

**File:** `davigen/basic/correct.py`. Tests go in `tests/test_basic_correct.py`.

## Tasks

1. **`01_EXPOSURE`:**
   - Target from EV100 (the concept §5.1 table), or skin at 60–70 IRE through the simulator.
   - High key, low key and night: correction limited to what is clearly wrong.
   - Clamp to ±`max_stops`, then `offset = stops · STOP` on R, G and B.
2. **`02_WHITE_BALANCE`:**
   - Illuminant → xy → (CCT, Duv), with `colour.temperature.xy_to_CCT(method="Ohno 2013")`.
   - Duv to 0, fully, up to `max_duv`. Beyond that: clamp and flag.
   - CCT towards 6500 K with the strength from `cct_strength`.
   - New target white → per-channel linear gains in DWG → offsets. Normalise so luminance (DWG Y weights) is
     unchanged.
3. **`03_CONTRAST`:**
   - Simulate 01 + 02 first.
   - Bisect *c* in `range` so the black percentile lands in `black` and the white percentile stays under
     `white_max`. Stop early if both are already inside.
   - Haze: halve the change.
4. **`04_SATURATION`:** simulate 01–03, then scale mean chroma towards the target range, clamped to `range`.
   Never above 1.0 if chroma is already high.
5. **Confidence:**
   - Per node, from the measurement: estimator spread, achromatic fraction, clipping, spread within the clip, and
     whether a limit was reached.
   - Overall confidence = the minimum of the node confidences.
   - Flags are carried over from the measurement and added to here (`limit reached`).
6. **Result:** a dataclass `Correction`: CDL per node label, confidence, flags, and the intermediate values
   (stops, CCT before/after, *c*, sat) for the record.

## Tests

- A −1 stop grey card gets `offset` ≈ +0.0733 on all channels.
- 3000 K light: the correction moves CCT to 3000 + 0.35 · (6500 − 3000) ± 50 K.
- A pure green cast (Duv +0.01) is removed completely.
- WB offsets leave the luminance of a grey patch unchanged (± 0.002).
- Contrast never changes 0.336, and stays inside its range for extreme inputs.
- Night with EV100 = 3 at −1 stop: brightened by less than 1 stop.
- The chain (exposure → WB → contrast → sat) is applied in order: `simulate` with the result puts a grey card at
  0.336 and neutral.

## Done when

`correct(measurement, cfg) -> Correction` is deterministic and fully covered by the tests above.
