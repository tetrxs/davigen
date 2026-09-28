# 03 · Frame sampling and cache

**Goal:** get small, exact frames of every clip in DaVinci Intermediate, cheaply enough to run on a whole trip.

**File:** `davigen/basic/sampling.py`. Tests go in `tests/test_basic_sampling.py`, with the Resolve parts mocked.
No new dependency (see task 5).

## Tasks

1. **Choose sample frames** per timeline item:
   - The used source range is `GetSourceStartFrame()` up to `GetSourceStartFrame() + GetDuration()`, end exclusive.
     Step 01 found `GetSourceEndFrame` inconsistent between a whole clip and a snippet, so it isn't used. Without a
     start frame, use the whole clip.
   - Count: `min + used_seconds / per_seconds`, capped at `max`.
   - Spread evenly, avoiding the first and last 5 frames (fades, stabiliser start-up).
2. **Cache lookup:**
   - The key is `(file path, file size, mtime, frame)`.
   - Cached samples are reused.
   - The cache lives in `03_WORK/ANALYSIS/`, one `.npz` per media clip.
3. **Analysis timeline** for uncached samples:
   - `ZZ_DAVIGEN_ANALYSIS` holds one-frame snippets, with no group and no grade:
     `AppendToTimeline([{"mediaPoolItem", "startFrame": f, "endFrame": f + 1, "mediaType": 1}])`. The end is
     exclusive, `(f, f)` fails (step 01).
   - Timeline resolution: `analysis_width`, keeping the aspect ratio.
   - The helper is modelled on `color.Baker` (create, use, always delete, restore the previous timeline).
4. **Render:**
   - Format `tif`, codec `RGB16` (uncompressed 16-bit) to a temp folder, as an image sequence. Files are numbered
     from the timeline start timecode (`spike00090000.tif` at 25 fps), so sort by name.
   - The job is removed from the queue afterwards.
   - The API can't read render settings back, and step 01 confirmed they stay changed. Afterwards, load the
     project's davigen master preset (`LoadRenderPreset`, name from `formats.deliveries`) if it exists, and
     say so in the report.
   - Wait with `IsRenderingInProgress`, with progress reported to the `Reporter`.
5. **Read and convert:**
   - Read each TIFF with a small reader for uncompressed baseline TIFF (the one in the spike script, tested) and
     map it back to its clip and frame by sequence number. A compressed file raises a clear error.
   - Apply the clip's input LUT (the one in its group's Pre-Clip graph) via `pipeline.apply_lut`.
   - Store a float16 thumbnail at about 96 px wide, plus the frame number, in the cache.
6. **Clean up:** delete the TIFFs and the scratch timeline, also on errors, and in the `finally` of the flow.

## Tests

- Sample choice: counts, spread, edges, short clips (fewer frames than samples), fallback without source range.
- Cache: a hit skips the render, and a changed mtime invalidates the entry.
- Sequence mapping: TIFF number → (clip, frame) for a mocked timeline of three clips.
- Reading a small uncompressed 16-bit TIFF written by the test itself (struct, no `tifffile`), in both byte
  orders.

## Check in Resolve

Run on a real project with about 20 clips:

- time per frame
- the scratch timeline is gone afterwards
- render settings are as before
- a sample's thumbnail matches a Resolve still of the same frame, graded with the group only, within 1 %
- the saturation luma weights from step 01, (0.21, 0.70, 0.09), refitted on a colourful frame
- Resolve's 3D LUT interpolation (Project Settings → Color Management → 3D Lookup Table Interpolation): the simulator
  uses trilinear, Resolve's default. Trilinear bends the grey axis slightly between grid points (up to 0.002 in the
  65-point output LUT at 18 % grey); tetrahedral wouldn't. If the project uses tetrahedral, add it to `pipeline.py`

## Done when

A function `samples_for(timeline) -> {item_id: [ndarray DI thumbnails]}` works on a real project and the cache is
reused on a second run.
