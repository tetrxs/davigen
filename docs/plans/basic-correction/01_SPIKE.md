# 01 · API spike in Resolve

**Goal:** answer the open questions of the concept (§12) on the real Resolve, before anything else is built.

**Script:** [`scripts/spike_basic_correction.py`](../../../scripts/spike_basic_correction.py)

## How to run it

1. Open a davigen project in Resolve. The current timeline needs at least one davigen clip, preferably 6K V-Log
   HEVC so the render timing means something.
2. Copy the script to
   `~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/`.
3. Start it with **Workspace → Scripts → spike_basic_correction**.
4. Open **Workspace → Console** to watch the output.

**What the script touches:**

- It works only on its own scratch timelines, `ZZ_DAVIGEN_SPIKE*`.
- `ZZ_DAVIGEN_SPIKE` is kept so you can inspect it on the Color page. The next run replaces it; delete it by hand
  when you're done.
- It leaves one folder of rendered TIFFs in `~/Movies/davigen_spike/`.
- Your timelines and grades stay untouched. The render job it adds is removed again.
- The render settings on the Deliver page are changed for this project, because the API can't read them back.
  Use a copy of a project if that matters.

**Result:** the report is saved to `~/Movies/davigen_spike/report.txt`, and each check prints `OK` or `FAIL` with
details.

## What it checks

| # | Check | Why |
|---|---|---|
| 1 | Resolve version, Free or Studio, project colour settings | context for everything else |
| 2 | `TimelineItem.GetSourceStartFrame` / `GetSourceEndFrame` exist | sampling the used range (§4) |
| 3 | `AppendToTimeline` with a single-frame `startFrame`/`endFrame` gives a 1-frame item | analysis timeline |
| 4 | `GetNodeGraph`, node count and labels after applying `CLIP_STRUCTURE.drx` | find nodes by label |
| 5 | `SetCDL` on node 1 (and node 2) with known values returns True | the core question |
| 6 | `AddVersion("DAVIGEN_AUTO", 0)`, `GetVersionNameList`, `LoadVersionByName`, `SetCDL` inside it | safe writing |
| 7 | Node count and labels in the new version | does the structure carry over? |
| 8 | `AddMarker` with custom data, `GetMarkerByCustomData`, `DeleteMarkerByCustomData` | flags |
| 9 | Render the snippets as a 16-bit TIFF sequence at 480 px, timed | speed and format of the analysis |
| 10 | TIFF renders of the same frame with known CDLs, compared per pixel | how CDL values act on pixels, and whether writing `DAVIGEN_AUTO` leaves the user version alone |

The first seven items on `ZZ_DAVIGEN_SPIKE` show the same source frame: 1 base, 2 offset +1 stop, 3 slope 1.2,
4 saturation 0.5, 5 and 6 the version test (+1 stop in the user version, −1 stop in `DAVIGEN_AUTO`; item 5 is
switched back to the user version, item 6 stays on `DAVIGEN_AUTO`), 7 offset +1 stop on node 2. Twenty more
items spread over the clip give the render timing.

## What to look at by hand

The script can't see the UI, so four things are yours:

- **Check 5:** open the 2nd to 4th clip on `ZZ_DAVIGEN_SPIKE` on the Color page. Note what Primaries → Offset,
  Gain, Gamma and Saturation show for the values the script printed.
- **Check 6:** on items 5 and 6, which version is active, and whether `DAVIGEN_AUTO` started as a copy of the
  grade before it or empty. The pixel comparison in check 10 answers whether the user version stayed unchanged.
- **Check 9:** the time per frame, as printed.
- **Check 10:** the script compares both TIFFs itself, if numpy is available (davigen's runtime has it). It prints
  whether the pixel difference matches `offset` in code values. That answers "in which space". Without
  `tifffile` it reads uncompressed TIFFs itself, so it picks an uncompressed 16-bit codec.

## Done when

- `report.txt` is back here, and the answers are written into §12 of the concept.
- Steps 03 and 07 are updated to match.
- If `SetCDL` fails in Free: stop and look for alternatives before continuing. Candidates are DRX templates with
  the values patched in (as `drx.py` does for the CST), or LUTs per clip.

## Result (2026-09-28)

Run in Resolve 21.0.0.48 Free on a 6K V-Log HEVC clip. All ten checks passed; the report is summarised in §12 of
the concept. The answers that change the plan:

- `SetCDL` works in Free, on nodes 1 and 2, from the Edit page, and acts on the pixels as exact ASC CDL. It shows up on the
  Lift/Gain wheels and sets Lum Mix to 0.
- `AddVersion` copies the current grade and activates the copy. The user version stays unchanged on the pixels.
- `AppendToTimeline` treats `endFrame` as exclusive. Used ranges come from `GetSourceStartFrame` + `GetDuration`.
- The TIFF render is uncompressed `RGB16`, holds the camera code values unchanged, and takes 0.1 s per frame.
- Saturation mixes with luma weights (0.21, 0.70, 0.09), to be confirmed on a colourful frame in step 03.

The run was done on a real project instead of a copy. It left the timeline `ZZ_DAVIGEN_SPIKE` and changed the
Deliver page render settings to TIFF 480 × 320; nothing else in the project was touched.
