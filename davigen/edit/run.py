"""The Edit Assist flow: watch → selects → markers → selects timeline → (music → rough cut) → save."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from .. import media_pool
from ..config import Config
from ..resolve_api import ResolveError, ensure_bin
from . import apply, decode, music as music_mod, roughcut, selects as sel, settings as settings_mod, watch

STEPS = [
    ("watch", "Watch every clip"),
    ("selects", "Selects + markers"),
    ("timeline", "Selects timeline"),
    ("music", "Music: beats, bars, sections"),
    ("roughcut", "Rough cut to music"),
    ("save", "Save"),
]
WORKERS = 2                     # ffmpeg processes at a time (hardware decoding is shared)


def _created(mpi) -> str:
    for key in ("Date Recorded", "Date Created", "Start TC"):
        try:
            value = mpi.GetClipProperty(key)
        except TypeError:
            value = ""
        if value:
            return str(value)
    return ""


def edit_assist(resolve, cfg: Config, rep, music_path: str = "", base: Path | None = None) -> dict:
    from ..creator import project_base, project_format  # noqa: PLC0415 - creator is heavy; only when used
    if not decode.available():
        raise ResolveError("Edit Assist needs ffmpeg to watch the clips – install it with 'brew install ffmpeg'")
    proj = resolve.GetProjectManager().GetCurrentProject()
    base = base or project_base(proj)
    s = settings_mod.load(cfg.workflow)
    tl_fps = float(proj.GetSetting("timelineFrameRate") or 25)
    clips = [c for c in media_pool.davigen_clips(proj.GetMediaPool().GetRootFolder())
             if c.GetClipProperty("File Path")]
    if not clips:
        raise ResolveError("No davigen clips in this project")

    # ------------------------------------------------------------------------------------------ watch
    rep.start("watch", f"{len(clips)} clips")
    cache = watch.Cache(base / "03_WORK" / "ANALYSIS" / "edit")
    watched: dict[int, watch.Watch] = {}
    errors = []

    def work(n_mpi):
        n, mpi = n_mpi
        try:
            return n, watch.watch(mpi.GetClipProperty("File Path"), cache), ""
        except Exception as e:  # noqa: BLE001 - one broken file mustn't stop the rest
            return n, None, f"{mpi.GetName()}: {e}"
    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for n, w, err in pool.map(work, enumerate(clips)):
            done += 1
            rep.detail("watch", f"{done}/{len(clips)} · {clips[n].GetName()}")
            if w is not None:
                watched[n] = w
            if err:
                errors.append(err)
    rep.warn([f"Couldn't watch {e}" for e in errors])
    minutes = sum(w.duration for w in watched.values()) / 60
    rep.finish("watch", f"{len(watched)} clips, {minutes:.0f} min of footage")

    # ---------------------------------------------------------------------------------------- selects
    rep.start("selects")
    plans: list[roughcut.ClipPlan] = []
    marked = good_s = bad_s = 0
    for n, mpi in enumerate(clips):
        w = watched.get(n)
        if w is None:
            continue
        segs = sel.segments(w, s["selects"])
        marked += apply.mark_clip(mpi, segs, apply.clip_fps(mpi, tl_fps))
        good_s += sum(x.length for x in segs if x.kind == sel.GOOD)
        bad_s += sum(x.length for x in segs if x.kind == sel.UNUSABLE)
        plans.append(roughcut.ClipPlan(id=str(n), name=mpi.GetName(), created=_created(mpi), order=n,
                                       segments=segs, watch=w))
    rep.finish("selects", f"{marked} markers · {good_s / 60:.1f} min good, {bad_s / 60:.1f} min unusable")

    # --------------------------------------------------------------------------------------- timeline
    rep.start("timeline")
    fmt = project_format(cfg, proj, base)
    stem = f"TL_00_SELECTS_AUTO_{fmt.aspect_token}_{fmt.fps_token}"
    st = s["selects_timeline"]
    picks = []
    for plan in sorted(plans, key=lambda p: (p.created, p.order)):
        for seg in (x for x in plan.segments if x.kind == sel.GOOD and x.rating >= st["min_rating"]):
            a, b = sel.calmest_window(plan.watch, seg, min(seg.length, st["max_seconds"]))
            picks.append((clips[int(plan.id)], a, b))
    name = apply.next_name(proj, stem)
    selects_tl = apply.selects_timeline(proj, name, "03_TIMELINES/01_ASSEMBLY", picks, tl_fps) if picks else None
    rep.finish("timeline", f"{name}: {len(picks)} stretches, {sum(b - a for _, a, b in picks) / 60:.1f} min"
               if picks else "no good stretches", state="done" if picks else "skipped")

    # ----------------------------------------------------------------------------------- music + cut
    result = {"selects_timeline": name if picks else "", "rough_cut": "", "music": None}
    if music_path:
        rep.start("music", Path(music_path).name)
        samples = decode.audio(music_path, music_mod.RATE)
        if len(samples) < music_mod.RATE * 5:
            raise ResolveError(f"{Path(music_path).name}: no usable audio")
        track = music_mod.analyse(samples)
        mp = proj.GetMediaPool()
        mp.SetCurrentFolder(ensure_bin(mp, "04_AUDIO/MUSIC"))
        existing = [c for c in (media_pool_folder_clips(mp, "04_AUDIO/MUSIC"))
                    if c.GetClipProperty("File Path") == music_path]
        music_mpi = existing[0] if existing else (mp.ImportMedia([music_path]) or [None])[0]
        if music_mpi is not None:
            apply.mark_music(music_mpi, track, apply.clip_fps(music_mpi, tl_fps))
        rep.finish("music", f"{track.tempo:.0f} BPM · {len(track.downbeats)} bars · {len(track.sections)} sections")
        rep.start("roughcut")
        shots = roughcut.plan(plans, track, s["rough_cut"])
        cut_name = apply.next_name(proj, f"TL_02_EDIT_AUTO_{fmt.aspect_token}_{fmt.fps_token}")
        tl, warns = apply.rough_cut_timeline(proj, cut_name, "03_TIMELINES/02_EDIT", shots,
                                             {p.id: clips[int(p.id)] for p in plans}, music_mpi, tl_fps)
        rep.warn(warns)
        length = shots[-1].record_end if shots else 0
        rep.finish("roughcut", f"{cut_name}: {len(shots)} shots, {length:.0f} s of {track.duration:.0f} s music")
        result.update(rough_cut=cut_name, music=track.to_dict(), shots=[x.to_dict() for x in shots])
        if tl is not None:
            proj.SetCurrentTimeline(tl)
    else:
        rep.finish("music", "no music chosen", state="skipped")
        rep.finish("roughcut", "choose a music file for a rough cut", state="skipped")
        if selects_tl is not None:
            proj.SetCurrentTimeline(selects_tl)

    # ------------------------------------------------------------------------------------------- save
    rep.start("save")
    record = {"date": datetime.now().isoformat(timespec="seconds"), "settings": s, **result,
              "clips": [{"name": p.name, "created": p.created, "segments": [x.to_dict() for x in p.segments]}
                        for p in plans]}
    target = base / "00_ADMIN" / "PROJECT_INFO" / "edit_assist.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(record, indent=1, default=str), encoding="utf-8")
    resolve.GetProjectManager().SaveProject()
    rep.finish("save", "00_ADMIN/PROJECT_INFO/edit_assist.json")
    rep.result = result
    return record


def media_pool_folder_clips(mp, path: str) -> list:
    folder = ensure_bin(mp, path)
    return folder.GetClipList() or []


def flow(resolve, cfg: Config, options: dict, rep) -> None:
    edit_assist(resolve, cfg, rep, music_path=options.get("music", ""))

