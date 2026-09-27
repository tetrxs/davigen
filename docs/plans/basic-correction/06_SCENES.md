# 06 · Scenes and matching

**Goal:** balance, then match, so a sequence keeps its mood and cuts together (concept §6).

**File:** `davigen/basic/scenes.py`. Tests go in `tests/test_basic_scenes.py`.

## Tasks

1. **Group into scenes:**
   - Sort by recording time (`created`, stored in the clip metadata at import; add it there if it isn't yet),
     falling back to timeline order.
   - A new scene starts after a gap of more than `gap_minutes`.
   - Within a block, also split where EV100 jumps by more than 3, or the illuminant CCT jumps by more than
     1500 K, between neighbouring clips with high WB confidence.
2. **Hero:** the highest overall confidence × used duration.
3. **Scene reference:** the hero's corrected exposure level (display luminance of its midtones) and its corrected
   white point.
4. **Pull:**
   - High confidence: final = own correction moved `pull_to_scene` towards what matches the reference.
   - Low confidence: WB comes entirely from the scene. Exposure comes from the scene when the exposure confidence
     is low.
   - Flag `matched to scene` so the user knows where a value came from.
5. **Cross-camera:** no special case. Group membership only matters for which input LUT was used, which the
   samples already include.

## Tests

- Three clips, the same scene, one with a dominant green: the green one inherits the scene WB.
- Two blocks 30 minutes apart form two scenes.
- A jump from day to night inside 5 minutes (entering a building) splits the scene.
- A single-clip scene is left unchanged.
- Pull strength 0 changes nothing, and 1 matches the reference exactly.

## Done when

`match_scenes(corrections, clip_meta, cfg) -> corrections` works on the synthetic cases, and the record shows the
scene and hero of every clip.
