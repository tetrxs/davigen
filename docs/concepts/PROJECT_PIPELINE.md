# Concept: davigen as a project manager, built on one pipeline

**Status:** concept for review (2026-09-30; proxies and safety levels decided the same day). The Resolve facts in §2 were checked live in Resolve 21.0.0.48 Free on
this Mac today; nothing else is built yet.

davigen has been a project *setup* tool with two AI-ish extras. From here on it **looks after a project for its whole
life**: it sets the project up, takes in every kind of asset at any time (footage, stock, photos, GIFs, graphics,
music, voice-over, sound effects), sorts them, and applies actions to them – on day one and weeks later, the same way.

- [1. What changes](#1-what-changes)
- [2. What Resolve allows (checked today)](#2-what-resolve-allows-checked-today)
- [3. Assets](#3-assets)
- [4. Actions: the building blocks](#4-actions-the-building-blocks)
- [5. The pipeline](#5-the-pipeline)
- [6. Safety: what "atomic" means here](#6-safety-what-atomic-means-here)
- [7. The actions in detail](#7-the-actions-in-detail)
- [8. Project setup](#8-project-setup)
- [9. User interface](#9-user-interface)
- [10. Deleting a project](#10-deleting-a-project)
- [11. Settings](#11-settings)
- [12. Order of work](#12-order-of-work)

---

## 1. What changes

| | Today | From now on |
|---|---|---|
| Direction | set up a project, then extras | set up **and** manage a project |
| Getting media in | *New project* and *Add footage*: camera video only | one **import** for every kind of asset, in the setup and at any time later |
| Move / copy / leave | chosen in the wizard | one davigen setting, the same for setup and every import |
| Basic correction | a flow of its own | an **action** on video assets (the correction itself is not touched) |
| Song markers | part of Edit assist | an **action** on music assets, with four kinds of markers |
| Rough cut, selects markers, Whisper transcription, preview video | Edit assist | **removed**, together with every model download |
| Progress | a fixed list of steps per flow | a **pipeline** that is built from the chosen actions: steps appear and disappear, each with *n of m*, a bar and a time estimate, and a step can stop and ask for input |
| Playback frame rate, working folders | manual steps after the setup | set automatically (§2); every working folder (proxies, cache, project media, gallery) lies inside the project folder |
| Deleting a project | not possible | Resolve project and project folder go to the Trash |

The rule behind it: **everything that can happen to an asset exists once, as a building block, and is used in the
setup and later in exactly the same way.** A new feature is a new block, not a new flow.

## 2. What Resolve allows (checked today)

Checked with a throwaway project folder `DAVIGEN_SPIKE` in the Project Manager (MARSEILLE_2026 was only read).

| Setting | Scripting API | What davigen does |
|---|---|---|
| Playback frame rate | `SetSetting("timelinePlaybackFrameRate")` returns `False` on a new, empty project, before and after the timeline frame rate, for `"25"`, `"25.0"` and `25` | **project template** (below): verified with 25 and 50 fps |
| Proxy generation location | no key in `GetSetting()` (157 keys dumped) | **project template**: verified, Resolve keeps the path (read back through `ExportProject`) |
| Cache files location (`perfCacheClipsLocation`) | works and survives closing and loading the project | API |
| Project media location (`projectMediaLocation`) | works, survives reload | API |
| Proxy resolution (`perfProxyResolutionRatio`), proxy mode, optimized media resolution | work | API |
| Proxy / optimized / render cache **codec** (`perfOptimisedCodec`, `perfRenderCacheCodec`) | refuse every value tried (`apco`, `apcn`, `ProRes 422 Proxy`, …) | template (to be mapped the same way) or left at Resolve's default |
| Generating proxies | **no API at all**: a `MediaPoolItem` can `LinkProxyMedia` / `UnlinkProxyMedia`, but nothing starts Resolve's own proxy generation | not davigen's job: davigen sets the proxy folder, proxies are made in Resolve when wanted (§7.6) |

**The project template.** A `.drp` is a zip; its `project.xml` holds the project settings as a zstd-compressed
protobuf (the same packing as a `.drx`). The playback frame rate is field 248 (a float), the proxy location is field
40 inside field 36. davigen makes a template itself, on the user's own Resolve:

1. create an empty project, `ExportProject` it to `data/templates/`, delete the empty project again;
2. set field 248 to the frame rate and field 40 to `<project>/03_WORK/PROXIES`;
3. `ImportProject(template, name)`, then everything else through the API as today.

Nothing is shipped as a binary, the template always matches the installed Resolve version, and if Resolve ever
changes the format the fields are simply not found: davigen then falls back to the API plus one clear manual step,
which it checks afterwards. It only ever writes files it made itself; Resolve's database is never touched.

Existing projects can't be re-imported, so for them the playback frame rate stays a checked manual step.

## 3. Assets

An **asset** is one file the project owns. davigen recognises its kind and gives it a home:

| Kind | Recognised by | Folder | Bin |
|---|---|---|---|
| Camera clip | video with camera metadata (as today) | `01_MEDIA/NN_<CAMERA>/<date>_<source>/` | `01_FOOTAGE/NN_<CAMERA>` |
| Stock video | video without camera metadata, or chosen by hand | `01_MEDIA/92_STOCK/` | `01_FOOTAGE/92_STOCK` |
| Photo | jpg, heic, tiff, raw, png without alpha | `01_MEDIA/91_STILLS/` | `08_REFERENCES` → new `09_STILLS` |
| Graphic | png/tiff with alpha, psd, svg, gif, webp | `04_ASSETS/GRAPHICS/` (logos: `LOGOS`) | `05_GRAPHICS/…` |
| Music | audio longer than ~60 s with music content | `04_ASSETS/MUSIC/` | `04_AUDIO/MUSIC` |
| Voice-over / dialogue | speech-like audio | `01_MEDIA/90_AUDIO/` | `04_AUDIO/VO` |
| Sound effect | short audio | `04_ASSETS/SFX/` | `04_AUDIO/SFX` |
| LUT, font | .cube, .ttf/.otf | `04_ASSETS/LUTS`, `FONTS` | not imported |

The guess is shown in the import list and can be changed per file or per group, like the camera groups today.

**The asset record.** `00_ADMIN/PROJECT_INFO/assets.json` remembers every asset: a stable id (from size and the
first and last megabyte, so it survives the move into the project and recognises the same card a second time), kind,
path, where it came from, its Media Pool id, and per action what was done, when and with which settings.

The record is a memory, not the truth. **Every action checks the real state itself** (is the file there? is the
proxy linked? does the clip have `DAVIGEN_AUTO`? does the song have davigen's markers?). So:

- an asset removed in Resolve is noticed and marked as removed; nothing breaks, nothing is redone;
- a clip added by hand in Resolve can be taken over into davigen;
- an action already done is never done again, unless asked for explicitly (*redo*).

Projects made by an older davigen get their record built once from what is in Resolve (the davigen metadata on the
clips, `DAVIGEN_AUTO` versions, markers).

## 4. Actions: the building blocks

Every action is one Python class in `davigen/pipeline/actions/` and registers itself. It says:

| Part | Meaning |
|---|---|
| `id`, `label`, `kinds` | which asset kinds it applies to (`camera`, `music`, …) or `project` for project-wide steps |
| `mandatory` | always part of an import (sorting, colour groups) or chosen (Basic correction, song markers) |
| `after` | which actions must come first (colour after import, Basic correction after the node structure) |
| `inputs` | what it needs to know, as a small schema: choice, toggle, number, text – or a named custom form (the look picker of Basic correction). Defaults come from the davigen settings or the project. |
| `check(asset)` | done / to do / not applicable / out of date, with a reason |
| `estimate(assets)` | seconds, for the time estimate; refined while it runs |
| `run(unit)` | does the work for one unit (usually one asset), reporting progress |
| `undo(unit)` | puts the unit back if it failed half-way (§6) |

The frontend has the matching registry: a step is drawn by one generic component; an action with a custom form names
it, and the UI looks it up. **A new action = one Python class, plus a React form only if the generic fields are not
enough.** The *apply to all* buttons on the assets page come for free: they start the same pipeline with one action
and all assets whose `check` says *to do*.

## 5. The pipeline

A **run** is: assets + chosen actions + inputs. From it the runner builds the steps:

1. order the actions by `after`;
2. ask every action's `check` which assets still need it – an action with nothing to do disappears, or shows as
   *already done (12)*;
3. a step for the move/copy only exists when files are really moved or copied, a conversion step only when there are
   files Resolve can't read well, and so on.

So the timeline in the UI is different for every run, as it should be: three new clips without any optional action
give three short steps; a whole card with Basic correction and a song gives ten.

Each step shows its state (waiting, **needs input**, running, done, skipped with the reason, failed), *n of m*, a
bar, the elapsed time and the time left. The estimate starts from `estimate()` and follows the real speed as soon as
the first units are done.

**Input.** When a step needs input that isn't settled yet, the runner **pauses there**: the step opens and shows its
form (for Basic correction: brightness, contrast, warmth, saturation, with the before/after pictures from the look
setup). Everything before it has run already; after *Continue* it goes on – for one clip or thirty. Inputs can also be
given before the start (the import screen offers the optional actions with their forms), then nothing pauses.

**Waiting for the user in Resolve.** The same mechanism covers steps that need a click in Resolve (§7.6): the step
explains exactly what to do, davigen watches Resolve and continues by itself once it's done.

**Stop, crash, resume.** Stop ends after the current unit. Every run is written to a journal
(`00_ADMIN/PROJECT_INFO/runs/<date>.json`) before each unit and after it. After a crash davigen finds the open run on
the next start, undoes the unit that was half-done, and offers to continue the rest.

## 6. Safety: what "atomic" means here

Undoing *everything* when anything fails would throw away finished work: if the 31st of 40 clips can't be corrected,
the 30 corrected clips are fine and shouldn't be redone. So there are three levels:

1. **The unit is atomic.** A clip's move (with its sidecar files), a clip's import and tagging, a clip's
   `DAVIGEN_AUTO`, a song's markers: either completely or not at all. Every unit writes its intent to the journal
   before it starts and knows how to undo itself.
2. **Getting assets in is one transaction.** Move/copy, conversion and import belong together: if any of them fails
   (a disk full, Resolve refuses a file, a crash), every file goes back to where it was and nothing half-imported
   stays in the Media Pool. That's today's transfer journal, extended to the import.
3. **Actions after that keep what they finished.** A failure stops that action (or skips the one asset, depending
   on the action), is shown with the reason, and the next run picks up exactly the rest – because `check` knows what
   is done.

Checked before anything starts: free space (transfer, conversions), that the project is still the one open
in Resolve, that the source files are readable. Only then does the first file move.

## 7. The actions in detail

| Action | Kinds | Mandatory | Inputs |
|---|---|---|---|
| 7.1 Classify | all | yes | kind per file (changeable) |
| 7.2 Bring in (move / copy / leave) | all | yes | – (setting) |
| 7.3 Make importable | audio, graphics | yes | – |
| 7.4 Import and sort | all but LUTs, fonts | yes | – |
| 7.5 Colour: groups, input/output transforms, node structure, assembly timeline | camera clips | yes | camera profile (as today) |
| 7.7 Basic correction | camera clips | chosen | the look |
| 7.8 Song markers | music | chosen | which marker kinds |

**7.3 Make importable.** Resolve crackled on a 44.1 kHz FLAC with a cover picture on MARSEILLE; as 48 kHz / 24-bit
WAV it played clean. Audio that isn't 48 kHz PCM, or carries a picture, gets a 48 kHz WAV next to it, and the WAV is
imported; the original stays. Images Resolve can't read (webp, animated gif) are converted the same way. Needs ffmpeg
(§11).

**7.5 Colour.** Today's code (groups, LUTs, node structure, assembly timeline), called per new camera group and new
clip – already idempotent today.

**7.6 Proxies (no action).** davigen doesn't make proxies (decided 2026-09-30). It makes sure that when they are
made – in Resolve, from the Media Pool or the Media page – they land in the project: the proxy folder is
`03_WORK/PROXIES` (template, §2), the proxy resolution comes from the settings (API). The same goes for every other
place Resolve writes to: cache `03_WORK/CACHE`, project media `03_WORK/PROJECT_MEDIA`, gallery `02_RESOLVE/03_GALLERY`.
The project check (§8) reads them back and says if one points outside the project.

**7.7 Basic correction.** The existing code, unchanged in what it measures and decides. The action runs it on the
assembly timeline (every camera clip is on it) and writes only the assets of this run; clips that have
`DAVIGEN_AUTO` are skipped, as today. Afterwards the existing *carry* puts it on every other timeline. Its input is
the look (already stored per project).

**7.8 Song markers.** Markers on the music clip itself (they show wherever the song is used), each kind in its own
colour and replaceable by davigen without touching the user's markers:

| Kind | Where | Colour |
|---|---|---|
| Bars | every downbeat | Cyan |
| Phrases and parts | every 4 / 8 bars, and where the song changes character (verse → chorus, drop, calm part) | Purple |
| Accents | hits that stand out clearly from the beat around them (snare, crash, drop) | Yellow |
| Vocal entries | where a sung line starts after a pause | Pink |

Bars and parts exist today (numpy, beat tracking after Ellis 2007). Accents and vocal entries are new, pure signal
analysis, no model: accents are onset peaks well above their neighbourhood; vocal entries look for harmonic energy in
the voice band starting after a pause. Vocal entries will be the least reliable kind and are labelled so.

## 8. Project setup

The setup is an import into an empty project, with the project-wide steps in front:

1. project folder;
2. Resolve project from the template (§2) – playback frame rate and proxy folder included, and the codecs the API
   refuses once they are mapped the same way;
3. every other setting through the API: timeline frame rate and resolution, colour management, input scaling,
   monitoring format, cache folder `03_WORK/CACHE`, project media folder `03_WORK/PROJECT_MEDIA`, gallery, proxy
   resolution, render cache;
4. bins, timelines with named tracks, render presets;
5. **the same asset pipeline as every later import.**

What it still can't set is listed as a manual step with the exact click path and checked afterwards; after the
setup the only thing left to do in Resolve should be to edit.

## 9. User interface

- **Import** (one screen for *New project* and *Add to project*): choose files and folders → the list of assets by
  kind (changeable) → the optional actions as ticks, each with its form folded in → *Start*. *New project* has the
  name and format steps in front, as today.
- **The run view**: the dynamic timeline of §5, reused everywhere something runs.
- **Assets** (new page of the open project): every asset by kind, with a small status per action (colour ✓, Basic
  correction ✓, markers –), filters, and per action *Apply to all that need it* / *Apply to selection*.
- **Edit assist** disappears; *Song markers* lives on the assets page.
- **Projects**: *Delete…* per project (§10).

## 10. Deleting a project

*Delete…* on a project asks with the project name typed in, then:

1. if it is open in Resolve, saves and closes it;
2. exports it once more as `.drp` into its own folder (so *Put Back* from the Trash restores everything);
3. deletes it from Resolve's Project Manager;
4. moves the whole project folder to the Trash through Finder (footage in `01_MEDIA`, proxies, exports – everything);
   footage imported with *leave in place* is outside the folder and stays untouched;
5. removes it from davigen's list.

## 11. Settings

New in davigen's settings: move / copy / leave (for setup and import alike), which optional actions are ticked by
default, the default song-marker kinds, and the proxy resolution Resolve uses when you make proxies. ffmpeg becomes a fixed requirement (audio and image
conversion, song analysis); the installer checks for it.

## 12. Order of work

1. Pipeline core: action interface, registry, runner (steps, estimates, pause for input, stop, journal, resume),
   asset record – test-first against the fake Resolve.
2. Project template (§2) and the setup steps on the pipeline.
3. Assets: classify, bring in, make importable, import and sort; *New project* and *Add footage* become the one import.
4. Actions: colour, Basic correction (wrapped), song markers (new kinds).
5. UI: run view, import screen, assets page, settings, delete project.
6. Remove Edit assist and its dependencies; README and WORKFLOW.
7. Real run in Resolve on a test project (new project, add later, remove in Resolve, add again, crash in the middle),
   then on a copy of MARSEILLE_2026.
