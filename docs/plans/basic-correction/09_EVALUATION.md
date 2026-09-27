# 09 · Evaluation and tuning

**Goal:** know how good Basic Correction is on the user's real footage, and tune it there (concept §11).

**Files:** `scripts/eval_basic_correction.py`, plus a small test-set description in `tests/basic_eval_set.toml`.
The footage itself stays out of the repo.

## Tasks

1. **Test set:**
   - 30–50 clips from finished projects, hand-graded in nodes 01–04.
   - The list, with categories, goes into `basic_eval_set.toml`: daylight, golden hour, night, backlight, drone,
     skin, snow or sand, forest or sea, interior mixed light.
2. **Reference renders:**
   - For each clip, load the user's version, disable nodes 05 and 06 (`SetNodeEnabled`), and render the sample
     frames through the full pipeline.
   - Then the same for `DAVIGEN_AUTO`.
   - The renders reuse the sampling code from step 03, now with grades on.
3. **Metrics per clip:**
   - exposure difference in stops (log-average luminance in DI / `STOP`)
   - WB angle in degrees (the illuminant estimated on both, same estimator)
   - mean ΔE2000 in the display image
   - flagged yes/no
4. **Summary:**
   - The medians and 90th percentiles per category.
   - A list of the 10 worst clips, each with its flags.
   - A check of whether the flags cover the misses: precision and recall of "flagged" vs "ΔE above 5".
5. **Tune:**
   - Change the values in `[basic_correction]` and re-run from the cache, which needs no Resolve.
   - Keep the best values as the defaults in `workflow.toml`.
6. **Baseline:** the same metrics for Resolve's own Auto Balance on the same clips, applied by hand once, so we
   know whether this beats the built-in tool.

## Acceptance (first targets, revisit after the first run)

- median exposure difference < 1/3 stop
- median WB angle < 2°
- ≥ 80 % of clips with ΔE2000 < 3 (a small tweak or nothing)
- ≥ 70 % of clips with ΔE2000 > 5 flagged
- clearly better than Resolve Auto Balance on exposure and WB

## Done when

The numbers are met or understood, and the summary is saved as `docs/concepts/BASIC_CORRECTION_RESULTS.md`.
