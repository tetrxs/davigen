# 07 · Media checks on import

**Goal:** on import, audio that doesn't match the project rate or carries a picture is converted to 48 kHz /
24-bit WAV; clips whose frame rate differs from the timeline get a note; heavy codecs are marked (concept §8).

**References:**

- concept §8
- [`davigen/scanner.py`](../../../davigen/scanner.py) `ClipInfo`, `VIDEO_EXT`
- [`davigen/media_pool.py`](../../../davigen/media_pool.py) `import_group` (Comments, Keywords)
- [`davigen/edit/run.py`](../../../davigen/edit/run.py) (music import), [`davigen/server.py`](../../../davigen/server.py)
  `AUDIO_EXT`
- [`davigen/edit/decode.py`](../../../davigen/edit/decode.py) (ffmpeg / ffprobe)

## How it is developed

Test-first:

1. `media_checks.probe(path)`: one ffprobe call → codec, profile, pixel format, frame rate, timecode, audio streams
   (sample rate, channels), attached pictures. Parsing tested on stored ffprobe JSON fixtures
   (`tests/fixtures/ffprobe/`: Lumix HEVC 4:2:2, DJI HEVC 4:2:0, FLAC with cover, iPhone MOV).
2. **Audio:** `needs_conversion(probe, cfg)`, `conversion_command(src, dst, cfg)` (soxr, 24-bit, no picture, tags
   kept), `convert(src)` writing `.part` then renaming; a check that the ffmpeg in use has soxr (a tiny conversion
   of a generated tone), otherwise a clear message and no conversion.
3. **Add footage accepts audio files:** the scanner lists them separately; they go to `01_MEDIA/90_AUDIO/` through
   the journaled transfer, are converted if needed, and the WAV is imported into `04_AUDIO/…`. Music from Edit
   assist as decided in question 5.
4. **Frame rate:** `fps_note(clip_fps, timeline_fps) -> (category, text, keyword)` for the three cases of the
   concept; written by `media_pool` (Comments appended, keyword added) and listed in the report.
5. **Heavy:** `is_heavy(probe, cfg)`; keyword `HEAVY`; step 05's order uses it.
6. `Check & repair` (04) runs the same checks on clips already in the project, as *fix* (audio) and *info* rows.
7. **Docs:** README (What it does), WORKFLOW §7 (Adding footage) and a note on frame rates in §4.

## How it is checked

- Tests: every rule on the fixtures; note texts for 59.94/25, 50/25, 23.976/24; a generated 44.1 kHz FLAC with a
  cover converts to a 48 kHz 24-bit WAV with no video stream and the same duration (± 1 ms); the original is
  untouched (hash before and after).
- **In Resolve**, on the MARSEILLE copy: *Add footage* with the FLAC from the trip: the WAV is in the Media Pool, the
  FLAC next to it on disk, no crackle on the Edit page; the DJI clips carry the note and the keyword; a Smart Bin on
  `FPS_5994` finds them. Written down under *Result*.
