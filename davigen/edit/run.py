"""The Edit Assist flow: watch → selects → markers → selects timeline → (music → rough cut) → save."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from .. import media_pool
from ..config import Config
from ..resolve_api import ResolveError, ensure_bin
from . import apply, decode, music as music_mod, preview, roughcut, selects as sel, settings as settings_mod, \
    transcribe, watch

STEPS = [
    ("watch", "Watch every clip"),
    ("selects", "Selects"),
    ("transcribe", "Transcribe speech (Whisper)"),
    ("markers", "Markers on the clips"),
    ("music", "Music: beats, bars, sections"),
    ("roughcut", "Rough cut to music"),
    ("grade", "Basic correction on the rough cut"),
    ("preview", "Preview video"),
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


def edit_assist(resolve, cfg: Config, rep, music_path: str = "", base: Path | None = None,
                transcribe_speech: bool = False, music_seconds: float = 0.0, pace: str = "auto") -> dict:
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
    for n, mpi in enumerate(clips):
        w = watched.get(n)
        if w is None:
            continue
        plans.append(roughcut.ClipPlan(id=str(n), name=mpi.GetName(), created=_created(mpi), order=n,
                                       segments=sel.segments(w, s["selects"]), watch=w))

    def total(kind):
        return sum(x.length for p in plans for x in p.segments if x.kind == kind) / 60
    rep.finish("selects", f"{total(sel.GOOD):.1f} min good, {total(sel.UNUSABLE):.1f} min unusable, "
               f"{total(sel.SPEECH):.1f} min speech")

    # ------------------------------------------------------------------------------------- transcribe
    tr = s["transcribe"]
    if transcribe_speech and tr["enabled"] and any(x.kind == sel.SPEECH for p in plans for x in p.segments):
        rep.start("transcribe")
        written = _transcribe(plans, clips, base, tr, rep)
        rep.finish("transcribe", written)
    else:
        rep.finish("transcribe", "no speech" if transcribe_speech else "not chosen", state="skipped")

    # ---------------------------------------------------------------------------------------- markers
    rep.start("markers")
    marked = sum(apply.mark_clip(clips[int(p.id)], p.segments, apply.clip_fps(clips[int(p.id)], tl_fps))
                 for p in plans)
    flags = [apply.tag_clip(clips[int(p.id)], p.segments, p.watch.duration, s["clip_flags"]) for p in plans]
    rep.finish("markers", f"{marked} markers on {len(plans)} clips · flags: {flags.count('Green')} green, "
               f"{flags.count('Red')} red · keywords 'davigen good' / 'davigen speech' for the Media Pool search")

    # the best stretches, for the record (the Edit assist page lists them; in Resolve they are the green markers,
    # visible on the clips in every timeline, and the 'davigen good' keyword)
    fmt = project_format(cfg, proj, base)
    st = s["selects_timeline"]
    chosen = []
    for plan in sorted(plans, key=lambda p: (p.created, p.order)):
        for seg in (x for x in plan.segments if x.kind == sel.GOOD and x.rating >= st["min_rating"]):
            a, b = sel.calmest_window(plan.watch, seg, min(seg.length, st["max_seconds"]))
            chosen.append({"clip": plan.id, "name": plan.name, "start": round(a, 2), "end": round(b, 2),
                           "rating": round(seg.rating, 3)})

    # ----------------------------------------------------------------------------------- music + cut
    result = {"selects": chosen, "rough_cut": "", "music": None}
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
        a, b = roughcut.music_window(track, music_seconds)
        part = roughcut.cut_music(track, a, b) if (a, b) != (0.0, track.duration) else track
        rep.finish("music", f"{track.tempo:.0f} BPM · {len(track.downbeats)} bars · {len(track.sections)} sections"
                   + (f" · using {a:.0f}–{b:.0f} s" if part is not track else ""))
        rep.start("roughcut")
        shots = roughcut.plan(plans, part, roughcut.with_pace(s["rough_cut"], pace))
        tl, cut_name = apply.edit_timeline(proj, cfg, fmt)
        rep.warn(apply.rough_cut(proj, tl, shots, {p.id: clips[int(p.id)] for p in plans}, music_mpi, tl_fps, a))
        length = shots[-1].record_end if shots else 0
        rep.finish("roughcut", f"{cut_name}: {len(shots)} shots, {length:.0f} s"
                   + (f" ({pace} pace)" if pace != "auto" else ""))
        result.update(rough_cut=cut_name, music=track.to_dict(), music_file=music_path, music_window=[a, b],
                      pace=pace, shots=[x.to_dict() for x in shots])
        proj.SetCurrentTimeline(tl)
        _grade(proj, base, [tl], rep)
        rep.start("preview")
        try:
            clips_info = [{"id": p.id, "path": clips[int(p.id)].GetClipProperty("File Path")} for p in plans]
            target = preview_path(base, cut_name)
            preview.render(base, {**result, "clips": clips_info}, target,
                           progress=lambda i, n, name: rep.detail("preview", f"{i}/{n} · {name}"))
            result["preview"] = str(target)
            rep.finish("preview", str(target.relative_to(base)))
        except Exception as e:  # noqa: BLE001 - the timeline is there; the preview is a bonus
            rep.finish("preview", f"not made: {e}", state="skipped")
    else:
        rep.finish("music", "no music chosen", state="skipped")
        rep.finish("roughcut", "choose a music file for a rough cut", state="skipped")
        rep.finish("grade", "only with a rough cut", state="skipped")
        rep.finish("preview", "only with a rough cut", state="skipped")

    # ------------------------------------------------------------------------------------------- save
    rep.start("save")
    record = {"date": datetime.now().isoformat(timespec="seconds"), "settings": s, **result,
              "clips": [{"id": p.id, "name": p.name, "created": p.created,
                         "path": clips[int(p.id)].GetClipProperty("File Path"),
                         "duration": round(p.watch.duration, 2) if p.watch is not None else 0,
                         "segments": [x.to_dict() for x in p.segments]} for p in plans]}
    target = base / "00_ADMIN" / "PROJECT_INFO" / "edit_assist.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(record, indent=1, default=str), encoding="utf-8")
    resolve.GetProjectManager().SaveProject()
    rep.finish("save", "00_ADMIN/PROJECT_INFO/edit_assist.json")
    rep.result = result
    return record


def _grade(proj, base: Path, timelines: list, rep) -> None:
    """The clips of davigen's new timelines get the Basic correction their camera files already have."""
    from ..basic import carry  # noqa: PLC0415
    timelines = [t for t in timelines if t is not None]
    rep.start("grade")
    if not timelines or not carry.graded_clips(base)[0]:
        rep.finish("grade", "no Basic correction yet – run it once, then 'On every timeline'", state="skipped")
        return
    try:
        counts = carry.carry_over(proj, base, timelines)
    except Exception as e:  # noqa: BLE001 - the timelines are there; the grade can be added later
        rep.finish("grade", f"not done: {e}", state="skipped")
        return
    rep.warn(counts["warnings"][:10])
    rep.finish("grade", carry.summary(counts))


def _merge_speech(segs: list[sel.Segment], gap: float = 1.0) -> list[list[sel.Segment]]:
    groups: list[list[sel.Segment]] = []
    for x in sorted((x for x in segs if x.kind == sel.SPEECH), key=lambda x: x.start):
        if groups and x.start - groups[-1][-1].end <= gap:
            groups[-1].append(x)
        else:
            groups.append([x])
    return groups


def _transcribe(plans: list, clips: list, base: Path, tr: dict, rep) -> str:
    """Whisper on every speech stretch; text into the segments, SRT per clip, one searchable transcript."""
    if not transcribe.supported():
        rep.warn(["Transcription needs a Mac with Apple Silicon"])
        return "not supported on this Mac"
    if not transcribe.available():
        rep.detail("transcribe", "installing mlx-whisper into davigen's Python (once)")
        err = transcribe.install()
        if err:
            rep.warn([f"Couldn't install mlx-whisper: {err}"])
            return "mlx-whisper couldn't be installed"
    jobs, owners = [], []
    for p in plans:
        path = clips[int(p.id)].GetClipProperty("File Path")
        for group in _merge_speech(p.segments):
            jobs.append({"path": path, "start": max(0.0, group[0].start - tr["pad"]),
                         "end": group[-1].end + tr["pad"]})
            owners.append((p, group))
    rep.detail("transcribe", f"{len(jobs)} stretches · first run downloads the model (~1.6 GB)")
    results = transcribe.run(jobs, tr["model"])
    folder = base / "03_WORK" / "TRANSCRIPTS"
    folder.mkdir(parents=True, exist_ok=True)
    lines, words, dropped = ["# Transcripts", ""], 0, 0
    by_clip: dict[str, list] = {}
    for (p, group), res in zip(owners, results):
        if not res["segments"]:
            for x in group:                          # Whisper heard no words: not speech after all
                p.segments.remove(x)
            dropped += len(group)
            continue
        group[0].text = res["text"]
        group[0].end = group[-1].end
        for x in group[1:]:
            p.segments.remove(x)
        by_clip.setdefault(p.name, []).extend(res["segments"])
        words += len(res["text"].split())
        lines.append(f"- **{p.name}** {_clock(group[0].start)} ({res['language']}): {res['text']}")
    for name, entries in by_clip.items():
        (folder / f"{Path(name).stem}.srt").write_text(transcribe.srt(entries), encoding="utf-8")
    (base / "00_ADMIN" / "PROJECT_INFO" / "transcripts.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return f"{words} words in {len(by_clip)} clips · {dropped} stretches were no speech · 03_WORK/TRANSCRIPTS"


def _clock(t: float) -> str:
    return f"{int(t // 60)}:{int(t % 60):02d}"


def media_pool_folder_clips(mp, path: str) -> list:
    folder = ensure_bin(mp, path)
    return folder.GetClipList() or []


def preview_path(base: Path, cut_name: str) -> Path:
    return base / "03_WORK" / "PREVIEWS" / f"{cut_name}.mp4"


PREVIEW_STEPS = [("preview", "Preview video of the rough cut")]


def preview_flow(resolve, cfg: Config, options: dict, rep) -> None:
    """Render (again) the preview of the last rough cut – no Resolve needed."""
    base = Path(options["base"])
    target_file = base / "00_ADMIN" / "PROJECT_INFO" / "edit_assist.json"
    record = json.loads(target_file.read_text(encoding="utf-8"))
    rep.start("preview")
    out = preview.render(base, record, preview_path(base, record.get("rough_cut") or "rough_cut"),
                         progress=lambda i, n, name: rep.detail("preview", f"{i}/{n} · {name}"))
    record["preview"] = str(out)
    target_file.write_text(json.dumps(record, indent=1, default=str), encoding="utf-8")
    rep.finish("preview", str(out.relative_to(base)))
    rep.result = {"preview": str(out)}


def flow(resolve, cfg: Config, options: dict, rep) -> None:
    edit_assist(resolve, cfg, rep, music_path=options.get("music", ""),
                transcribe_speech=bool(options.get("transcribe")),
                music_seconds=float(options.get("seconds") or 0), pace=options.get("pace") or "auto")

