# 05 · Making and linking proxies

**Goal:** davigen makes small proxies with ffmpeg, mirrored under `03_WORK/PROXIES`, with a size guard and a
free-space check, in the background, stoppable and resumable, and links them in Resolve (concept §6.1–6.8, §6.10).

**References:**

- concept §6
- [`davigen/edit/decode.py`](../../../davigen/edit/decode.py) `tool`, `probe`, `_run` (ffmpeg discovery, hardware
  decode with software fallback)
- [`davigen/transfer.py`](../../../davigen/transfer.py) (`.part` + rename, journaling pattern)
- [`davigen/creator.py`](../../../davigen/creator.py) `_transfer` (free-space check), `Reporter`
- the spike's proxy answers (01, checks 8–11)

## How it is developed

1. **Plan** (`proxy/plan.py`), test-first, no ffmpeg needed:
   - `needs_proxy(clip, cfg)`; `choose_size(w, h, fps, source_bytes, duration, cfg)` returns the ratio and size, or
     *none* with the reason (guard)
   - `estimate_bytes(w, h, fps, duration, cfg)`
   - `proxy_path(base, clip_path)`: mirroring, name collisions, `_EXTERNAL/<parent>_<hash8>`
   - `space_ok(plans, free_bytes, cfg)`; `total_ok(plans, cfg)`
   - heavy clips first in the order
2. **Record** (`proxy/record.py`): read, update, atomic write (temp file + rename) of `proxies.json`.
3. **Encode** (`proxy/make.py`):
   - `command(src, dst, probe, size, cfg) -> list[str]`: tested as an argument list (range, tags, timecode,
     passthrough, audio)
   - `encode(job, progress, stop)`: runs ffmpeg with `-progress pipe:1`, extrapolates the size (guard at ≥ 10 %),
     honours `stop`, writes `.part.mov`, checks the result with ffprobe (frames, fps, duration, timecode, audio
     channels, range), renames
   - encoder choice: `prores_videotoolbox` if `ffmpeg -encoders` lists it, else `prores_ks`
   - tests with small generated clips (`ffmpeg -f lavfi testsrc2`, 10-bit 4:2:2 HEVC or H.264 with timecode and
     audio) where ffmpeg is installed, skipped otherwise
4. **Link** (`proxy/link.py`): `link(item, path)`, `state(item)` from the clip properties the spike found.
5. **Background job:** a job slot in `server.py` next to the flows: progress (`/api/proxy/progress`), stop
   (`/api/proxy/stop`), start (`/api/proxy`), resume on start. Links are made only when the same project is open.
   The idle shutdown waits while a job runs.
6. **Wiring:** `new_project` starts it at the end if *Make proxies* is on; Resolve's own proxy settings come from
   02.
7. **Docs:** README (What it does, Troubleshooting: ffmpeg), WORKFLOW §4 step 1 rewritten.

## How it is checked

- Tests: plan (the MARSEILLE cameras as cases: 5952 × 3968 25p → ¼; 3840 × 2160 59.94 → per guard; 1920 × 1080 →
  none), paths, guard (a fake progress stream that overshoots stops the clip), space check, resume (complete ones
  skipped, `.part` discarded), command lists, encode of generated clips (frame count, timecode and audio equal).
- **In Resolve**, on a copy of MARSEILLE_2026 with ten clips: proxies made, linked, *Prefer proxies* plays them
  (Clip Attributes / the clip's *Proxy* column), sizes reported, restart keeps the links, a render with proxies on
  matches one with proxies off (spike check 11 repeated). Stop and resume once mid-clip. Written down under
  *Result*.
