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
| 1 | Resolve version, Free or Studio | context for everything else |
| 2 | `TimelineItem.GetSourceStartFrame` / `GetSourceEndFrame` exist | sampling the used range (§4) |
| 3 | `AppendToTimeline` with a single-frame `startFrame`/`endFrame` gives a 1-frame item | analysis timeline |
| 4 | `GetNodeGraph`, node count and labels after applying `CLIP_STRUCTURE.drx` | find nodes by label |
| 5 | `SetCDL` on node 1 with known values returns True | the core question |
| 6 | `AddVersion("DAVIGEN_AUTO", 0)`, then `GetVersionNameList` and `LoadVersionByName` | safe writing |
| 7 | Node count and labels in the new version | does the structure carry over? |
| 8 | `AddMarker` with custom data, `GetMarkers`, `DeleteMarkerByCustomData` | flags |
| 9 | Render the snippets as a 16-bit TIFF sequence at 480 px, timed | speed and format of the analysis |
| 10 | Two TIFF renders of the same frame, one with a known CDL | how CDL values act on pixels |

## What to look at by hand

The script can't see the UI, so four things are yours:

- **Check 5:** open the 2nd to 4th clip on `ZZ_DAVIGEN_SPIKE` on the Color page. Note what Primaries → Offset,
  Gain, Gamma and Saturation show for the values the script printed.
- **Check 6:** which version is active afterwards, and whether the grade from before is still in the other one.
- **Check 9:** the time per frame, as printed.
- **Check 10:** the script compares both TIFFs itself, if numpy is available (davigen's runtime has it). It prints
  whether the pixel difference matches `offset` in code values. That answers "in which space".

## Done when

- `report.txt` is back here, and the answers are written into §12 of the concept.
- Steps 03 and 07 are updated to match.
- If `SetCDL` fails in Free: stop and look for alternatives before continuing. Candidates are DRX templates with
  the values patched in (as `drx.py` does for the CST), or LUTs per clip.
