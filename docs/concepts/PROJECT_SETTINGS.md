# Concept: Project settings, proxies and media checks

**Status:** concept, waiting for review (2026-09-30). Nothing is built yet, and the API spike (§3, plan step 01) has
not run. Key names marked *(unverified)* come from Resolve's scripting documentation or earlier versions and must
be confirmed in Resolve 21 before code depends on them. The step-by-step plan is in
[docs/plans/project-settings/](../plans/project-settings/00_OVERVIEW.md).

davigen already sets frame rate, resolution and colour management when it creates a project. This concept adds three
things:

- every project and playback setting that matters, derived from the format and the deliveries
- proxies that davigen makes and links itself
- checks on imported media

They are tied together by one command, **Check & repair**, which works on new and existing projects. After the
setup, the user should not have to set anything by hand in Resolve.

- [1. Why: what went wrong on MARSEILLE_2026](#1-why-what-went-wrong-on-marseille_2026)
- [2. Goals and rules](#2-goals-and-rules)
- [3. What is known, what the spike must answer](#3-what-is-known-what-the-spike-must-answer)
- [4. Target settings per preset](#4-target-settings-per-preset)
- [5. Settings the API won't take](#5-settings-the-api-wont-take)
- [6. Proxies: davigen makes and links them](#6-proxies-davigen-makes-and-links-them)
- [7. Check & repair](#7-check--repair)
- [8. Media checks on import](#8-media-checks-on-import)
- [9. What changes for the user](#9-what-changes-for-the-user)
- [10. Configuration](#10-configuration)
- [11. How we know it works](#11-how-we-know-it-works)
- [12. Open questions](#12-open-questions)

---

## 1. Why: what went wrong on MARSEILLE_2026

The project was created with davigen on 2026-09-25 (Resolve 21 Free, Lumix S1II 6K open gate V-Log HEVC and a DJI
Air 3 in D-Log M, 25 fps, 3:2). Five things went wrong, and davigen must prevent or detect each of them from now on:

| # | What happened | Cause (known or suspected) | What davigen does about it |
|---|---|---|---|
| 1 | Playback frame rate 24, timeline 25. Resolve stretched the audio live: it crackled on the Edit page, but not in Fairlight. | `SetSetting("timelinePlaybackFrameRate", …)` returns `False` in Resolve 21 (verified). A new project starts at 24. `project.py` noticed, but only printed a `MANUAL` line. | Set at creation through a one-time template or preset (§5). *Check & repair* reads the value, shows it red with a one-click instruction, and checks again afterwards. |
| 2 | The "proxies" were ProRes 422 HQ at the full 5952 × 3968: 170 GB of proxies for 43 GB of footage, each one about twice its HEVC original. They sat under `03_WORK/PROXIES/ProxyMedia/Users/<user>/Movies/…`, mirroring the absolute path. | `perfProxyResolutionRatio = "half"` either didn't take or was reset; Resolve's proxy *format* was never set by davigen. Resolve's own generation mirrors the absolute source path. | davigen makes the proxies itself: small, in a folder tree relative to `01_MEDIA`, with a size guard (§6). Resolve's own proxy settings are still set to small values, so a manual *Generate Proxy Media* can't do this again. |
| 3 | The proxy and cache locations had to be set by hand (`MANUAL` in `creator.py`). | The cache location set by a script is lost when the project is loaded again (observed before, see `creator.project_base`). The proxy location was never set. | With davigen's own proxies, Resolve's proxy location no longer matters. For the cache location the spike finds a route (§5); whatever remains manual is checked and shown. |
| 4 | A 44.1 kHz FLAC with an embedded cover picture (an MJPEG stream) crackled. As 48 kHz / 24-bit WAV without the cover it played clean. | Not isolated: the sample rate, the picture stream or Resolve's FLAC decoding. | Audio that doesn't match the project rate, or carries a picture, is converted on import (§8.1). The spike isolates the cause. |
| 5 | DJI clips at 59.94 fps on a 25 fps timeline. Not wrong, but nowhere visible. | The wizard warns once in the Format step; nothing marks the clips. | A note on each clip and a line in the report, with what it means for slow motion (§8.2). |

## 2. Goals and rules

**Goals:**

- A new project needs no manual step for frame rate, working folders or proxies. Where Resolve provably refuses a
  setting through the API, there is a **one-time setup per machine** (not per project), guided by davigen, checked
  afterwards and shown clearly in the UI.
- Every value comes from `config/workflow.toml` or is derived from the chosen `Format` (resolution, fps, aspect) and
  the deliveries. The code holds the rules; the config holds the values.
- One command tells the truth about any davigen project, old or new, and repairs what can be repaired.

**Rules (binding, from the brief):**

- Original media is never changed, moved or deleted. The only move is the existing journaled transfer on import.
- Nothing is deleted for good: files go to the Trash (through Finder, so *Put Back* works), as `uninstall.sh` does.
  Proposed exception: davigen's own unfinished `.part` files (§6.7, question 4).
- No writes to Resolve's database, preferences or config files. Reading config files, for analysis only, is allowed.
- Every change to an existing project is shown first, applied only after confirmation, and can be undone.

**Not in scope:** generating optimized media, changing clip speed or clip attributes, Resolve's global preferences
(only read, if at all, §4.5).

## 3. What is known, what the spike must answer

Plan step 01 runs a spike in Resolve 21 Free, and in Studio if it's available, following
[the Basic Correction spike](../plans/basic-correction/01_SPIKE.md). It dumps `project.GetSetting()` without an
argument, which returns every key with its value, on a fresh project and on MARSEILLE_2026 (read only). Then, for
every setting below, it records three answers: the key exists, the value is taken, and the value is still there
after saving, closing and loading the project again.

What is known today:

| Setting (Project Settings →) | Key | Known today |
|---|---|---|
| Master Settings → Timeline frame rate | `timelineFrameRate` | API works on an empty project (verified, in use) |
| Master Settings → Playback frame rate | `timelinePlaybackFrameRate` | readable; `SetSetting` returns `False` (verified) |
| Master Settings → Timeline resolution | `timelineResolutionWidth` / `…Height` | API, height first (verified, in use) |
| Master Settings → Video monitoring format | `videoMonitorFormat` | set quietly today, never checked |
| Color Management → colour science, timeline and output colour space | `colorScienceMode`, `colorSpaceTimeline`, `colorSpaceOutput` | API (verified, in use) |
| Color Management → 3D lookup table interpolation | unknown, possibly no key | – |
| Image Scaling → Resize filter | `imageResizeMode` *(unverified)* | – |
| Image Scaling → Input scaling | `timelineInputResMismatchBehavior` | API (verified, in use) |
| Fairlight / timeline audio sample rate | unknown | – |
| Master Settings → Render cache mode | `perfRenderCacheMode` | set, never checked after reload |
| Master Settings → Render cache format | `perfRenderCacheCodec` *(unverified)* | – |
| Master Settings → Working Folders → Cache files location | `perfCacheClipsLocation` *(unverified)* | lost after reload (observed) |
| Master Settings → Working Folders → Proxy generation location | unknown | – |
| Master Settings → Proxy handling mode | `perfProxyMediaMode` | set to `"1"`, meaning unverified |
| Master Settings → Proxy resolution | `perfProxyResolutionRatio` | set to `half`, didn't hold on MARSEILLE |
| Master Settings → Proxy format | unknown *(`perfProxyMediaFormat`?)* | never set |
| Master Settings → Optimized media resolution / format | `perfOptimizedResolutionRatio`, `perfOptimisedCodec` *(unverified)* | never set |
| Working Folders → Gallery stills location | `colorGalleryStillsLocation` | API, persists (verified; davigen finds the project folder through it) |
| Color → Use S-curve for contrast | `colorUseContrastSCurve` | on by default (verified in the Basic Correction spike) |

API functions this concept depends on, all documented in Resolve's scripting README but not yet tried in Free:
`ProjectManager.ImportProject(path, name)`, `ProjectManager.ExportProject(name, path, withStillsAndLUTs)`,
`Project.GetPresetList()`, `Project.SetPreset(name)`, `MediaPoolItem.LinkProxyMedia(path)`,
`MediaPoolItem.UnlinkProxyMedia()`, and `GetClipProperty()` for the proxy state (`"Proxy"`, `"Proxy Media Path"`).

**The spike's result** is one table, one row per setting, with one of: *API*, *template*, *preset*, *manual only*.
§5 describes the routes; §5.5 says which one davigen uses when more than one works.

## 4. Target settings per preset

### 4.1 The model

A new module computes the target state:

```python
desired(cfg, fmt, deliveries, studio, base) -> list[Setting]

@dataclass
class Setting:
    id: str               # "playback_fps"
    key: str              # "timelinePlaybackFrameRate" (from the spike)
    value: str            # "25"
    source: str           # "format", "config", "deliveries", "project folder"
    route: str            # "api" | "template" | "preset" | "manual" (from the spike table)
    compare: str          # "text" | "number" | "path"
    manual: str           # exact click path, shown when it has to be done by hand
```

`project.apply_settings` and *Check & repair* both use it, so creating a project and checking one can never
disagree. Tests cover `desired` without Resolve.

### 4.2 Rules

| Setting | Rule | Why |
|---|---|---|
| Timeline frame rate | `Format.fps` | as today |
| Playback frame rate | always equal to the timeline frame rate; not configurable | MARSEILLE #1 |
| Video monitoring format | `"{monitor} {fps}"` with `monitor = "HD 1080p"` from the config; `"UHD 2160p"` only if the config says the I/O device can do it | only matters with a Blackmagic I/O device; the common ones (UltraStudio Monitor 3G) do HD only; the frame rate must match, or the monitor gets a converted signal |
| Colour science, timeline and output space | from `[project.settings]`, as today | unchanged |
| 3D LUT interpolation | `tetrahedral` (config) | the input and output transforms are LUTs; trilinear bends the grey axis between grid points (plan basic-correction 02), tetrahedral is the more accurate interpolation. See §4.4. |
| Resize filter | `sharper` (config) | Resolve's default. Whether downscaled deliveries (6K → UHD, 1080) look better with `smoother` is question 10. |
| Input scaling | `scaleToCrop`, as today | unchanged |
| Timeline audio sample rate | `48000` (config) | the rate davigen converts audio to (§8.1) |
| Render cache mode | `smart` (config) | as today |
| Render cache format | derived from the master delivery's codec: `ProRes422HQ` → ProRes 422 HQ, `ProRes4444` → ProRes 4444, everything else → ProRes 422 (a table in the config) | with *Use render cached images* on the Deliver page, cached frames go into the render, so the cache must not be worse than the master; for playback alone ProRes 422 is plenty |
| Cache files location | `<project>/03_WORK/CACHE` | the project folder holds everything it uses |
| Proxy handling | *Prefer proxies* | proxies exist for the clips that need them |
| Resolve's own proxy resolution and format | the same as davigen's proxies (quarter, ProRes 422 Proxy) | a manual *Generate Proxy Media* then produces something harmless |
| Resolve's own proxy location | `<project>/03_WORK/PROXIES/_RESOLVE` | kept apart, so the proxy check (§6.9) can tell Resolve's proxies from davigen's |
| Optimized media resolution and format | the same as the proxies | davigen doesn't make optimized media; an accidental one shouldn't be large |
| Gallery stills location | `<project>/02_RESOLVE/03_GALLERY`, as today | unchanged |
| Contrast S-curve | check only: must be on | Basic Correction's contrast math depends on it (BASIC_CORRECTION §12) |

Where the spike shows that Resolve puts optimized media into the cache files location rather than a folder of
their own, `03_WORK/OPTIMIZED_MEDIA` is never used (question 9).

### 4.3 Examples

| | 3:2 6K, Studio, 25 fps, ProRes HQ master + UHD + 9:16 | 3:2, Free (3240 × 2160), 25 fps, same deliveries | 16:9 UHD, 50 fps, H.265 only | 9:16 1080 × 1920, 29.97 fps, H.264 only |
|---|---|---|---|---|
| Timeline = playback fps | 25 | 25 | 50 | 29.97 |
| Monitoring | HD 1080p 25 | HD 1080p 25 | HD 1080p 50 | HD 1080p 29.97 |
| LUT interpolation / resize | tetrahedral / sharper | tetrahedral / sharper | tetrahedral / sharper | tetrahedral / sharper |
| Audio | 48 kHz | 48 kHz | 48 kHz | 48 kHz |
| Render cache | ProRes 422 HQ | ProRes 422 HQ | ProRes 422 | ProRes 422 |

Proxies depend on the clips, not on the master: see §6.2.

### 4.4 Side effect on Basic Correction

Basic Correction's simulator applies the input and output LUTs trilinearly (`basic/pipeline.apply_lut`, "Resolve's
default") and matches Resolve within 0.3–0.5 % of display. If projects switch to tetrahedral, the simulator must
switch with them, reading the project's setting, and the comparison on MARSEILLE_2026 must be run again (1.35 %
median today). The switch and the simulator change go out together (question 12). Existing projects keep trilinear
until *Check & repair* changes them, so the simulator has to handle both.

### 4.5 Further settings, proposed (not adopted without your OK)

| # | Proposal | Reason | Cost |
|---|---|---|---|
| a | Loudness meter target −14 LUFS integrated when any web or social delivery is chosen | YouTube, Instagram and TikTok normalise playback to about −14 LUFS; mixing to it avoids being turned down, or quiet next to other videos. Only the meter target, the audio is not touched. | one setting, key *(unverified)* |
| b | Web deliveries tagged so Apple players show what the viewer shows (Resolve's *Rec.709-A* on macOS) | A Rec.709 Gamma 2.4 render tagged 1-1-1 looks brighter and flatter in QuickTime and Safari than in Resolve's viewer. | changes how uploads look; needs a test upload before deciding |
| c | Read (never write) Resolve's preferences to show whether *Live Save*, *Project Backups* and *Use Mac display color profiles for viewers* are on | `docs/WORKFLOW.md` §9 asks for them by hand today; a check makes them visible | only if the preference file is readable text (spike) |
| d | Considered and not proposed: a different retime process (frame blend, optical flow) for clips whose frame rate differs | Every choice has a visible cost (ghosting, artefacts, render time), and the right fix for slow motion is conforming the clip (§8.2). | – |

## 5. Settings the API won't take

### 5.1 Route A: the API

`SetSetting`, then `GetSetting`, then the same after save and reload. If all three hold, that's the route.

### 5.2 Route B: a project template (`.drp`)

New projects are created with `ProjectManager.ImportProject(template, name)` instead of `CreateProject(name)`, from
a template whose playback frame rate (and LUT interpolation, if that has no key) is already right. Then the API
settings are applied as today. One template per frame rate.

Two ways to get the templates:

- **Shipped with davigen:** made once by us in Resolve 21, one per frame rate in `fps_choices` (nine files).
  Automatic for the user, but a `.drp` from a newer Resolve probably doesn't import into an older one (spike), the
  files are opaque binaries in the repository, and they might carry machine-specific paths.
- **Made once on the user's machine (recommended):** the first time a frame rate is used, davigen creates an empty
  project `DAVIGEN_TEMPLATE_25` in a Project Manager folder `DAVIGEN_TEMPLATES`, applies everything it can, and asks
  for the rest with an exact instruction ("Project Settings → Master Settings → Playback frame rate → 25 → Save").
  davigen watches `GetSetting` until the value is right, then exports the template to
  `data/templates/project_25fps_r21.drp`. The template project stays where it is; nothing is deleted. From then on,
  every 25 fps project is automatic. The template matches the user's own Resolve version and edition.

### 5.3 Route C: Resolve's project presets

The user saves a preset once (Project Settings → Presets → Save As `DAVIGEN_25`). davigen finds it with
`GetPresetList()` and applies it to a new, empty project with `SetPreset`, then applies the API settings on top.

- It is not known whether `SetPreset` works in Free or carries the playback frame rate (spike).
- Presets can be changed by the user without davigen knowing, so davigen reads every setting back after applying.
- A preset carries all master settings. Applied to an **existing** project it could change its resolution or
  frame rate, so davigen only ever applies presets to new projects.

### 5.4 Other routes

| Route | Verdict |
|---|---|
| `Timeline.SetSetting` with `useCustomSettings` | the spike tries whether it takes a playback frame rate (unlikely: playback is a project setting) |
| Variants of the API call: `"25"` vs `"25.0"`, before or after `timelineFrameRate`, after a save | the spike tries all of them; cheap |
| GUI scripting (System Events clicking in Resolve) | rejected: needs Accessibility permission, clicks into the user's session, breaks with every Resolve UI change |
| Writing Resolve's database or preference files | forbidden by the rules |
| Importing an FCPXML/AAF timeline so Resolve sets the project | not controllable through the API's import options |

### 5.5 Recommendation (pending the spike)

- **API** wherever it works, including after reload.
- **Template made once per frame rate** (route B, second variant) for what the API can't set in a new project.
- **Presets** only if `ImportProject` turns out not to carry those settings.
- **Existing projects** (repair): neither a template nor a preset can be applied safely, so the playback frame rate
  stays a one-click manual step there, with the exact path, and davigen checks it afterwards.
- **Cache location:** if the API value doesn't survive a reload, davigen sets it again every time it starts with a
  project open, and the check shows it. It can't go into a template, because it is a different path for every
  project.

## 6. Proxies: davigen makes and links them

### 6.1 Why not Resolve's

Resolve Free offers no proxy generation through the API, and Resolve's own generation produced the MARSEILLE
proxies. davigen already uses ffmpeg (`davigen/edit/decode.py`), which decodes HEVC and encodes ProRes in hardware
on Apple silicon. `MediaPoolItem.LinkProxyMedia(path)` attaches the result. Whether it works in Free, and whether
Resolve keeps the link after a restart, is the spike's most important proxy question.

### 6.2 Which clips get one

A rule in the config (`[proxy.needed]`):

| Clip | Proxy? |
|---|---|
| video with a long edge above `max_long_edge` (1920) | yes |
| video at or below it (phone 1080p, a 1080p drone clip, ProRes HD) | no: a proxy would be as large and gain nothing |
| stills, audio | no |
| Long-GOP 10-bit 4:2:2 or 6K and larger ("heavy", §8.3) | yes, first in the queue |

On MARSEILLE_2026: every Lumix 6K clip gets one; the DJI 4K clips too (at the ratio the size guard allows, §6.5).

### 6.3 Format

- **Codec:** ProRes 422 Proxy in `.mov` by default (configurable: ProRes 422 LT, DNxHR LB). Apple silicon decodes
  ProRes in hardware, and it is intra-frame, so scrubbing is instant.
- **Size:** the largest of ½ and ¼ of the original whose long edge is at most 1920 (config `ratios`,
  `max_long_edge`). The Lumix 5952 × 3968 becomes 1488 × 992, DJI 3840 × 2160 becomes 1920 × 1080. Whether Resolve
  also accepts non-integer ratios (5952 → 1920) is question 2.
- **Must match the original exactly:** the same frame count and frame rate (no frame-rate conversion, frames passed
  through one by one), the same start timecode, the same audio channels at the same sample rate (as 24-bit PCM).
- **Colour unchanged:** log stays log. No colour conversion; the same matrix, range, primaries and transfer tags as
  the source; 10-bit. Scaling happens in the source's YUV with the source's range on both sides, so Basic
  Correction and the group transforms look the same on proxy and original.
- **ffmpeg, in outline** (exact flags are settled in step 05, against real clips):

  ```
  ffmpeg -hwaccel videotoolbox -i SRC -map 0:v:0 -map 0:a? -map_metadata 0 -timecode <source TC>
         -vf scale=1488:992:flags=lanczos:in_range=<src>:out_range=<src> -fps_mode passthrough
         -c:v prores_videotoolbox -profile:v proxy        (or prores_ks -profile:v 0, 10-bit 4:2:2)
         -color_primaries/-color_trc/-colorspace/-color_range  <as the source>
         -c:a pcm_s24le   DST.part.mov
  ```

- **Checked after encoding** (ffprobe): frames, frame rate, duration within one frame, start timecode, audio
  channels and range tags. A proxy that fails is discarded (it is davigen's own file, §6.7).

### 6.4 Where they go

Mirrored relative to `01_MEDIA`, never with an absolute path:

```
01_MEDIA/01_LUMIX_S1II/2026-09-25_MARSEILLE/P1000090.MP4
03_WORK/PROXIES/01_LUMIX_S1II/2026-09-25_MARSEILLE/P1000090.mov
```

- Two originals with the same stem in one folder (`X.MP4`, `X.MOV`): the second becomes `X_MOV.mov`.
- Clips imported with *Leave in place* sit outside the project: `03_WORK/PROXIES/_EXTERNAL/<parent folder>_<hash of
  its absolute path, 8 characters>/<stem>.mov`. The hash keeps names unique without writing the path out.
- Resolve's own proxies, if anyone generates them, go to `03_WORK/PROXIES/_RESOLVE` (§4.2).

### 6.5 Size guard

MARSEILLE must not happen again:

1. **Before:** an estimate per clip. ProRes 422 Proxy uses about 0.72 bits per pixel per frame (Apple's white
   paper: 45 Mb/s at 1920 × 1080, 29.97 fps). If the estimate is above `max_size_ratio` (50 %) of the original, the
   next smaller ratio is tried. If none fits, the clip gets no proxy and the report says why. A high-frame-rate 4K
   clip is the typical case: at ½ and 59.94 fps a ProRes Proxy is about 90 Mb/s, probably more than half of its
   HEVC original. Question 3 decides what happens then.
2. **While encoding:** from ffmpeg's progress, davigen extrapolates the final size; beyond 10 % of the clip, a
   projected size over the limit stops that clip.
3. **After:** the real size is checked against the limit once more.
4. **The whole set:** if all proxies together would exceed `max_total_ratio` (35 %) of the originals, davigen asks
   before starting.

### 6.6 Free space

Before starting: the sum of the estimates × 1.2 plus `min_free_gb` (20 GB) must fit on the volume of `03_WORK`,
otherwise davigen doesn't start and says how much is missing. Checked again before every clip, since other
programs fill disks too.

### 6.7 Background, cancel, resume

- Proxies are made in the background, with progress in the UI (clip n of m, overall percentage, time left) and a
  **Stop** button. The rest of davigen stays usable.
- Each proxy is written as `<name>.part.mov` and renamed only when it is complete and checked. Stop kills ffmpeg;
  the `.part` file is removed. It is davigen's own unfinished file, not media (question 4).
- **Resume:** a new run skips every proxy that is complete, checked and still matches its original, and starts
  the rest. Leftover `.part` files are discarded first.
- One clip at a time by default (the media engines are the limit, not the CPU); `parallel` in the config.
- davigen quits 10 minutes after its window closes today. While proxies are being made, it stays alive as long as
  Resolve does.
- **Linking** happens after each finished proxy, on the thread that talks to Resolve, and only if the same project
  is still open. Otherwise the link waits for the next proxy check.

### 6.8 The record

`00_ADMIN/PROJECT_INFO/proxies.json`, one entry per clip:

- the original: relative path, size, modification time, frames, frame rate, duration, start timecode
- the proxy: relative path, size, spec (codec, ratio), when it was made, whether it was linked

It is what makes resume, stale detection and the report possible without asking ffprobe again.

### 6.9 Proxy check: new, changed and orphaned

*Check proxies* (its own command, and part of *Add footage* and *Check & repair*) compares three things: the clips
in the Media Pool, the files in `03_WORK/PROXIES`, and the record.

| State | Meaning | Action |
|---|---|---|
| ok | proxy exists, matches the original, linked | – |
| not needed | the rule of §6.2 says no proxy | – |
| missing | the clip needs one, there is none | make and link |
| stale | the original is newer than the proxy, its size changed, or frames, frame rate or duration differ | make again and link |
| old spec | made with other settings (codec, ratio) | reported; remade only on request |
| not linked | the file is fine, Resolve has no link or has lost it | link |
| foreign link | Resolve links something else (e.g. its own 6K ProRes HQ under `ProxyMedia/`) | make davigen's proxy, link it instead |
| broken link | Resolve links a file that no longer exists | make and link |
| orphan | a proxy file without an original, or a file davigen didn't make (e.g. Resolve's `ProxyMedia/` tree) | reported with its size; moved to the Trash only on request |

### 6.10 Resolve's side

- Rendering uses the originals, not proxies, unless a render setting says otherwise. Basic Correction and Edit
  assist keep measuring and decoding the originals. The spike confirms this for the render settings davigen uses.
- Relinking the originals (a moved project folder) doesn't touch proxy links, because both paths are inside the
  project folder. The spike checks what Resolve does with proxy links when the project folder moves.

## 7. Check & repair

### 7.1 Where

- A **Check project** button on the home screen.
- **Workspace → Scripts → davigen Check Project**, a new menu entry that opens davigen on the check page (like
  *davigen Basic Correction*).
- Automatically at the end of *New project* and *Add footage*, read only: the result shows in their summary.

### 7.2 What it checks

The project's target state comes from `PROJECT_INFO/davigen.json` (format, deliveries) and today's
`workflow.toml`:

1. **Settings:** everything in §4, including the working folders.
2. **Proxies:** the states in §6.9, with sizes (made, missing, orphaned).
3. **Media:** the checks in §8, for clips already in the project.
4. **Resolve preferences:** if proposal 4.5 c is adopted.

For a project made by an older davigen, where `davigen.json` lacks something, the target comes from the current
standard, and the report says so.

### 7.3 The report

One row per item, with a traffic light:

| Light | Meaning |
|---|---|
| green | OK |
| blue | davigen can fix it; after confirming: *fixed* |
| amber | has to be done by hand: the exact click path, and a **Check again** button |
| grey | information only (e.g. a clip's frame rate, §8.2) |
| red | davigen couldn't read it (with the reason) |

Every blue row shows *now* → *after*. Proxy rows show the estimated size and time.

### 7.4 The flow

1. **Read:** settings, clips, proxy folder, record. Nothing is changed.
2. **Report:** fixable rows are ticked; the user can untick any.
3. **Confirm:** a summary of what will change.
4. **Backup:** the project is exported with `ExportProject` to
   `02_RESOLVE/02_BACKUPS/<PROJECT>_<yyyymmdd_hhmmss>_before_repair.drp`. A repair journal
   (`00_ADMIN/PROJECT_INFO/repairs/<stamp>.json`) records every old value and every old proxy link. If the export
   fails, nothing is changed.
5. **Apply**, save the project, **check again**, show the new report.
6. Proxy work starts as a background job (§6.7) after its space check.

Resolve's *Create Timeline Backup* isn't in the API as far as known; the project export covers every timeline. The
spike checks for an API call.

### 7.5 Undo

- **Undo last repair:** sets every setting back and relinks every proxy as recorded in the journal.
- Files moved to the Trash come back with Finder's *Put Back*.
- As a last resort, the backup `.drp` can be imported as a separate project; davigen never overwrites the project
  with it.

### 7.6 On MARSEILLE_2026 (acceptance)

The check must show at least:

- **Playback frame rate** 24 instead of 25: amber, with the click path; green after *Check again*.
- **Proxies:** the clips linked to Resolve's full-resolution ProRes HQ files: *foreign link*, with their total size.
  After confirming, davigen makes its own proxies (far smaller than the originals, mirrored under
  `03_WORK/PROXIES`), links them, and lists the old `ProxyMedia/` tree as an orphan to move to the Trash.
- **Cache location:** where it points, fixed or amber depending on the spike.
- **LUT interpolation** and the other settings of §4: whatever differs.
- **Media:** the 44.1 kHz FLAC with cover (convert), the DJI clips at 59.94 fps (note), the Lumix 6K HEVC (heavy).

## 8. Media checks on import

They run on *New project*, *Add footage* and Edit assist's music choice, and inside *Check & repair* for clips
already imported. They need an ffprobe pass per file (codec, profile, pixel format, frame rate, timecode, audio
streams, sample rate, attached pictures), which the scanner doesn't do today.

### 8.1 Audio

- **When:** a file whose sample rate isn't the project's (48 kHz), or which carries a picture stream (cover art,
  `attached_pic`).
- **What:** converted next to the original as `<stem>_48k24.wav`: 48 kHz, 24-bit PCM, soxr resampler, no picture,
  text tags (title, artist) kept. The WAV goes into the Media Pool; the original stays next to it on disk and is not
  imported.
- **Where:** audio for the film (field recorders) goes to `01_MEDIA/90_AUDIO/`. Today *Add footage* only accepts
  video files, so it learns to accept audio. Music picked in Edit assist is imported from wherever it is today;
  where it should go is question 5.
- **Off switch:** `[media_checks.audio] convert = false`.
- **Camera audio inside video clips** (a phone at 44.1 kHz) is only reported: it can't be converted without
  rewriting the video file.

### 8.2 Frame rate

A clip whose frame rate differs from the timeline gets a note, and nothing is changed automatically. The note
depends on the case:

| Clip on timeline | Note |
|---|---|
| a whole multiple (50 on 25, 59.94 on 29.97) | "50 fps on a 25 fps timeline: every 2nd frame is dropped. For 2× slow motion: Clip Attributes → Video Frame Rate 25." |
| not a whole multiple (59.94 on 25, 29.97 on 25, 30 on 25) | "59.94 fps on a 25 fps timeline: frames are dropped unevenly, motion may stutter. For slow motion (41.7 %): Clip Attributes → Video Frame Rate 25." |
| close (23.976 on 24, 25 on 24) | "23.976 fps on a 24 fps timeline: plays 0.1 % fast, a frame is repeated or dropped now and then." |

- The note is appended to the clip's *Comments* (davigen's own line there stays).
- The clip gets a keyword `FPS_5994`, so a Smart Bin can collect them.
- The report lists them.

### 8.3 Heavy codecs

A clip is *heavy* when it is Long-GOP (H.264, HEVC) and 10-bit 4:2:2, or 6K and above, or at least UHD at 50 fps
and above. It gets the keyword `HEAVY`, a line in the report, and its proxy comes first.

## 9. What changes for the user

The `MANUAL` lines at the end of *New project* today, and what becomes of them:

| Today | After |
|---|---|
| Playback frame rate | set through the template (one-time setup per frame rate), checked |
| Working folders (proxy and cache location) | proxy location no longer matters; cache location through the route the spike finds, checked |
| Generate Proxy Media | davigen makes and links proxies in the background |
| Add look nodes before the output node in Group Post-Clip | a tip, not a manual step: moved to a *Tips* list |
| Run *Assign groups & nodes* after building new timelines | a tip, as above |
| Create colour groups by hand (Resolve before 18.5) | stays: old Resolve |

- The wizard's **Review** step says either *Resolve settings: all automatic* or *One-time setup needed for 25 fps*
  (with a button that guides through it before the project is created).
- It has a **Make proxies** checkbox, on by default.
- The run's summary shows the check's result: green, or the amber rows.

## 10. Configuration

Sketch; the final names are decided in plan step 02. Everything from §4 moves into one place:

```toml
[project.settings]                  # set through the API, verified in Resolve 21 (spike)
colorScienceMode = "davinciYRGB"
colorSpaceTimeline = "DaVinci WG/Intermediate"
colorSpaceOutput = "Rec.709 Gamma 2.4"
timelineInputResMismatchBehavior = "scaleToCrop"
perfRenderCacheMode = "smart"

[project.derived]                   # values that follow the format, deliveries or project folder
lut_interpolation = "tetrahedral"
resize_filter = "sharper"
audio_sample_rate = 48000
monitor = "HD 1080p"                # + " {fps}"
cache_location = "03_WORK/CACHE"
cache_codec = "ProRes 422"
cache_codec_by_master = { ProRes422HQ = "ProRes 422 HQ", ProRes4444 = "ProRes 4444" }

[proxy]
enabled = true
folder = "03_WORK/PROXIES"
codec = "prores_proxy"              # prores_proxy | prores_lt | dnxhr_lb
ratios = [0.5, 0.25]
max_long_edge = 1920
max_size_ratio = 0.5                # per clip: never larger than this share of the original
max_total_ratio = 0.35              # all together: ask above this
min_free_gb = 20
parallel = 1
bits_per_pixel = 0.72               # the estimate (ProRes 422 Proxy)

[proxy.needed]
min_long_edge = 1921                # clips at or below max_long_edge need none

[media_checks.audio]
convert = true
sample_rate = 48000
bit_depth = 24
resampler = "soxr"

[media_checks.heavy]
long_gop = ["h264", "hevc"]
min_long_edge = 5760
min_pixel_rate = 414_720_000        # 3840 × 2160 × 50
```

## 11. How we know it works

**Without Resolve (pytest, `tests/fake_resolve.py`):**

- target settings from format, deliveries and config, for the four presets of §4.3
- the proxy plan: which clips, which ratio, which path; the size guard, the estimate, the free-space check
- path mirroring, name collisions, *Leave in place* clips
- the proxy check: ok, missing, stale, old spec, not linked, foreign, broken, orphan
- the repair: diff, journal, undo, and that nothing is applied without confirmation
- audio and frame-rate rules, note texts, heavy-codec rule
- ffmpeg commands, built as argument lists and checked, and real encodes of small generated clips where ffmpeg is
  installed (skipped otherwise)

The fake Resolve gains `Project.GetSetting()` without an argument, `SetSetting` that refuses what Resolve refuses,
`GetPresetList` / `SetPreset`, `ProjectManager.ImportProject` / `ExportProject`, and `MediaPoolItem.LinkProxyMedia`
/ `UnlinkProxyMedia` with the proxy clip properties.

**In Resolve:** every step that touches Resolve ends with a documented check (plan files), and step 09 runs the
whole thing on MARSEILLE_2026.

## 12. Open questions

1. **Template:** made once per frame rate on your machine (recommended, §5.2) or shipped with davigen?
2. **Proxy size:** ½ or ¼ with the long edge at most 1920 (the Lumix 6K becomes 1488 × 992), or exactly 1920 on the
   long edge if Resolve accepts non-integer ratios?
3. **A clip that can't meet the size guard** even at ¼ (typical: 4K at 59.94): no proxy for it (it may play fine
   as HEVC 4:2:0 on Apple silicon), ⅛, or make it anyway with a warning?
4. **Unfinished `.part` proxies:** delete them directly (davigen's own temporary files), or also to the Trash?
5. **Music:** into `04_ASSETS/MUSIC` (where the folder tree puts it) or `01_MEDIA/90_AUDIO`? Should davigen copy a
   song picked from outside the project into the project folder first (a copy, never a move)?
6. **ffmpeg:** proxies and audio conversion make it required. Keep asking for Homebrew's ffmpeg (it has soxr and
   VideoToolbox), or have the installer fetch a static build into `runtime/ffmpeg` (licensing, and a build with both
   is needed)?
7. **Language of clip notes:** English, like the rest of davigen's UI?
8. **Proposals 4.5 a–c:** which of them should go in?
9. **`03_WORK/OPTIMIZED_MEDIA`:** drop it from the folder tree if the spike confirms Resolve writes optimized media
   into the cache location?
10. **Resize filter:** stay with `sharper`, or compare with `smoother` on a downscaled delivery in step 09?
11. **The spike:** is a Studio installation available? May it run on a copy of MARSEILLE_2026
    (`scripts/dev_project_copy.py`), with the proxy part reading the real footage (read only)?
12. **Tetrahedral:** ship it together with the Basic Correction simulator change (§4.4)?
13. **Proxies after New project and Add footage:** start automatically in the background, or ask each time?
