# 06 · Proxy check: new, changed, orphaned

**Goal:** *Check proxies* finds clips without a proxy, stale proxies, lost or foreign links and orphaned files,
makes or relinks what is needed and reports orphans, which go to the Trash only on request (concept §6.9). It runs
as its own command, at the end of *Add footage* and inside *Check & repair*.

**References:**

- concept §6.9
- step 05's `plan`, `record`, `link`
- [`davigen/media_pool.py`](../../../davigen/media_pool.py) `davigen_clips`, `all_clip_paths`
- [`davigen/creator.py`](../../../davigen/creator.py) `add_footage`
- step 04's `trash.py`

## How it is developed

Test-first, the comparison is pure:

1. `proxy/reconcile.py`: `reconcile(clips, files, record, cfg) -> list[Entry]`, where `clips` are
   `(path, size, mtime, frames, fps, linked proxy path)` from the Media Pool, `files` the proxy folder listing, and
   `record` the JSON. States as in the concept table, one test per state, plus mixed cases.
2. Orphans: files under `03_WORK/PROXIES` that are neither in the record nor a mirror of a clip, grouped by folder
   with sizes, so Resolve's `ProxyMedia/` tree appears as one line.
3. Actions: `missing`, `stale`, `broken` and `foreign` become jobs for step 05's background job; `not linked` is
   linked right away; `old spec` only on request; orphans only listed.
4. `add_footage`: after the import, the check runs for the new clips and starts the job (or asks, question 13).
5. A **Check proxies** button (step 08) and `GET /api/proxy/check`, `POST /api/proxy/trash` (selected orphans).

## How it is checked

- Tests: every state; a clip whose original was replaced (newer mtime, other size) is stale; a changed frame count
  is stale; a record entry without a file is missing; a Resolve link into `ProxyMedia/` is foreign and its tree an
  orphan; trashing calls Finder for exactly the selected paths.
- **In Resolve**, on the MARSEILLE copy: add a new clip with *Add footage* (it gets a proxy), unlink one proxy by
  hand (it is relinked), rename one proxy file (broken → remade, the renamed one shows as orphan). Written down under
  *Result*.
