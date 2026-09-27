# 07 · Writing into Resolve

**Goal:** put the corrections into Resolve without ever losing the user's work (concept §8).

**File:** `davigen/basic/write.py`. Tests go in `tests/test_basic_write.py`, with mocked API objects like the
existing tests.

The details depend on step 01 (version behaviour, marker API).

## Tasks

1. **Preconditions per item:**
   - It has davigen group metadata and is in its group; if not, call `color.assign` first.
   - It has the node structure; if not, call `color.apply_clip_structure` first.
   - Otherwise skip it with the flag `not in its colour group`.
2. **Version:**
   - If `DAVIGEN_AUTO` doesn't exist: `AddVersion("DAVIGEN_AUTO", 0)` and load it.
   - If it exists: skip the item, unless *Recompute all* is set.
   - If step 01 shows that a new version starts empty, apply `CLIP_STRUCTURE.drx` in it first.
3. **Nodes by label:** map `01_EXPOSURE`, `02_WHITE_BALANCE`, `03_CONTRAST` and `04_SATURATION` to their indices
   via `GetNodeLabel`. A missing label is skipped and reported, never written to another node.
4. **SetCDL** per node, with values formatted as Resolve expects (space-separated strings). Check the return
   value.
5. **Markers:**
   - Remove the old ones with custom data `davigen-basic`.
   - Add one per flagged item, at the item's first frame. Colour: yellow for low confidence, red for
     `not in its colour group`.
   - Name `davigen: <main flag>`, note = all flags with values.
6. **Record:** write `00_ADMIN/PROJECT_INFO/basic_correction/<timeline>.json`, holding for every item:
   - item id, clip path and used range
   - measurement, correction, scene and hero
   - what was written, or why nothing was
7. **Dry run:** steps 1, 5 and 6 only. Nothing is created or written in the grades.

## Tests

- No `DAVIGEN_AUTO` version: one is added and written.
- An existing version without *Recompute all*: nothing is written.
- Nodes are found by label even when the order differs, and a missing label is reported.
- A failed `SetCDL` becomes a warning, not an exception, and the rest of the timeline continues.
- A dry run calls none of `AddVersion`, `LoadVersionByName` or `SetCDL`.
- Old davigen markers are removed, and user markers are never touched.

## Check in Resolve

On a copy of a real project:

- Run it, then check that the user version is unchanged, that `DAVIGEN_AUTO` shows the values, and that switching
  between them works.
- Run again: nothing changes. *Recompute all*: values are rewritten.

## Done when

The tests pass, and the Resolve check above was done on a real project.
