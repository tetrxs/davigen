# Concept: Basic Correction

**Status:** built (steps 01–08 in code, tested against a fake Resolve); the check in Resolve and the tuning on hand-graded clips are open. API answers in §12. The step-by-step plan is in
[docs/plans/basic-correction/](../plans/basic-correction/00_OVERVIEW.md).

Basic Correction fills the technical nodes of every clip grade (`01_EXPOSURE`, `02_WHITE_BALANCE`,
`03_CONTRAST`, `04_SATURATION`) with measured values, so grading time goes into the creative part: secondaries,
windows, looks. It is a first pass, not a finished grade. Where the measurement is unsure, it says so on the clip
instead of guessing.

- [1. What it does and what it doesn't](#1-what-it-does-and-what-it-doesnt)
- [2. Why davigen is a good place for it](#2-why-davigen-is-a-good-place-for-it)
- [3. The color pipeline in numbers](#3-the-color-pipeline-in-numbers)
- [4. Getting pixels](#4-getting-pixels)
- [5. Measuring and correcting, node by node](#5-measuring-and-correcting-node-by-node)
- [6. Scenes: balance, then match](#6-scenes-balance-then-match)
- [7. Confidence and flags](#7-confidence-and-flags)
- [8. Writing into Resolve safely](#8-writing-into-resolve-safely)
- [9. How the user runs it](#9-how-the-user-runs-it)
- [10. Configuration](#10-configuration)
- [11. How we know it works](#11-how-we-know-it-works)
- [12. Answers from the API spike](#12-answers-from-the-api-spike)

---

## 1. What it does and what it doesn't

| Node | Automatic | How reliable |
|---|---|---|
| Group Pre-Clip (camera log → DWG) | already done by davigen | exact |
| `01_EXPOSURE` | Offset, in stops | medium to good, with the rules in §5.1 |
| `02_WHITE_BALANCE` | Offset per channel | medium: fails on scenes without neutral surfaces, must detect that |
| `03_CONTRAST` | Slope + Offset around middle grey | good |
| `04_SATURATION` | CDL saturation | medium; skin tones are checked, never rotated |
| Matching clips of one scene | yes | good, this saves the most time |
| Exposure that changes inside a clip | flagged only | keyframes can't be scripted |
| `05_SECONDARIES`, `06_FINISH`, looks | no | creative work stays with the user |

Basic Correction never decides what a shot *should* look like. A silhouette, a night street or a sunset stays one.
The rules for that are in §5 and §6.

## 2. Why davigen is a good place for it

Generic auto tools (Resolve's Auto Balance, Shot Match) look at the displayed image and know nothing about how it
was made. davigen knows the whole chain, because it built it:

- the camera and log profile of every clip (clip metadata, `davigen.profile`)
- the input LUT in Group Pre-Clip, which davigen computed or baked itself (`data/luts/DAVIGEN_IN_*.cube`)
- the working space, DaVinci Wide Gamut / DaVinci Intermediate
- the output LUT in Group Post-Clip (`DAVIGEN_OUT_DWG_TO_REC709_G24`)
- recording time, ISO and lens from the scan

So Python can reproduce exactly what the nodes see and what the viewer will see:

```
camera log ──[input LUT]──▶ DWG / Intermediate ──[01–04, CDL]──▶ DWG / Intermediate ──[output LUT]──▶ Rec.709
                            ▲ measure here                                                           ▲ check targets here
```

Corrections are computed in the space the nodes work in, and targets such as "skin at 60–70 IRE" are checked
after the simulated output LUT.

**Studio is not needed.** davigen runs from Workspace → Scripts, inside Resolve, where the Free version allows
scripting. Only external connections need Studio.

## 3. The color pipeline in numbers

The clip nodes run in DaVinci Intermediate, a log encoding:

```
y = (log2(x + 0.0075) + 7.0) · 0.07329248        for linear x above 0.00262409
```

This gives the constants everything else is built on:

| Quantity | Value in DaVinci Intermediate |
|---|---|
| 18 % grey | **0.336** |
| one stop | **0.0733**: doubles *x* + 0.0075, so +1.03 stops at 18 % grey, +1.01 from 50 % up |

The API writes grades as ASC CDL per node (`TimelineItem.SetCDL`):

```
out = (in · slope + offset) ^ power             per channel
out = luma + sat · (out − luma)                 luma weights (0.21, 0.70, 0.09), measured in step 01
```

In a log space this maps cleanly onto what a colorist does by hand:

| Correction | CDL in DaVinci Intermediate | Why it is right |
|---|---|---|
| exposure by *s* stops | offset ≈ 0.0733 · *s* on all channels, solved exactly at the frame's key | adding in log is multiplying in linear, like opening the iris |
| white balance | offset per channel ≈ 0.0733 · log2(gain), solved exactly at the frame's key | a per-channel linear gain in DWG primaries |
| contrast *c* around grey | slope = *c*, offset = 0.336 · (1 − *c*) | middle grey stays where it is |
| saturation | sat | used as it is |
| power | stays 1.0 | a gamma on log data has no clean meaning |

The values land in the node's primaries and stay editable. Nothing is baked in. Resolve applies them directly to the
values that enter the node, with no colour management in between, and shows them on its Lift and Gain wheels rather
than on Offset (verified in step 01, see §12).

**Limit:** per-channel gains are a good white balance for moderate errors. For large ones (tungsten light shot with
a daylight setting) Resolve's Chromatic Adaptation is cleaner. Such clips get a flag and only a partial correction.

## 4. Getting pixels

The API can't read pixels, so Resolve renders them itself:

1. davigen builds a scratch timeline, `ZZ_DAVIGEN_ANALYSIS`, following the same pattern as `ZZ_DAVIGEN_BAKE` in
   `color.py`. It holds single-frame snippets (`MediaPool.AppendToTimeline` with `startFrame`/`endFrame`), several
   per clip, spread over the part of the clip that is actually used.
2. The snippets carry no group and no grade. In davigen's DaVinci YRGB project, the render then outputs the camera
   log values untouched.
3. davigen renders the timeline at a small size, about 480 px wide, as an uncompressed 16-bit TIFF sequence
   (`tif` / `RGB16`), then deletes the timeline. In step 01 this took 0.1 s per frame for 6K HEVC.
4. Python reads the TIFFs (a small reader for uncompressed TIFF, as in the spike, so no new dependency), applies the clip's own input LUT (`colour.LUT3D`), and gets
   DWG/Intermediate exactly as Group Pre-Clip produces it.
5. Per sample it keeps a small float16 thumbnail (about 96 × 64 px) plus statistics in
   `03_WORK/ANALYSIS/<clip>.npz`, then deletes the TIFFs. Algorithms can be retuned later without rendering again.

Why not ffmpeg: Resolve decodes every format it can play, including BRAW and ProRes RAW. The frames also match the
pipeline exactly, and nothing extra needs to be installed.

**Which frames:**

- On a timeline, samples lie inside each item's used source range: `GetSourceStartFrame()` up to
  `GetSourceStartFrame() + GetDuration()` (see §12 for why not `GetSourceEndFrame`).
- The default is 5 samples per item, plus 1 per extra 4 s of used duration, 12 at most.
- The cache is per media clip and frame, so the same clip on several timelines is measured once.

## 5. Measuring and correcting, node by node

Order matters. Each node is computed on the output of the ones before it, in the simulated pipeline.

### 5.1 `01_EXPOSURE`

**Measure:**

- log-average (geometric mean) luminance of the frame in linear DWG, not the median
- pixels clipped in camera or near black are left out
- a soft centre weighting

**Priority to skin:** when at least 2 % of the frame is skin (§5.4), the skin median is the reference. Its target is
60–70 IRE after the output LUT, the value in `docs/WORKFLOW.md`.

**Scene brightness from metadata:** from aperture, shutter and ISO:

```
EV100 = log2(N² / t) − log2(ISO / 100)
```

This tells a dark scene apart from an underexposed one, which pixels alone can't do. **But** video is often shot
through a (variable) ND filter, which the metadata doesn't record: the spike clip, bright daylight at f/2.8, 1/50 s,
ISO 640, gives EV100 5.9. So EV100 only counts when the picture agrees: a low EV100 on a bright frame means ND, not
night. DJI drones write no exposure data at all.

| EV100 | Scene | Target |
|---|---|---|
| ≥ 10 | daylight | 18 % grey |
| 5–10 | dusk, interior | lowered step by step, down to −1 stop |
| < 5 | night | −1.5 stops. Only brightens a clip that is clearly underexposed even for night |

**High key and low key:**

- Snow and beach: a bright, low-saturation histogram with little in the shadows counts as high key and is not
  pulled down to grey.
- Silhouettes: much of the frame near black plus a bright, clipped background count as low key. They are not
  brightened.

Both only get a flag.

**Limit:** ±1.5 stops. Anything beyond that is flagged and clamped.

**Write:** offset = 0.0733 · stops, equal on R, G and B.

### 5.2 `02_WHITE_BALANCE`

**Measure:** four estimators of the illuminant in linear DWG, midtones only, clipped pixels excluded:

1. grey world
2. shades of grey (Minkowski p = 6)
3. grey edge (first-order derivatives, p = 6)
4. achromatic pixels: the pixels closest to neutral under the current estimate, iterated

The illuminant is their per-channel median. Their angular spread feeds the confidence (§7).

**Split before correcting:**

- The estimate is converted to xy and split into correlated colour temperature (CCT) and **Duv**, the distance from
  the blackbody locus, with `colour.temperature` (Ohno 2013).
- **Duv (green/magenta) is corrected fully**, to the Duv of D65 (+0.0032), the white of Rec.709. A green or magenta cast is almost always an error, from light
  sources or AWB. Warm and cool, on the other hand, is often the point of the shot.
- **CCT is corrected partially**, towards 6500 K:

  | CCT | Strength |
  |---|---|
  | 5000–7500 K | 100 % |
  | 3500–5000 K | 60 % |
  | below 3500 K (golden hour, tungsten, fire) | 35 % |

  Golden hour stays golden, but loses the orange overload.

**Camera metadata:** if the camera recorded a fixed Kelvin value (not AWB) and it is the same across the scene,
colour differences between its clips are probably real. Per-clip WB then leans more on the scene value (§6).

**Low confidence:**

- One hue fills more than 50 % of the frame (forest, sea, sky, walls).
- Too few achromatic pixels.
- The estimators disagree.

Such a clip takes the scene's white balance (§6), or gets no correction and a flag.

**Write:** per-channel offsets from the linear gains that map the illuminant to D65 in DWG, normalised so that
luminance is unchanged (exposure already lives in `01`).

### 5.3 `03_CONTRAST`

**Measure:** the 0.5th and 99.5th percentiles of luminance **after the simulated output LUT**, so DaVinci tone
mapping's highlight roll-off is taken into account.

**Correct:**

- Solve for contrast *c* around 0.336, by bisection with the simulator.
- Targets: black point at 2–4 % display, white point at or below 95 % display.
- Nothing is pushed into clipping that wasn't clipped in camera.

**Limit:** *c* between 0.85 and 1.35. Flat, soft scenes (fog, haze, backlight flare) have a small dynamic range and
little local contrast. They are recognised and only get a mild correction plus the flag "haze: intended?".

**Write:** slope = *c*, offset = 0.336 · (1 − *c*), on R, G and B.

### 5.4 `04_SATURATION`

**Measure:** mean chroma in the simulated display image (CIELAB C\*), leaving out highlights and near-black pixels.

**Correct:** move it towards a target range, clamped to 0.85–1.25. An already colourful frame is never boosted
further.

**Skin check, warning only:**

- Skin pixels are found by a hue and chroma mask in YCbCr: a coarse mask, no face detection.
- Their hue is compared with the skin line, about 123° on Resolve's vectorscope.
- More than about 8° off gives a flag. Skin is never rotated automatically, because that is a secondary.

**Write:** CDL saturation only.

## 6. Scenes: balance, then match

Pushing every clip to the same absolute target destroys a sequence's mood. Colorists balance each shot, then match
the shots of a scene to a hero shot. Basic Correction does the same:

1. **Group into scenes:**
   - by recording time, a gap of more than 10 minutes starts a new scene (`created` from the scan)
   - order on the timeline breaks ties
   - within a time block, a large jump in estimated illuminant or EV100 also starts a new scene
2. **Choose a hero per scene:** the clip with the highest confidence, weighted by how long it is on the timeline.
3. **Scene values:** the hero's corrected exposure level and illuminant become the scene's reference.
4. **Per clip:**
   - high confidence: its own correction, pulled 30 % towards the scene reference so shots cut together smoothly
   - low confidence: it inherits the scene's white balance and exposure level. A close-up of a green bush gets the
     white balance of the wide shot taken a minute earlier.
5. **Across cameras:** V-Log and D-Log M clips of one scene are matched like any others. This works because both
   are in DWG already.

## 7. Confidence and flags

Every correction gets a confidence from 0 to 1. Clips below a threshold get a timeline marker (custom data
`davigen-basic`) whose note says why:

| Flag | Trigger |
|---|---|
| `clipped highlights` | more than 3 % of the frame clipped in camera (sky, windows) |
| `no neutral surfaces` | WB estimators disagree by more than 5°, or fewer than 1 % achromatic pixels |
| `dominant colour` | one hue fills more than 50 % of the frame |
| `mixed light` | WB estimate differs strongly between frame regions |
| `changes within clip` | exposure varies over 0.5 stop, or CCT over 800 K, across the samples |
| `high key / low key / night` | from §5.1; exposure left close to the camera |
| `haze` | from §5.3 |
| `skin off the skin line` | from §5.4 |
| `limit reached` | a correction was clamped |
| `not in its colour group` | the clip has no Group Pre-Clip transform, so nothing is written |

The report lists every clip with its values, confidence and flags. A click jumps to the clip in Resolve.

## 8. Writing into Resolve safely

- **Preconditions:**
  - The clip is in its davigen colour group. Otherwise it is assigned first, the same as *Assign groups & nodes*.
  - It has the node structure, and the nodes are found by label, not by position.
- **Its own grade version:** the correction goes into a local version called `DAVIGEN_AUTO` (`AddVersion`,
  `LoadVersionByName`).
  - `AddVersion` copies the current grade and makes the copy active (§12). So `DAVIGEN_AUTO` starts as the user's
    grade, with the node structure and everything in `05` and `06`, and only 01–04 are then overwritten.
  - The name of the version it was copied from is stored in the record, so davigen can switch back to it.
  - The user's own version is never changed (checked on the pixels in step 01).
  - Switching between the two versions on the Color page is a quick before/after.
  - This matters because script changes can't be undone in one step.
- **Only nodes 01–04** are written. `05_SECONDARIES` and `06_FINISH` are never touched.
- **Running again:**
  - By default only clips that don't have `DAVIGEN_AUTO` yet are corrected.
  - "Recompute all" overwrites existing `DAVIGEN_AUTO` versions and says so first, because tweaks made inside that
    version are lost. It loads `DAVIGEN_AUTO` and writes 01–04 again, so `05`/`06` inside it stay as they were
    copied the first time.
  - Old `davigen-basic` markers are removed and set again.
- **Dry run:** measure and flag only, without writing any grades. Good for building trust.
- **Record:** all measurements and written values go to
  `00_ADMIN/PROJECT_INFO/basic_correction/<timeline>.json`. That file is used both for evaluation (§11) and to
  explain any value.

## 9. How the user runs it

| Where | What happens |
|---|---|
| New project wizard | Checkbox **"Basic correction"**. It analyses the footage while the project is built, and switches on automatic correction for this project (stored in `davigen.json`). |
| *Assign groups & nodes* | If the project has it switched on, clips that got the node structure also get their `DAVIGEN_AUTO` version, from the cache. |
| Home screen | Button **"Basic correction"** for the current timeline, with *Dry run* and *Recompute all* as options. It works on any davigen project, including existing ones. |
| Workspace → Scripts | A second entry, **"davigen Basic Correction"**, opens davigen straight to Basic Correction for the current timeline. |

**Recommended moment:** on the master timeline after picture lock. Only used clips and used ranges are measured,
and grades are stored per timeline item anyway.

## 10. Configuration

A new section in `config/workflow.toml`. Every number above lives here, not in code:

```toml
[basic_correction]
samples = { min = 5, per_seconds = 4, max = 12 }
analysis_width = 480
exposure = { max_stops = 1.5, skin_ire = [60, 70] }
white_balance = { cct_strength = [[5000, 1.0], [3500, 0.6], [0, 0.35]], max_duv = 0.02 }
contrast = { range = [0.85, 1.35], black = [0.02, 0.04], white_max = 0.95 }
saturation = { range = [0.85, 1.25] }
scenes = { gap_minutes = 10, pull_to_scene = 0.3 }
confidence_flag_below = 0.6
```

## 11. How we know it works

We don't guess. We measure against grades the user already made by hand:

1. **Test set:** 30–50 clips from finished davigen projects, mixed on purpose: daylight, golden hour, night,
   backlight, drone, skin, snow or sand, forest.
2. **Compare:** render the user's grade and `DAVIGEN_AUTO` at the same frames, through nodes 01–04 only (the other
   nodes disabled for the comparison).
3. **Metrics:**
   - exposure difference in stops
   - white-balance angle in degrees
   - mean ΔE2000 in the display image
   - "flag correct": was a clip flagged where auto and hand grade differ most?
4. **Initial acceptance targets:**
   - median exposure difference under 1/3 stop
   - median WB angle under 2°
   - at least 80 % of clips needing no correction or only a small one
   - most of the large misses flagged
5. The thresholds in §10 are tuned on this set, so it keeps its value as a regression test.

## 12. Answers from the API spike

Step 01 ran on 2026-09-28 in Resolve 21.0.0.48 **Free**, from Workspace → Scripts, on a 6K V-Log HEVC clip
(Lumix S1II, 5952×3968, 25 fps) in a davigen project (DaVinci YRGB, timeline DWG/Intermediate). Everything below is
tested there unless it says *untested*.

**`SetCDL`**

- Works in Free, from the Edit page too (no page switch needed), on node 1 and on node 2. Returns `True`.
- On the pixels it is exact ASC CDL on the values entering the node: offset +0.07329 added exactly 0.07329 to every
  channel (std 1e-5), slope 1.2 multiplied by exactly 1.2. There is no colour management and no scaling in between.
- Saturation is a mix with a luma, `out = luma + sat · (in − luma)`, but the fitted luma weights are
  **(0.21, 0.70, 0.09)**, not Rec.709 (0.2126, 0.7152, 0.0722). The fit is tight (rms 7e-6 vs 4e-4 with Rec.709) but
  the frame had little colour; step 03 rechecks it on a colourful frame.
- On the wheels (Primaries → Color Wheels):

  | CDL written | Resolve shows |
  |---|---|
  | offset +0.0733 | Lift +0.03, Gain 1.08 (R, G, B), Offset wheel unchanged at 25.00 |
  | offset −0.0733 | Lift −0.04, Gain 0.93 |
  | slope 1.2 | Gain 1.20 |
  | sat 0.5 | Saturation 25.00 (display scale 50 = 1.0) |
  | any | **Lum Mix is set to 0.00** (a fresh node has 100.00) |

  So the values stay editable, but exposure shows up on Lift and Gain, not on Offset.
- *Untested:* power, and whether `SetCDL` resets other primaries in the node (Contrast, Pivot, Temp, Tint).

**Versions**

- `AddVersion("DAVIGEN_AUTO", 0)` creates a **copy of the current grade** (all six labelled nodes and their values)
  and **makes it the active version** right away.
- `GetVersionNameList(0)`, `GetCurrentVersion()` and `LoadVersionByName(name, 0)` work. The default local version is
  called `Version 1`.
- Writing into `DAVIGEN_AUTO` leaves the other version untouched: on the pixels, the user version still rendered its
  own +1 stop after `DAVIGEN_AUTO` had been set to −1 stop, and the item left on `DAVIGEN_AUTO` rendered −1 stop.
- *Untested:* `DeleteVersionByName`.

**Source range and snippets**

- `GetSourceStartFrame`, `GetSourceEndFrame`, `GetLeftOffset`, `GetRightOffset` exist. For a whole 348-frame clip
  they gave 0 / 347, so the end looks inclusive.
- `AppendToTimeline` with `startFrame` / `endFrame`: **the end is exclusive**. `(f, f)` fails and returns nothing,
  `(f, f + 1)` gives a 1-frame item showing exactly source frame *f* (0-based, checked against ffmpeg).
- The 1-frame item then reports `GetSourceEndFrame() = f + 1`, which contradicts the inclusive reading above. Until
  that is understood, davigen uses `GetSourceStartFrame()` and `GetDuration()` only.

**Render**

- TIFF codecs in Free: `RGB16`, `RGB8`, `XYZ16`. `RGB16` is uncompressed, so no extra library is needed to read it.
- `FormatWidth` / `FormatHeight` of 480 × 320 are accepted for a 3240 × 2160 timeline.
- 27 frames of 6K HEVC, each from a different place in the clip: 2.7 s, **0.1 s per frame**.
- The TIFF holds the camera code values unchanged: it matches ffmpeg's full-range decode of the same frame within
  0.001 on average (the clip is tagged full range). No data-level or colour conversion.
- The render settings on the Deliver page stay changed afterwards (the API can't read them back). The job is removed.

**Markers**

- `TimelineItem.AddMarker(frame, color, name, note, duration, customData)` takes custom data;
  `GetMarkerByCustomData` and `DeleteMarkerByCustomData` work, and a marker without custom data survives the delete.
