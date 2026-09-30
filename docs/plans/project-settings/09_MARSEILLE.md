# 09 · Run on MARSEILLE_2026

**Goal:** the acceptance run from the brief, on the real project (after a rehearsal on a copy): *Check & repair*
reports the playback frame rate and the proxy situation correctly and fixes what can be fixed; the proxies end up
far smaller than the originals, mirrored under `03_WORK/PROXIES` and linked (concept §7.6).

**References:**

- concept §1, §7.6
- [`scripts/dev_project_copy.py`](../../../scripts/dev_project_copy.py) for the rehearsal
- the Basic Correction simulator comparison (`davigen/basic/evaluate.py`), if tetrahedral is adopted (concept §4.4)

## How it is done

1. **Rehearsal on a copy:** the whole run on `ZZ_DAVIGEN_TEST_MARSEILLE_2026`. Everything that goes wrong is fixed
   in its step before touching the real project.
2. **Before:** `GetSetting()` dump, proxy folder size and layout, clip count, originals' total size.
3. **Check:** the report, saved as JSON and as a screenshot.
4. **Repair:** confirm the fixable items; the backup lands in `02_RESOLVE/02_BACKUPS`; set the playback frame rate
   by hand as instructed, *Check again*.
5. **Proxies:** made in the background (time noted), linked; the old `ProxyMedia/` tree moved to the Trash on
   request (170 GB).
6. **After:** a new dump, sizes, a restart of Resolve, the check again (all green except accepted *info* rows).
7. **Playback:** the Edit page plays the 6K timeline with *Prefer proxies*; the audio doesn't crackle.
8. **Basic Correction**, if tetrahedral was switched on: the comparison run again; the median must stay near
   today's 1.35 %.
9. **A new project** from the same cards (a few clips) at 25 fps: no manual step besides the one-time template, if
   it isn't there yet.

## How it is checked

A *Result* section in this file with the numbers: originals vs proxies in GB (target: proxies well below the
originals, no single proxy above 50 % of its original), the report before and after, time taken, anything that
still needs a hand. `pytest` green, README and WORKFLOW current.
