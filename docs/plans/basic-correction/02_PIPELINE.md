# 02 · Pipeline simulator

**Goal:** Python reproduces what Resolve does to a clip in a davigen project, so every later step can measure in
the working space and check targets in the display image.

**File:** `davigen/basic/pipeline.py`. Tests go in `tests/test_basic_pipeline.py`.

## Tasks

1. **DaVinci Intermediate:**
   - `to_linear(y)` and `to_log(x)` via `colour.models.oetf_DaVinciIntermediate` and its inverse
   - constants `GREY = 0.336`, `STOP = 0.07329248`
   - `stops_to_offset(s)` and `gain_to_offset(g)`
2. **LUT application:** `apply_lut(image, cube_path)` with `colour.read_LUT` and `LUT3D.apply`, trilinear
   (Resolve's default). LUTs read once and cached per path.
3. **CDL model:** `apply_cdl(image, slope, offset, power, sat)`, as ASC CDL (see concept §3). Step 01 measured
   slope and offset as exact, and saturation mixing with luma weights (0.21, 0.70, 0.09) instead of Rec.709. Use
   those as a module constant `SAT_LUMA`, so step 03 can correct them after its recheck.
4. **Chain:** `simulate(log_image, input_lut, nodes, output_lut)` gives `(dwg_after_nodes, display)`. Here `nodes` is
   a list of CDL dicts for 01–04, in order.
5. **Display helpers:** luminance (Rec.709 weights on the display image), CIELAB conversion, IRE scale
   (0–100 = 0–1 display, data levels).

## Tests

- 18 % grey in linear lands at 0.336 ± 1e-3.
- +1 stop offset doubles linear values above the toe (tolerance 1 %).
- A contrast of *c* around 0.336 leaves 0.336 unchanged for any *c*.
- CDL identity (slope 1, offset 0, power 1, sat 1) returns the input unchanged.
- `simulate` with identity nodes equals applying the two LUTs directly.
- If the Resolve-baked V-Log LUT exists locally, grey from V-Log code 0.423 lands on 0.336 after the input LUT,
  like `test_colormath.py`. Otherwise the test is skipped.

## Done when

The tests pass, and the simulator is the only place in `davigen/basic/` that knows about LUTs and CDL.
