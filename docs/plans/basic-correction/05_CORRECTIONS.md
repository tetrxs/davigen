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

## Result (2026-09-28)

Done: [`davigen/basic/correct.py`](../../../davigen/basic/correct.py), settings in `[basic_correction.exposure]`,
`.white_balance`, `.contrast`, `.saturation` and `.confidence`. Tests in
[`tests/test_basic_correct.py`](../../../tests/test_basic_correct.py). Entry point:
`correct(measurement, samples, output_lut, settings) -> Correction`. What changed on the way:

- **Offsets are solved, not multiplied.** A log offset is one stop only above the toe. `01` is solved so the frame's
  key lands exactly on its target; `02` so that a neutral surface at the key, as `01` leaves it, lands exactly on
  the target white. The −1 stop grey card gets +0.069 instead of 0.0733 and ends at 0.336.
- **Duv target is D65's Duv (+0.0032), not 0.** Rec.709 white is D65, which sits slightly above the blackbody
  locus; "Duv to 0" would tint every clip magenta.
- **Skin and dominant hue are measured on a white-balanced image** (in `measure.py`): under tungsten light every
  grey wall looked like skin.
- **Clipped highlights:** darkening stops where the camera's clip level would fall below display white, and pixels
  the camera clipped don't count for the contrast white point. If the white point is above `white_max` already,
  contrast may take it up to `white_ceiling`.
- `cct_strength` got a row for above 7500 K (60 %), because shade and blue hour are often intended too.
- On the real spike clip (backlit, flare, +2.7 stops, 9 % clipped sky): −0.75 stop (held by the high-key rule),
  6504 K, contrast 1.17, saturation 1.25, overall confidence 0 with six flags. A colorist would pull it down
  further; whether the high-key rule is too careful is for the evaluation (step 09).
- 0.6 s per clip for 6 samples, all four nodes.

## Calibration on real footage (2026-09-28)

`scripts/basic_offline.py` on all 57 clips of MARSEILLE_2026 (52 × Lumix S1II 6K V-Log, 5 × DJI Air 3 D-Log M,
backlight, windscreens, sea, limestone). What the first rules got wrong, and what changed:

| Seen | Cause | Change |
|---|---|---|
| almost every clip "exposed for skin" | beige limestone and sand sit at ~140° on the vectorscope, inside the skin window | skin window 110–136°; skin only nudges the key-based exposure by ≤ 0.5 stop |
| the V-Log clips were 2–3 stops over (ETTR) but came down at most 1.5 | symmetric limit | down to −3 stops, up to +1.5 |
| a sunny DJI cliff was brightened 2.7 stops and burned out | the DJI LUT puts highlights on a ceiling, measured as "clipped" and left out of the key | clipped pixels count for exposure (not for white balance) |
| a dark sea under bright rock was brightened | nothing checked the highlights when brightening | brighten only until the 99.5th percentile reaches `white_max` |
| scene matching moved a clip to −4.9 stops | the pull ignored the shot's limits | every shot records its exposure range; the pull stays inside it |
| 54 of 57 clips flagged | thresholds from synthetic scenes; hand-held shots vary a lot between samples | flag thresholds at about the 85th percentile of these clips; CCT spread in mired |
| contrast/saturation at their range flagged as "limit reached" | — | only exposure and white balance limits count |

After: median exposure −0.96 stops (−3.0 … +1.1), 24 of 57 clips with a marker, and a contact sheet that looks like
a first grade (sky and colour back, haze reduced, blown skies held). Known weak spot: an interior with a bright
window is metered on the window and ends up too dark. Tuning further needs hand grades (step 09).
