# Working in a davigen project

This guide covers what to do in Resolve after davigen has built a project: where things are, which timeline is
for what, how grading is laid out, and how to deliver. The names below are the defaults from
`config/workflow.toml`, with a 3:2, 25 fps master project called `ITALY_2026` as the example.

- [1. On disk](#1-on-disk)
- [2. In the Media Pool](#2-in-the-media-pool)
- [3. Timelines](#3-timelines)
- [4. Editing, step by step](#4-editing-step-by-step)
- [5. Grading](#5-grading)
- [6. Delivering](#6-delivering)
- [7. Adding footage later](#7-adding-footage-later)
- [8. Naming and versions](#8-naming-and-versions)
- [9. One-time Resolve settings](#9-one-time-resolve-settings)

---

## 1. On disk

```
ITALY_2026/
├── 00_ADMIN/
│   ├── PROJECT_INFO/        davigen.json (format, groups, cameras) + transfer journals
│   ├── VERSION_HISTORY/
│   └── REFERENCES/          mood boards, briefs, scripts
├── 01_MEDIA/
│   ├── 01_LUMIX_S1II/       original camera files, one folder per camera (original file names)
│   ├── 02_DJI_AIR3/
│   ├── 90_AUDIO/            external audio recorders
│   ├── 91_STILLS/           photos
│   └── 92_STOCK/            licensed stock
├── 02_RESOLVE/
│   ├── 01_PROJECT_FILES/    .drp exports (File → Export Project) for archiving
│   ├── 02_BACKUPS/
│   └── 03_GALLERY/          stills and PowerGrades of this project
├── 03_WORK/
│   ├── PROXIES/
│   ├── CACHE/
│   └── OPTIMIZED_MEDIA/
├── 04_ASSETS/
│   └── MUSIC/  SFX/  GRAPHICS/  LOGOS/  FONTS/  LUTS/  VFX/
├── 05_EXPORTS/
│   ├── 01_DRAFTS/           rough exports for yourself
│   ├── 02_REVIEW/           exports for feedback
│   ├── 03_16X9/  04_9X16/   deliveries (one folder per delivery you picked)
│   └── 05_MASTER/
└── 99_ARCHIVE/
```

**The rule:** anything the project uses lives inside this folder. Because everything is linked relative to it,
you can move the whole folder to another drive or archive it, and relinking stays trivial.

The Resolve project itself sits in Resolve's project database under `VIDEO_PROJECTS/ACTIVE`. When a project is
finished, move it to a sibling folder such as `VIDEO_PROJECTS/ARCHIVE` in the Project Manager and export a `.drp`
into `02_RESOLVE/01_PROJECT_FILES`.

## 2. In the Media Pool

| Bin | Use |
|---|---|
| `00_ADMIN` | Reference clips and notes. |
| `01_FOOTAGE/<CAMERA>` | All clips from one camera, filled by davigen. Leave these bins as they are and work from selects. |
| `02_SELECTS/01_A_ROLL … 05_FINAL_SELECTS` | Your picks. Drag or copy clips here while you review (see step 2 below). |
| `03_TIMELINES/01_ASSEMBLY … 04_DELIVERABLES` | Every timeline lives in the bin for its stage. |
| `04_AUDIO/…` | Music, VO, dialogue, SFX and ambience. |
| `05_GRAPHICS/…` | Titles, lower thirds, logos and VFX. |
| `06_EXPORTS` | Rendered files you re-import, for example for review. |
| `07_POWERGRADES` | Place for PowerGrade stills (the Gallery has a PowerGrade album too). |
| `08_REFERENCES` | Reference films and frames for matching. |

**Clip metadata:** every clip davigen imports carries its camera, log profile and color group, both in its metadata
(Keywords, Camera) and in a davigen field. That is how *Assign groups & nodes* finds the right group for a clip on
any timeline. Each camera also gets its own **clip color**.

## 3. Timelines

| Timeline | Resolution | Purpose |
|---|---|---|
| `TL_01_ASSEMBLY_3X2_25_v001` | master | All footage in shooting order, filled by davigen (new footage goes to its end). Basic correction measures here; Edit assist's markers show on its clips. Watch it, mark and pick. Don't edit here. |
| `TL_02_EDIT_3X2_25_v001` | master | **Where you cut**: story, timing, music. Edit assist's rough cut lands here while it's empty; once you've started cutting, a new rough cut goes into `…_v002`, so your edit is never touched. |
| `TL_03_MASTER_3X2_25_v001` | master | The picture-locked, graded, mixed film. It is the source of every delivery. |
| `TL_04_DELIVERY_16X9_25_v001` | 3840 × 2160 | The master reframed for 16:9. |
| `TL_05_DELIVERY_9X16_25_v001` | 1080 × 1920 | The master reframed for 9:16. |

Basic correction's `DAVIGEN_AUTO` reaches every timeline: Resolve keeps a grade per timeline clip, so davigen
writes it onto the same clips wherever they are used (*On every timeline*, and automatically for the rough cut).

These are the tracks on every timeline:

| Video | Audio |
|---|---|
| V1 `MAIN`: the story shots | A1 `PRODUCTION`: camera sound you keep |
| V2 `SECONDARY`: cutaways over MAIN | A2 `DIALOGUE_VO`: voice over and dialogue |
| V3 `BROLL` | A3 `MUSIC` |
| V4 `ADJUSTMENT`: adjustment clips for effects on everything below | A4 `SFX` |
| V5 `TITLES` | A5 `AMBIENCE` |
| | A6 `CAMERA_SCRATCH`: reference sound, usually muted |

**Delivery timelines** fill the frame by cropping the master: *Scale full frame with crop* is set on each one.
You then reframe shot by shot in the Inspector (Zoom and Position). A 3:2 open-gate master leaves room to move up
and down for 16:9 and plenty of room left and right for 9:16.

## 4. Editing, step by step

1. **Proxies (once):**
   1. In `01_FOOTAGE`, select all clips.
   2. Right-click → **Generate Proxy Media**.

   Proxies are half resolution (the project is set up that way). First point them at `03_WORK/PROXIES`, under
   Project Settings → Master Settings → Working Folders → *Proxy generation location*. Resolve doesn't keep this
   path when a script sets it. Playback → **Proxy Handling → Prefer Proxies** keeps 6K open gate smooth on a
   laptop.
2. **Let davigen watch the footage first (Edit assist):** on the home screen press **Selects**. davigen
   watches every clip once (about three times faster than real time) and marks it in the Media Pool:

   | Marker | Means |
   |---|---|
   | green `davigen: good 0.82` | a steady, sharp, well exposed stretch; the number rates it |
   | red `davigen: blurred` / `shaking` / `dark / covered` / `whip pan` | skip this |
   | blue `davigen: speech` | someone talks: an A-roll candidate; with *Transcribe speech* the marker's note holds what was said |

   The markers belong to the clips, so they show in the Assembly and every other timeline; no extra timeline is
   made. **Rough cut to music** (song, length and pace chosen on the Edit assist page) marks the song's bars and
   sections and cuts the best stretches in shooting order on the bar into `TL_02_EDIT` – while it is empty, else
   into its next version – with the song on the MUSIC track, graded like the Assembly, plus a preview video to
   watch in davigen. A new run replaces only davigen's own markers. Needs ffmpeg (`brew install ffmpeg`).

   **Transcribe speech** (on by default, Apple Silicon): Whisper (large v3 turbo) writes what is said, in any
   language, into the blue markers, an `.srt` per clip in `03_WORK/TRANSCRIPTS` (import it as subtitles) and
   `00_ADMIN/PROJECT_INFO/transcripts.md`, a searchable list of everything said on the trip. Where Whisper hears no
   words (wind, traffic) the speech marker is dropped. The first run installs `mlx-whisper` into davigen's Python and
   downloads the model (~1.6 GB); after that a minute of speech takes a few seconds.
3. **Pick your shots on the assembly timeline:**
   1. Open `TL_01_ASSEMBLY` and play through it; the green markers point at the good stretches.
   2. Mark good moments with **I**/**O** and **F9** (insert) into your selects, or flag and color them.

   Put the picks into `02_SELECTS`.
4. **Cut:** build the film on `TL_02_EDIT` (or start from the rough cut), story first, on V1 `MAIN`.
5. **Picture lock:**
   1. Duplicate the edit (right-click → *Duplicate Timeline*).
   2. Rename the copy to `TL_03_MASTER_…` and move it into `03_TIMELINES/03_MASTER`.

   Alternatively, copy everything into the empty master timeline davigen made.
6. **Run *Assign groups & nodes*** in davigen. Resolve stores color groups and grades **per timeline clip**, so
   clips in a timeline you built yourself start out ungraded. This puts every clip in its camera group and gives
   each clip the six-node structure. It never touches clips you already graded.
7. **Grade** on the master timeline (see below).
8. **Deliver** (see below).

## 5. Grading

Every clip goes through three graphs. You can switch between them at the top of the Color page's node editor:

```
Group Pre-Clip           Clip (your grade, per shot)              Group Post-Clip
─────────────            ──────────────────────────               ───────────────────────────────
[input LUT]      ─▶      01_EXPOSURE ─▶ 02_WHITE_BALANCE ─▶        [your look]  ─▶  [output LUT]
camera log → DWG         03_CONTRAST ─▶ 04_SATURATION ─▶           film emulation,  DWG → Rec.709
                         05_SECONDARIES ─▶ 06_FINISH               creative LUTs    Gamma 2.4
```

- **Group Pre-Clip:** converts each camera's log into DaVinci Wide Gamut / Intermediate. Leave it alone. Every
  camera in the group gets the same, correct starting point.
- **Clip:** your per-shot work, done in DaVinci Wide Gamut:

  | Node | What goes in it |
  |---|---|
  | `01_EXPOSURE` | Offset or HDR wheels exposure. Get the brightness right first. |
  | `02_WHITE_BALANCE` | Temperature and tint, or the white-balance picker. |
  | `03_CONTRAST` | Contrast and pivot, or the Contrast/Pivot controls. |
  | `04_SATURATION` | Saturation and color density. |
  | `05_SECONDARIES` | Qualifiers, windows, skin, sky. |
  | `06_FINISH` | Anything last: vignette, sharpening, NR (NR is often better as the first node). |

  Empty nodes cost nothing. Use them in order and grades stay easy to read and copy.
- **Group Post-Clip:** where your **look** goes, before the output node:
  1. Select the output node.
  2. Choose **Color → Nodes → Add Serial Before Current**.

  A look placed here applies to the whole camera group. For a look across all cameras, save it as a PowerGrade
  and apply it to each group's post-clip graph.

### Basic correction: the first pass, measured

davigen can fill `01_EXPOSURE` to `04_SATURATION` for you. It renders a few small frames of every clip through
Resolve, measures them in the working space and writes the values into a **new grade version called
`DAVIGEN_AUTO`**. Your own version is never changed.

- **Start it:** tick *Basic correction* when you create the project (on by default), press **Basic correction**
  on the home screen for the current timeline, or choose **Workspace → Scripts → davigen Basic Correction**.
  *Assign groups & nodes* also corrects clips that just got the node structure, when the project has it on.
- **What it does per clip:**

  | Node | Measured | Written |
  |---|---|---|
  | `01_EXPOSURE` | log-average brightness, skin (60–70 IRE), scene brightness from the camera's ISO, aperture and shutter | offset |
  | `02_WHITE_BALANCE` | the light, from four estimators; its colour temperature and green/magenta | green/magenta fully, warm and cool only partly |
  | `03_CONTRAST` | black and white points after the output LUT | contrast around middle grey |
  | `04_SATURATION` | colourfulness after the output LUT | saturation, never boosting what is already colourful |

- **Scenes:** clips shot within a few minutes in the same light form a scene. The most confident, longest clip is
  its hero; the others are pulled towards it, and a clip that can't judge its light (a close-up of a green bush)
  takes the scene's white balance.
- **Where you see it:** on the Color page the values sit in the primaries of nodes 01–04: exposure shows on the
  **Lift and Gain** wheels, saturation on *Sat*, and Resolve sets *Lum Mix* to 0 for these nodes. Everything stays
  editable.
- **Before/after:** right-click a clip → **Local Versions** → `Version 1` / `DAVIGEN_AUTO`.
- **Unsure clips** get a yellow marker (*davigen: dominant colour*, *mixed light*, *clipped highlights*, …); clips
  outside their color group a red one. The home screen shows a report of every clip; click a row to jump to it.
- **Again:** a second run only corrects clips without `DAVIGEN_AUTO` and reuses the measured frames.
  *Recompute all* rewrites every `DAVIGEN_AUTO` (changes you made inside it are lost). *Dry run* measures and
  sets markers without touching any grade.
- **Where it keeps things:** measured frames in `03_WORK/ANALYSIS/`, every value and the reason for it in
  `00_ADMIN/PROJECT_INFO/basic_correction/<timeline>.json`.

It is a first pass, not a look: silhouettes, night shots and golden hour stay what they are. How it works is in
[docs/concepts/BASIC_CORRECTION.md](concepts/BASIC_CORRECTION.md); every threshold is in
`config/workflow.toml` under `[basic_correction]`.

**Matching cameras:** because each camera is converted into the same working space first, a V-Log clip and a
D-Log M clip usually need only exposure and white balance to match. Grade the hero camera first, then match the
others to it with the scopes (Waveform, Parade) using a split screen.

**Copying grades:** middle-click a graded clip in the thumbnail timeline to copy its clip grade to the current
clip. Only the clip graph is copied; the group graphs stay per group.

**Scopes:** Workspace → Video Scopes. Aim for skin at roughly 60–70 IRE on the waveform (IRE is the waveform's
brightness scale) and nothing clipping unless you mean it.

## 6. Delivering

1. **Reframe:** davigen created one delivery timeline per extra format. Copy the finished master into them:
   1. Select all clips on the master timeline (**Cmd+A**) and copy them (**Cmd+C**).
   2. Open the delivery timeline and paste (**Cmd+V**).
   3. Reframe each shot in the Inspector.
2. **Run *Assign groups & nodes* again.** Pasted clips carry their clip grade, and this step puts them back in
   their camera groups.
3. **Queue renders:** click *Queue renders* in davigen. Every delivery is added to Resolve's render queue with the
   right preset, timeline, folder and file name:

   | Preset | Timeline | File |
   |---|---|---|
   | `DAVIGEN_MASTER` (ProRes 422 HQ) | `TL_03_MASTER_…` | `05_EXPORTS/05_MASTER/ITALY_2026_MASTER_3X2_v001.mov` |
   | `DAVIGEN_16X9_UHD` (H.265) | `TL_04_DELIVERY_16X9_…` | `05_EXPORTS/03_16X9/ITALY_2026_WEB_16X9_4K_v001.mp4` |
   | `DAVIGEN_9X16_1080` (H.264) | `TL_05_DELIVERY_9X16_…` | `05_EXPORTS/04_9X16/ITALY_2026_SOCIAL_9X16_1080_v001.mp4` |

4. On the Deliver page, click **Render All**.

In **Resolve Free** the master renders at the project resolution, at most UHD. With Studio you can render
6K 3:2.

## 7. Adding footage later

Open the project in Resolve, start davigen, and click **Add footage** on the home screen. New clips:

- go into the same `01_MEDIA/<CAMERA>` folders, and a new camera gets the next number
- land in the matching camera bins, with the same metadata and clip colors
- are added at the end of `TL_01_ASSEMBLY`
- join their color group. A new camera or profile gets a new group with its transforms.

Clips already in the project are skipped.

## 8. Naming and versions

- **Projects:** `COUNTRY_YEAR` or `PLACE_TOPIC_YEAR`, in capitals with underscores (`ITALY_2026`,
  `ALPS_HUT_TOUR_2026`).
- **Timelines:** `TL_<NN>_<STAGE>_<ASPECT>_<FPS>_v<NNN>`. Duplicate before big changes and raise the version
  (`…_v002`) instead of overwriting.
- **Exports:** `<PROJECT>_<PURPOSE>_<ASPECT/SIZE>_v<NNN>`. Never use `FINAL`, because there is always another
  final. The version number says which one is current.
- **Color groups:** `G_<CAMERA>_<PROFILE>`, made by davigen. Don't rename them, because *Assign groups & nodes*
  finds groups by name.

## 9. One-time Resolve settings

davigen sets everything the scripting API allows. A few settings live in Resolve's global preferences, and you
set them once:

- **Resolve → Preferences → User → Project Save and Load:**
  - turn on **Live Save**
  - turn on **Project Backups** (for example every 10 minutes, then hourly and daily)
- **Project Settings → Master Settings → Playback frame rate:** set it to the project frame rate if davigen
  reported it as a manual step. The API can't set it.
- **Project Settings → Master Settings → Working Folders:** set the proxy generation location and the cache
  location to `03_WORK/PROXIES` and `03_WORK/CACHE`. This is once per project.
- **Playback → Proxy Handling → Prefer Proxies** once proxies exist.
