"""The Basic Correction flow: sample → measure → correct → scenes → write → save (plan step 08).

Runs on one timeline of the open davigen project (the current one by default). Used by the home screen, the
New project wizard and the 'davigen Basic Correction' menu entry.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np

from .. import mediainfo, scanner
from ..config import LUT_DIR, Config
from ..media_pool import META_GROUP
from ..resolve_api import ResolveError, find_timeline
from . import correct as c
from . import measure as ms
from . import pipeline as p
from . import sampling, scenes, settings as settings_mod, write

STEPS = [
    ("sample", "Sample frames"),
    ("measure", "Measure"),
    ("correct", "Corrections per node"),
    ("scenes", "Scenes + matching"),
    ("write", "Write DAVIGEN_AUTO + markers"),
    ("save", "Save"),
]


class Item:
    """One clip on the timeline, with everything Basic Correction learns about it."""

    def __init__(self, ti, order: int, timeline_fps: float):
        self.ti, self.order = ti, order
        self.mpi = ti.GetMediaPoolItem()
        self.id = ti.GetUniqueId() if hasattr(ti, "GetUniqueId") else f"{order}"
        self.name = ti.GetName()
        self.path = self.mpi.GetClipProperty("File Path") if self.mpi else ""
        self.group = (self.mpi.GetThirdPartyMetadata(META_GROUP) if self.mpi else "") or ""
        self.fps = _number(self.mpi.GetClipProperty("FPS")) if self.mpi else 0.0
        self.fps = self.fps or timeline_fps
        self.total_frames = int(_number(self.mpi.GetClipProperty("Frames"))) if self.mpi else 0
        # used source range: start + duration (GetSourceEndFrame is inconsistent, step 01); the timeline
        # counts in its own frame rate, the source in the clip's
        self.start = _source_start(ti)
        self.duration = max(1, round(ti.GetDuration() * self.fps / (timeline_fps or self.fps)))
        self.timeline_start = ti.GetStart()
        self.frames: list[int] = []
        self.thumbs: list[np.ndarray] = []           # DaVinci Intermediate
        self.luts: tuple[Path | None, Path | None] = (None, None)
        self.meta = ms.ClipMeta()
        self.measurement: ms.Measurement | None = None
        self.shot: scenes.Shot | None = None
        self.outcome: write.Outcome | None = None


def basic_correction(resolve, cfg: Config, rep, dry_run: bool = False, recompute: bool = False,
                     timeline_name: str = "", base: Path | None = None) -> dict:
    """Run the whole flow on a timeline. Returns the record that is also saved as JSON."""
    from ..creator import project_base  # noqa: PLC0415 - creator imports this module for the wizard
    proj = resolve.GetProjectManager().GetCurrentProject()
    if proj is None:
        raise ResolveError("No project is open")
    base = base or project_base(proj)
    s = settings_mod.load(cfg.workflow)
    timeline = find_timeline(proj, timeline_name) if timeline_name else proj.GetCurrentTimeline()
    if timeline is None:
        raise ResolveError(f"Timeline {timeline_name or '(current)'} not found")
    tl_fps = _number(timeline.GetSetting("timelineFrameRate")) or _number(proj.GetSetting("timelineFrameRate"))

    # ----------------------------------------------------------------------------------------- sample
    rep.start("sample", timeline.GetName())
    items = [Item(ti, n, tl_fps) for n, ti in enumerate(_video_items(timeline))]
    usable = [i for i in items if i.mpi and i.group and i.path]
    luts = _group_luts(proj)
    cache = sampling.Cache(base / "03_WORK" / "ANALYSIS")
    todo: dict[tuple, sampling.Request] = {}
    cached: dict[str, dict[int, np.ndarray]] = {}
    for it in usable:
        it.luts = luts.get(it.group, (None, None))
        it.frames = sampling.choose_frames(it.start, it.duration, it.fps, s["samples"])
        have = cached.setdefault(it.path, cache.load(it.path))
        for f in it.frames:
            if f not in have and (it.path, f) not in todo:
                todo[(it.path, f)] = sampling.Request((it.path, f), it.mpi, f, sampling.span_for(it.fps, tl_fps))
    rendered: dict[tuple, np.ndarray] = {}
    if todo:
        rep.detail("sample", f"rendering {len(todo)} frames of {len({k[0] for k in todo})} clips")
        rendered = sampling.render(resolve, proj, list(todo.values()), s["analysis_width"],
                                   progress=lambda pct: rep.detail("sample", f"rendering {len(todo)} frames · {pct} %"),
                                   restore_preset=_master_preset(proj))
        by_path: dict[str, dict[int, np.ndarray]] = {}
        for (path, f), thumb in rendered.items():
            by_path.setdefault(path, {})[f] = thumb
        for path, frames in by_path.items():
            cache.save(path, frames)
            cached[path].update(frames)
    for it in usable:
        if it.luts[0] is None or it.luts[1] is None:
            continue
        logs = [sampling.to_float(cached[it.path][f]) for f in it.frames if f in cached[it.path]]
        it.thumbs = [p.apply_lut(t, it.luts[0]) for t in logs]
    measurable = [i for i in usable if i.thumbs]
    total = sum(len(i.thumbs) for i in measurable)
    source = ("all rendered now" if len(rendered) >= total else "all from the cache" if not rendered
              else f"{len(rendered)} rendered now, the rest from the cache")
    rep.finish("sample", f"{total} frames of {len(measurable)} clips ({source})"
               + (f" · {len(usable) - len(measurable)} clips without a davigen group LUT" if len(usable) > len(measurable) else ""))

    # ---------------------------------------------------------------------------------------- measure
    rep.start("measure")
    metas = _clip_meta(cfg, [i.path for i in measurable])
    for n, it in enumerate(measurable, 1):
        rep.detail("measure", f"{n}/{len(measurable)} · {it.name}")
        it.meta = metas.get(it.path, ms.ClipMeta())
        it.measurement = ms.measure(it.thumbs, it.meta, it.luts[1], s)
    rep.finish("measure", f"{len(measurable)} clips")

    # ---------------------------------------------------------------------------------------- correct
    rep.start("correct")
    for n, it in enumerate(measurable, 1):
        rep.detail("correct", f"{n}/{len(measurable)} · {it.name}")
        corr = c.correct(it.measurement, it.thumbs, it.luts[1], s)
        it.shot = scenes.Shot(id=it.id, order=it.order, meta=it.meta, measurement=it.measurement, correction=corr,
                              used_seconds=it.duration / it.fps, clip_seconds=it.total_frames / it.fps)
    rep.finish("correct")

    rep.start("scenes")
    shots = [i.shot for i in measurable]
    scenes.match_scenes(shots, s)
    rep.finish("scenes", f"{len({sh.scene for sh in shots})} scenes" if shots else "no clips")

    # ------------------------------------------------------------------------------------------ write
    rep.start("write", "dry run: markers and report only" if dry_run else "")
    old = write.load_record(base, timeline.GetName())
    old_versions = {e["id"]: e.get("outcome", {}).get("user_version", "") for e in old.get("items", [])}
    write.clear_markers([i.ti for i in items])
    threshold = s["confidence_flag_below"]
    for n, it in enumerate(items, 1):
        rep.detail("write", f"{n}/{len(items)} · {it.name}")
        if it.shot is None:
            it.outcome = write.Outcome(skipped=write.NOT_IN_GROUP if not it.group else "no frames")
            reason = write.NOT_IN_GROUP if not it.group else it.outcome.skipped
            it.outcome.marker = write.mark(it.ti, [], None, threshold, reason) if it.mpi else ""
            continue
        corr = it.shot.correction
        it.outcome = write.write_item(proj, it.ti, corr, recompute=recompute, dry_run=dry_run,
                                      previous_user_version=old_versions.get(it.id, ""))
        it.outcome.marker = write.mark(it.ti, corr.flags, corr.overall, threshold, it.outcome.skipped)
        for w in it.outcome.warnings:
            rep.warn([f"{it.name}: {w}"])
    written = sum(1 for i in items if i.outcome and i.outcome.written)
    rep.finish("write", f"{written} clips written into {write.AUTO}, "
               f"{sum(1 for i in items if i.outcome and i.outcome.marker)} markers")

    # ------------------------------------------------------------------------------------------- save
    rep.start("save")
    record = _record(proj, timeline, items, s, dry_run, recompute)
    path = write.save_record(base, timeline.GetName(), record)
    if not dry_run:
        resolve.GetProjectManager().SaveProject()
    rep.finish("save", str(path.relative_to(base)) if path.is_relative_to(base) else str(path))
    rep.result = {"record": str(path), "timeline": timeline.GetName(), "rows": rows(record)}
    return record


# -------------------------------------------------------------------------------------------- helpers

def _video_items(timeline) -> list:
    out = []
    for idx in range(1, timeline.GetTrackCount("video") + 1):
        out += timeline.GetItemListInTrack("video", idx) or []
    return out


def _source_start(ti) -> int:
    for name in ("GetSourceStartFrame", "GetLeftOffset"):
        try:
            value = getattr(ti, name)()
            if value is not None:
                return int(value)
        except (AttributeError, TypeError, ValueError):
            continue
    return 0


def _number(value) -> float:
    try:
        return float(str(value).split()[0])
    except (TypeError, ValueError, IndexError):
        return 0.0


def _lut_file(rel: str) -> Path | None:
    if not rel:
        return None
    path = Path(rel)
    if not path.is_absolute():
        path = LUT_DIR.parent / rel
    return path if path.exists() else None


def _group_luts(proj) -> dict[str, tuple[Path | None, Path | None]]:
    """Group name → (input LUT, output LUT) as set in its Pre-Clip / Post-Clip graphs."""
    out = {}
    for g in proj.GetColorGroupsList() or []:
        if not hasattr(g, "GetPreClipNodeGraph"):
            continue
        pre, post = g.GetPreClipNodeGraph(), g.GetPostClipNodeGraph()
        out[g.GetName()] = (_lut_file(pre.GetLUT(1) if pre else ""), _lut_file(post.GetLUT(1) if post else ""))
    return out


def _master_preset(proj) -> str:
    names = [pr if isinstance(pr, str) else pr.get("PresetName", "") for pr in proj.GetRenderPresetList() or []]
    return next((n for n in names if n.startswith("DAVIGEN_") and "MASTER" in n), "")


def _clip_meta(cfg: Config, paths: list[str]) -> dict[str, ms.ClipMeta]:
    """Recording time, ISO, aperture, shutter and white balance from the files (exiftool)."""
    if not paths or not mediainfo.available():
        return {}
    unique = list(dict.fromkeys(paths))
    tags = mediainfo.read(unique)
    out = {}
    for path in unique:
        try:
            info = scanner.analyse(path, cfg, tags.get(path, {}))
            out[path] = ms.ClipMeta.from_clip_info(info)
        except Exception:  # noqa: BLE001 - metadata is optional
            continue
    return out


def _record(proj, timeline, items: list[Item], s: dict, dry_run: bool, recompute: bool) -> dict:
    entries = []
    for it in items:
        shot = it.shot
        entries.append({
            "id": it.id, "name": it.name, "path": it.path, "group": it.group,
            "timeline_start": it.timeline_start, "source_start": it.start, "source_frames": it.duration,
            "clip_fps": it.fps,
            "frames": it.frames, "meta": vars(it.meta),
            "measurement": it.measurement.to_dict() if it.measurement else None,
            "correction": shot.correction.to_dict() if shot else None,
            "scene": shot.scene if shot else None, "hero": shot.hero if shot else False,
            "scene_notes": shot.notes if shot else {},
            "outcome": vars(it.outcome) if it.outcome else {},
        })
    return {"project": proj.GetName(), "timeline": timeline.GetName(),
            "fps": _number(timeline.GetSetting("timelineFrameRate")),
            "date": datetime.now().isoformat(timespec="seconds"), "dry_run": dry_run, "recompute": recompute,
            "settings": s, "items": entries}


def rows(record: dict) -> list[dict]:
    """The report table: one row per clip, flagged and least confident first."""
    out = []
    for e in record.get("items", []):
        corr, meas, outcome = e.get("correction") or {}, e.get("measurement") or {}, e.get("outcome") or {}
        v = corr.get("values", {})
        out.append({
            "id": e["id"], "name": e["name"], "timeline_start": e["timeline_start"], "scene": e.get("scene"),
            "hero": e.get("hero", False), "confidence": corr.get("overall"),
            "stops": v.get("exposure_stops"), "cct_before": v.get("cct_before"), "cct_after": v.get("cct_after"),
            "contrast": v.get("contrast"), "saturation": v.get("saturation"),
            "flags": corr.get("flags", []), "skipped": outcome.get("skipped", ""),
            "written": bool(outcome.get("written")), "marker": outcome.get("marker", ""),
            "ev100": meas.get("ev100"),
        })
    return sorted(out, key=lambda r: (not (r["marker"] or r["skipped"]), r["confidence"] if r["confidence"]
                                      is not None else -1, r["timeline_start"]))


def goto(resolve, item_id: str, timeline_name: str = "") -> bool:
    """Put the playhead on a clip and open the Color page."""
    proj = resolve.GetProjectManager().GetCurrentProject()
    timeline = find_timeline(proj, timeline_name) if timeline_name else proj.GetCurrentTimeline()
    if timeline is None:
        return False
    proj.SetCurrentTimeline(timeline)
    fps = round(_number(timeline.GetSetting("timelineFrameRate")) or 25)
    for ti in _video_items(timeline):
        if (ti.GetUniqueId() if hasattr(ti, "GetUniqueId") else "") == item_id:
            frame = ti.GetStart()
            tc = f"{frame // (3600 * fps):02d}:{frame // (60 * fps) % 60:02d}:{frame // fps % 60:02d}:{frame % fps:02d}"
            resolve.OpenPage("color")
            return bool(timeline.SetCurrentTimecode(tc))
    return False


def flow(resolve, cfg: Config, options: dict, rep) -> None:
    """Adapter for creator.run / App._start: options = {dry_run, recompute, timeline}."""
    basic_correction(resolve, cfg, rep, dry_run=bool(options.get("dry_run")),
                     recompute=bool(options.get("recompute")), timeline_name=options.get("timeline", ""))


EVALUATE_STEPS = [("render", "Render your version and DAVIGEN_AUTO"), ("score", "Compare"), ("save", "Save")]


def evaluate_flow(resolve, cfg: Config, options: dict, rep) -> None:
    """Compare DAVIGEN_AUTO with the user's own version on the current timeline (plan step 09)."""
    from ..creator import project_base  # noqa: PLC0415
    from . import evaluate  # noqa: PLC0415
    proj = resolve.GetProjectManager().GetCurrentProject()
    base = project_base(proj)
    timeline = find_timeline(proj, options.get("timeline", "")) if options.get("timeline") else proj.GetCurrentTimeline()
    record = write.load_record(base, timeline.GetName())
    if not record.get("items"):
        raise ResolveError(f"Run Basic correction on {timeline.GetName()} first")
    rep.start("render")
    results = evaluate.evaluate_timeline(resolve, proj, timeline, record, user_version=options.get("user_version", ""),
                                         progress=lambda v: rep.detail("render", v),
                                         restore_preset=_master_preset(proj))
    rep.finish("render", f"{len(results)} clips, one frame each, both versions")
    rep.start("score")
    summary = evaluate.summarise(results)
    rep.finish("score", f"median ΔE2000 {summary['delta_e']['median']:.1f}" if results else "nothing to compare")
    rep.start("save")
    folder = base / "00_ADMIN" / "PROJECT_INFO" / "basic_correction"
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{timeline.GetName()}_evaluation"
    (folder / f"{stem}.json").write_text(json.dumps({"results": results, "summary": summary}, indent=1),
                                         encoding="utf-8")
    (folder / f"{stem}.md").write_text(evaluate.report_markdown(summary, f"Basic correction · {timeline.GetName()}"),
                                       encoding="utf-8")
    rep.finish("save", f"00_ADMIN/PROJECT_INFO/basic_correction/{stem}.md")
    rep.result = {"summary": summary}
