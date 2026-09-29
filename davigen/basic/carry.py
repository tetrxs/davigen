"""Basic correction on every timeline: what was measured once is written again wherever the same clip is used.

Resolve keeps a grade – and the colour group, and the version DAVIGEN_AUTO – per timeline item, not per clip, so a
rough cut or a selects timeline built from the footage starts ungraded. Nothing needs measuring again: the record
of the timeline Basic correction ran on has each clip's nodes (and keyframes) by camera file, and keyframes sit on
source frames, so they fit any trimmed piece of the clip (verified in Resolve 21, 2026-09-29: a keyframe at source
frame 1000 lands at source frame 1000 on an item that starts at source frame 600).
"""

from __future__ import annotations

from pathlib import Path

from .. import posters
from . import correct as c, dynamic, pipeline as p, write
from .run import _video_items

STEPS = [("carry", "Basic correction on every timeline"), ("save", "Save")]
SKIP_PREFIXES = ("ZZ_DAVIGEN_",)            # davigen's scratch timelines


def _cdl(d: dict) -> p.Cdl:
    return p.Cdl(tuple(d["slope"]), tuple(d["offset"]), tuple(d["power"]), float(d["sat"]))


def correction_of(entry: dict) -> c.Correction | None:
    corr = entry.get("correction") or {}
    if not corr.get("nodes"):
        return None
    return c.Correction(nodes={k: _cdl(v) for k, v in corr["nodes"].items()},
                        confidence=dict(corr.get("confidence") or {}), flags=list(corr.get("flags") or []),
                        values=dict(corr.get("values") or {}))


def keyframes_of(entry: dict) -> dynamic.Keyframes | None:
    kf = entry.get("keyframes") or {}
    if not kf.get("frames") or not (entry.get("outcome") or {}).get("keyframes"):
        return None
    return dynamic.Keyframes(frames=[int(f) for f in kf["frames"]],
                             nodes={k: [_cdl(v) for v in vs] for k, vs in kf["nodes"].items()},
                             stops=list(kf.get("stops") or []), kelvin=list(kf.get("kelvin") or []),
                             reason=kf.get("reason", ""))


def graded_clips(base: Path) -> tuple[dict[str, dict], set[str]]:
    """Camera file → its record entry (the newest record wins), and the timelines those records came from.
    Only clips whose DAVIGEN_AUTO was really written (no dry runs, no skipped clips)."""
    by_path: dict[str, dict] = {}
    sources: set[str] = set()
    for rec_file in posters.records(base):
        rec = posters.load_record(rec_file)
        if rec.get("dry_run"):
            continue
        sources.add(rec.get("timeline") or rec_file.stem)
        for e in rec.get("items", []):
            if e.get("path") and (e.get("outcome") or {}).get("written"):
                by_path.setdefault(e["path"], e)
    return by_path, sources


def carry_over(project, base: Path, timelines: list, refresh: bool = False, progress=None) -> dict:
    """Write each clip's Basic correction into DAVIGEN_AUTO on the given timelines.

    refresh: also rewrite items that already have DAVIGEN_AUTO (after a new look); otherwise they are left as they
    are – including anything changed inside them. The timelines Basic correction ran on are never touched here.
    Returns counts: written, kept (had it already), unknown (clip not corrected yet), failed; and warnings."""
    by_path, sources = graded_clips(base)
    counts = {"written": 0, "kept": 0, "unknown": 0, "failed": 0, "keyframed": 0, "timelines": 0, "warnings": []}
    drx_folder = base / "03_WORK" / "ANALYSIS" / "drx"
    todo = [tl for tl in timelines if tl.GetName() not in sources and not tl.GetName().startswith(SKIP_PREFIXES)]
    for n, tl in enumerate(todo):
        touched = False
        for item in _video_items(tl):
            mpi = item.GetMediaPoolItem()
            entry = by_path.get(mpi.GetClipProperty("File Path")) if mpi else None
            if entry is None:
                counts["unknown"] += 1
                continue
            had = write.AUTO in (item.GetVersionNameList(0) or [])
            if had and not refresh:
                counts["kept"] += 1
                continue
            kf = keyframes_of(entry)
            out = write.write_item(project, item, correction_of(entry), recompute=refresh, keyframes=kf,
                                   drx_folder=drx_folder, had_keyframes=had)
            if out.skipped or not any(out.written.values()):
                counts["failed"] += 1
                counts["warnings"].append(f"{tl.GetName()} · {item.GetName()}: {out.skipped or 'nothing written'}")
                continue
            counts["written"] += 1
            counts["keyframed"] += bool(out.keyframes)
            counts["warnings"] += [f"{tl.GetName()} · {item.GetName()}: {w}" for w in out.warnings]
            touched = True
        counts["timelines"] += touched
        if progress:
            progress(n + 1, len(todo), tl.GetName())
    return counts


def summary(counts: dict) -> str:
    parts = [f"{counts['written']} clips on {counts['timelines']} timelines got DAVIGEN_AUTO"
             + (f" ({counts['keyframed']} with keyframes)" if counts["keyframed"] else "")]
    if counts["kept"]:
        parts.append(f"{counts['kept']} had it already")
    if counts["unknown"]:
        parts.append(f"{counts['unknown']} not corrected yet (run Basic correction on a timeline with them)")
    if counts["failed"]:
        parts.append(f"{counts['failed']} failed")
    return " · ".join(parts)


def flow(resolve, cfg, options: dict, rep) -> None:
    from ..creator import project_base  # noqa: PLC0415
    proj = resolve.GetProjectManager().GetCurrentProject()
    base = project_base(proj)
    current = proj.GetCurrentTimeline()
    timelines = [proj.GetTimelineByIndex(i) for i in range(1, proj.GetTimelineCount() + 1)]
    rep.start("carry", f"{len(timelines)} timelines")
    counts = carry_over(proj, base, timelines, refresh=bool(options.get("refresh")),
                        progress=lambda i, n, name: rep.detail("carry", f"{i}/{n} · {name}"))
    rep.warn(counts["warnings"][:20])
    rep.finish("carry", summary(counts))
    rep.start("save")
    if current is not None:
        proj.SetCurrentTimeline(current)
    resolve.GetProjectManager().SaveProject()
    rep.finish("save")
    rep.result = {k: v for k, v in counts.items() if k != "warnings"}
