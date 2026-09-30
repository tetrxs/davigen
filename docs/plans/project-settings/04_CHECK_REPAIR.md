# 04 · Check & repair

**Goal:** a command that checks an existing project against its target state, shows a report with a traffic light
per item, and, after confirmation and a backup, fixes what can be fixed, with undo (concept §7).

**References:**

- concept §7
- [`davigen/creator.py`](../../../davigen/creator.py) `project_base`, `project_format`, `Reporter`, `run`
- [`davigen/filesystem.py`](../../../davigen/filesystem.py) `read_project_info`
- [`uninstall.sh`](../../../uninstall.sh) `trash()` (moving to the Trash through Finder)
- step 02's `desired` and `compare`

## How it is developed

Test-first against the fake Resolve:

1. `davigen/health.py`:
   - `read(resolve, cfg) -> State`: project base, format, settings (`GetSetting()`), later the proxy and media
     states from 06 and 07
   - `report(state, desired) -> list[Item]` with `status` in `ok / fix / manual / info / error`, `now`, `after`,
     `instructions`
   - `apply(resolve, items, selected, base) -> Journal`: export the backup first (stop if it fails), write the
     journal, apply, save
   - `undo(resolve, journal)`: old values back, old proxy links back
2. `davigen/trash.py`: `to_trash(path)` via Finder (`osascript`), tested with the command mocked; never
   `os.remove` / `shutil.rmtree` on anything but davigen's own `.part` files (grep test).
3. **Flows** in `creator.py`: `check_project` (read only) and `repair_project(selected)`, with `Reporter` steps
   `read → backup → apply → save → check`.
4. **Server:** `GET /api/health` (the report), `POST /api/health/repair` (selected item ids),
   `POST /api/health/undo`.
5. **Docs:** README *What it does* and a new section in WORKFLOW (*Checking a project*).

## How it is checked

- Tests: a MARSEILLE-like fake project (playback 24, cache elsewhere, a Resolve proxy link) gives the expected
  items; nothing changes without `apply`; `apply` fails cleanly when the backup fails; `undo` restores every value;
  an older `davigen.json` without the new fields falls back to today's standard and says so.
- **In Resolve**, on a copy of MARSEILLE_2026 (`dev_project_copy.py`): the report shows playback 24 → 25 as manual
  and the cache location; the backup `.drp` lands in `02_RESOLVE/02_BACKUPS` and imports as a separate project;
  *Undo* restores the settings. Written down under *Result*.
