# Concept: Edit Assist

**Status:** built and tested against a fake Resolve; tuned on the MARSEILLE_2026 footage (2026-09-28). The check in Resolve is open.

davigen already turns a card of footage into an organised, colour-managed project with a first grade. Edit Assist
takes the next step: it watches every clip once so the editor doesn't have to, and hands over **selects** and a
**first rough cut**. It is for travel and documentary footage, where most of a shoot is walking, framing and
pocketing the camera, and the good moments are short.

- [1. What it does](#1-what-it-does)
- [2. Watching a clip](#2-watching-a-clip)
- [3. Selects](#3-selects)
- [4. Music](#4-music)
- [5. Rough cut to music](#5-rough-cut-to-music)
- [6. How it gets into Resolve](#6-how-it-gets-into-resolve)
- [7. Configuration and limits](#7-configuration-and-limits)

---

## 1. What it does

| Feature | Result in Resolve |
|---|---|
| **Selects** | Every clip gets markers: green for good stretches, red for unusable ones (camera in the pocket, lens covered, blur, heavy shake), blue where someone speaks. A selects timeline `TL_00_SELECTS_AUTO` holds the good stretches in shooting order. |
| **Beat markers** | A music clip gets markers on its beats, a stronger colour on every bar, and one per section change (verse, drop, calm part). |
| **Rough cut** | `TL_02_EDIT_AUTO_v001`: the music on A1, and on V1 the best stretches of the shoot, in shooting order, each cut on a beat, as long as the music. |

Nothing is deleted or trimmed in the source clips. Markers carry the custom data `davigen-edit`, so a new run
replaces its own markers and never touches the user's.

## 2. Watching a clip

Each clip is decoded once at low resolution (320 px wide, 5 frames per second) with ffmpeg, using the Mac's
hardware decoder. Per frame davigen measures:

| Measure | How | Why |
|---|---|---|
| **Motion** | global shift to the previous frame by phase correlation (FFT) | panning is fine, jitter is shake |
| **Shake** | the high-frequency part of the motion (motion minus its 1-second moving average) | hand-held walking, bumps |
| **Sharpness** | variance of the Laplacian, normalised by the frame's contrast | focus misses, motion blur |
| **Brightness / detail** | mean luma, and edge density | black frames, lens covered, pocket shots |
| **Change** | histogram difference to the previous frame | cuts inside a clip (camera paused), whip pans |

Audio is decoded as 16 kHz mono: loudness per 0.2 s and a simple voice detector (energy in the speech band and
its modulation at the syllable rate, 2–8 Hz).

All of this is cached per clip (`03_WORK/ANALYSIS/edit/`), so re-running with other thresholds is instant.

## 3. Selects

Per frame a **usability score** from 0 to 1 combines sharpness, shake and exposure; unusable frames (black,
covered, extreme blur) are 0. Sharpness counts **relative to the same shot a few seconds around** (±5 s): texture
differs so much between scenes that an absolute or whole-clip scale calls a calm sea "blurred" next to a sunlit
cliff in the same drone flight. Blur is a dip against its neighbours. Then:

1. The score is smoothed over 1 second.
2. **Unusable** stretches: score below 0.2 for at least 1 s → red marker spanning them.
3. **Good** stretches: score above 0.6 for at least 2 s → green marker. Each gets a rating from its mean score,
   motion calm and length.
4. **Speech**: voice for at least 1.5 s → blue marker (an A-roll candidate).
5. The selects timeline takes every good stretch, at most `max_select_seconds` from its middle, in recording
   order.

## 4. Music

Beat tracking without dependencies (Ellis 2007, the method behind librosa's `beat_track`):

1. Onset strength: spectral flux of a log-magnitude mel spectrogram (numpy FFT).
2. Tempo: the autocorrelation of the onset envelope, weighted towards 120 BPM, in 60–180 BPM.
3. Beats: dynamic programming that picks onset peaks close to the tempo grid.
4. Bars: the beat phase with the strongest onsets every four beats.
5. Sections: Foote novelty on the bars' self-similarity (mean log-mel spectrum per bar), at least 8 bars apart.
   A section's energy is relative to the song: the share of its bars that are quieter.

Checked against librosa on two real tracks (Kevin MacLeod, CC BY): the same tempo (96.0 vs 95.7, 102.3 vs
103.4 BPM), 98 % and 88 % of the beats within 70 ms of librosa's; 7 and 8 sections.

## 5. Rough cut to music

- The music's bars are the cut grid. Calm sections get longer shots (two bars), energetic ones shorter (one bar,
  or half a bar at high tempo).
- The good stretches are used in recording order, the best-rated first when there are more than fit; each shot
  starts where its stretch is calmest.
- Drone and wide shots are preferred at section starts (they establish), speech stretches are skipped (their
  sound would fight the music).
- The result is a starting point: every edit is a normal Resolve edit that can be trimmed, swapped or deleted.

## 6. How it gets into Resolve

Verified in step 01 of Basic Correction: `AppendToTimeline` with `startFrame`/`endFrame` (end exclusive) builds
timelines from ranges. Markers on Media Pool clips use `MediaPoolItem.AddMarker` with custom data (on timeline items
this was verified; on Media Pool items it is documented but *untested* until run in Resolve). Placing a clip at a
given timeline position uses the clip info key `recordFrame` (*untested*); without it clips are appended in order,
which is all the rough cut needs.

## 7. Configuration and limits

All thresholds are in `[edit_assist]` in `config/workflow.toml`. ffmpeg is needed for decoding (Homebrew's is
found automatically); without it Edit Assist says so and does nothing. Limits: no face or object recognition, and the rough
cut doesn't judge content, only craft (sharp, steady, well exposed, varied).

**Transcription** uses Whisper large v3 turbo through mlx-whisper (Apple's MLX, Apple Silicon only) in a separate
process with davigen's own Python. It only sees the speech stretches, merged when less than a second apart and
padded by 0.3 s, and keeps only segments Whisper itself trusts (average log-probability above −1, no-speech
probability below 0.6, no known silence hallucinations like "Thank you."). On MARSEILLE_2026 it transcribed a
German and a French conversation correctly and dropped two stretches the voice detector had taken from wind.

## 8. Tuning on real footage

`scripts/edit_offline.py` on MARSEILLE_2026 (57 clips, 35 min: walking, car windows, drone, beach). The first
version called 13.4 min unusable, much of it good drone footage and mirror shots with a soft background, because
sharpness was judged against each clip's sharpest 10 %. Judged against ±5 s of the same shot: 22.7 min good,
4.6 min unusable (the camera pointing at the sand while walking, pocket shots, whip pans), 2.4 min speech. The
drone flights are good apart from take-off and landing. Watching took about a third of the footage's running time
(hardware decoding, two clips at a time).

On "Carefree" (96 BPM, 3:25) the rough cut from the Marseille footage has 58 shots, 1.6–5.0 s (median one bar),
from 29 clips, at most three per clip, 3:03 long before the footage ran out.
