"""How close Basic Correction gets to the user's own grades (concept §11, plan step 09).

DAVIGEN_AUTO starts as a copy of the user's version and only nodes 01–04 are replaced, so rendering both versions
at the same frames compares exactly what Basic Correction decided – without disabling or changing any node of
the user's grade. The renders go through the whole pipeline (groups, all nodes), as the viewer sees them.

Metrics per frame, on the display images: exposure difference in stops, white balance angle, mean ΔE2000.
"""

from __future__ import annotations

import shutil
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from ..colormath import _colour
from ..resolve_api import ResolveError
from . import pipeline as p
from . import sampling
from .write import AUTO

DELTA_E_SMALL, DELTA_E_BIG = 3.0, 5.0         # "a small tweak or nothing" / "a real miss" (concept §11)


@dataclass
class Score:
    exposure: float             # stops, test − reference (display, linearised)
    wb_angle: float             # degrees between the grey-world light of both images
    delta_e: float              # mean CIEDE2000
    kelvin: float = 0.0         # CCT of the grey-world light, test − reference (warmer test → negative)
    black: float = 0.0          # 0.5th percentile of display luma, test − reference
    chroma: float = 1.0         # mean CIELAB C*, test / reference


def score(reference, test) -> Score:
    """Compare two display images (Rec.709 Gamma 2.4, 0–1) of the same frame."""
    ref, tst = (np.clip(np.asarray(x, dtype="float64"), 0.0, 1.0) for x in (reference, test))
    lin_r, lin_t = ref ** p.DISPLAY_GAMMA, tst ** p.DISPLAY_GAMMA
    y_r, y_t = lin_r @ p.REC709_LUMA, lin_t @ p.REC709_LUMA
    keep = (y_r > 1e-4) & (y_t > 1e-4) & (y_r < 0.98) & (y_t < 0.98)
    if keep.sum() < 16:
        keep = np.ones(y_r.shape, bool)
    exposure = float(np.mean(np.log2(np.maximum(y_t[keep], 1e-6))) - np.mean(np.log2(np.maximum(y_r[keep], 1e-6))))
    wb = p.angle_deg(lin_r[keep].mean(0) / max(float(y_r[keep].mean()), 1e-9),
                     lin_t[keep].mean(0) / max(float(y_t[keep].mean()), 1e-9))
    colour = _colour()
    lab_r, lab_t = p.display_to_lab(ref), p.display_to_lab(tst)
    de = colour.delta_E(lab_r, lab_t, method="CIE 2000")
    rec709 = colour.RGB_COLOURSPACES["ITU-R BT.709"]

    def cct(lin):
        xyz = lin[keep].mean(0) @ rec709.matrix_RGB_to_XYZ.T
        return p.cct_duv(xyz[:2] / max(float(xyz.sum()), 1e-9))[0]
    black = float(np.percentile(p.luminance(tst), 0.5) - np.percentile(p.luminance(ref), 0.5))
    chroma = float(p.chroma(lab_t).mean() / max(float(p.chroma(lab_r).mean()), 1e-6))
    return Score(exposure=exposure, wb_angle=wb, delta_e=float(np.mean(de)), kelvin=cct(lin_t) - cct(lin_r),
                 black=black, chroma=chroma)


def summarise(results: list[dict]) -> dict:
    """results: [{"clip", "category", "flagged", "score": Score-like dict}] → the acceptance numbers."""
    if not results:
        return {"clips": 0}

    def stats(values):
        values = np.abs(np.asarray(values, dtype="float64"))
        return {"median": float(np.median(values)), "p90": float(np.percentile(values, 90))}

    exp = [r["score"]["exposure"] for r in results]
    wb = [r["score"]["wb_angle"] for r in results]
    de = np.array([r["score"]["delta_e"] for r in results])
    flagged = np.array([bool(r["flagged"]) for r in results])
    big = de > DELTA_E_BIG
    out = {
        "clips": len(results),
        "exposure_stops": stats(exp), "wb_degrees": stats(wb), "delta_e": stats(de),
        "small_or_none": float((de < DELTA_E_SMALL).mean()),
        "big_misses": int(big.sum()),
        "big_misses_flagged": float(flagged[big].mean()) if big.any() else None,       # recall
        "flags_on_big_misses": float(big[flagged].mean()) if flagged.any() else None,  # precision
        "simulator_error": (float(np.median([r["simulator_error"] for r in results
                                             if r.get("simulator_error") is not None]))
                            if any(r.get("simulator_error") is not None for r in results) else None),
        "worst": sorted(({"clip": r["clip"], "delta_e": r["score"]["delta_e"], "flagged": r["flagged"],
                          "category": r.get("category", "")} for r in results),
                        key=lambda r: -r["delta_e"])[:10],
        "targets": {"exposure_median_below": 1 / 3, "wb_median_below": 2.0, "small_or_none_at_least": 0.8,
                    "big_misses_flagged_at_least": 0.7},
    }
    categories = sorted({r.get("category", "") for r in results if r.get("category")})
    out["by_category"] = {
        c: {"clips": sum(1 for r in results if r.get("category") == c),
            "delta_e": stats([r["score"]["delta_e"] for r in results if r.get("category") == c])}
        for c in categories}
    return out


def report_markdown(summary: dict, title: str) -> str:
    if not summary.get("clips"):
        return f"# {title}\n\nNo clips were compared.\n"
    t = summary["targets"]

    def check(ok):
        return "✅" if ok else "❌"
    recall = summary["big_misses_flagged"]
    lines = [
        f"# {title}", "",
        f"{summary['clips']} clips, DAVIGEN_AUTO compared with the user's own version at the same frames.", "",
        "| Metric | Result | Target | |", "|---|---|---|---|",
        f"| Exposure difference, median | {summary['exposure_stops']['median']:.2f} stops "
        f"(p90 {summary['exposure_stops']['p90']:.2f}) | < {t['exposure_median_below']:.2f} | "
        f"{check(summary['exposure_stops']['median'] < t['exposure_median_below'])} |",
        f"| White balance angle, median | {summary['wb_degrees']['median']:.2f}° (p90 {summary['wb_degrees']['p90']:.2f}°) "
        f"| < {t['wb_median_below']:.1f}° | {check(summary['wb_degrees']['median'] < t['wb_median_below'])} |",
        f"| Clips with ΔE2000 < {DELTA_E_SMALL:g} | {summary['small_or_none']:.0%} | ≥ {t['small_or_none_at_least']:.0%} | "
        f"{check(summary['small_or_none'] >= t['small_or_none_at_least'])} |",
        f"| Big misses (ΔE > {DELTA_E_BIG:g}) flagged | "
        + (f"{recall:.0%} of {summary['big_misses']}" if recall is not None else "no big misses")
        + f" | ≥ {t['big_misses_flagged_at_least']:.0%} | "
        + (check(recall >= t['big_misses_flagged_at_least']) if recall is not None else "✅") + " |",
        "", (f"Simulator vs Resolve's render of DAVIGEN_AUTO: {summary['simulator_error']:.2%} mean difference "
             "(should stay under 1 %)." if summary.get("simulator_error") is not None else ""),
        "", "## Worst clips", "", "| Clip | Category | ΔE2000 | Flagged |", "|---|---|---|---|",
    ]
    lines += [f"| {w['clip']} | {w['category']} | {w['delta_e']:.1f} | {'yes' if w['flagged'] else 'no'} |"
              for w in summary["worst"]]
    if summary.get("by_category"):
        lines += ["", "## By category", "", "| Category | Clips | ΔE2000 median | p90 |", "|---|---|---|---|"]
        lines += [f"| {c} | {v['clips']} | {v['delta_e']['median']:.1f} | {v['delta_e']['p90']:.1f} |"
                  for c, v in summary["by_category"].items()]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------------------------- render

def _timecode_frame(item, frame_in_source: int, clip_fps: float, timeline_fps: float) -> int:
    """Timeline frame showing a given source frame of an item."""
    offset = (frame_in_source - item.GetSourceStartFrame()) * timeline_fps / clip_fps
    return int(item.GetStart() + round(offset))


def render_versions(resolve, project, timeline, picks: list[tuple], width: int, versions: tuple[str, str],
                    progress=None, restore_preset: str = "") -> dict:
    """Render timeline frames with each item on each of two versions, graded as the viewer sees them.

    picks: [(timeline item, timeline frame, key)]. Returns {(key, version): uint16 image}. Each item's active
    version is restored afterwards; no grade is changed.
    """
    active = {}
    out: dict[tuple, np.ndarray] = {}
    tmp = Path(tempfile.mkdtemp(prefix="davigen_eval_"))
    jobs = []
    try:
        project.SetCurrentTimeline(timeline)
        if not project.SetCurrentRenderFormatAndCodec(sampling.RENDER_FORMAT, sampling.RENDER_CODEC):
            raise ResolveError("This Resolve can't render uncompressed 16-bit TIFF")
        w = int(timeline.GetSetting("timelineResolutionWidth") or 1920)
        h = int(timeline.GetSetting("timelineResolutionHeight") or 1080)
        height = max(2, round(width * h / w / 2) * 2)
        for item, _, _ in picks:
            cur = item.GetCurrentVersion() or {}
            active.setdefault(id(item), (item, cur.get("versionName", "") if isinstance(cur, dict) else str(cur)))
        for version in versions:
            for item, _, _ in picks:
                item.LoadVersionByName(version, 0)
            batch = []
            for n, (item, frame, key) in enumerate(picks):
                folder = tmp / f"{version}_{n}"
                folder.mkdir()
                project.SetRenderSettings({"SelectAllFrames": False, "MarkIn": frame, "MarkOut": frame,
                                           "TargetDir": str(folder), "CustomName": "e", "ExportVideo": True,
                                           "ExportAudio": False, "FormatWidth": width, "FormatHeight": height})
                job = project.AddRenderJob()
                if not job:
                    raise ResolveError("Resolve didn't accept an evaluation render job")
                jobs.append(job)
                batch.append((job, folder, key))
            project.StartRendering([j for j, _, _ in batch])
            while project.IsRenderingInProgress():
                if progress:
                    progress(version)
                time.sleep(0.3)
            for job, folder, key in batch:
                files = sorted(folder.glob("*.tif*"))
                if files:
                    out[(key, version)] = sampling.read_tiff(files[0])
        return out
    finally:
        for item, name in active.values():
            if name:
                item.LoadVersionByName(name, 0)
        for job in jobs:
            project.DeleteRenderJob(job)
        if restore_preset:
            project.LoadRenderPreset(restore_preset)
        shutil.rmtree(tmp, ignore_errors=True)


def evaluate_timeline(resolve, project, timeline, record: dict, width: int = 480, user_version: str = "",
                      progress=None, restore_preset: str = "", cache: sampling.Cache | None = None) -> list[dict]:
    """Score every written item of a Basic Correction record: its user version vs DAVIGEN_AUTO.

    With the analysis cache, each result also says how far Resolve's DAVIGEN_AUTO render is from what the
    simulator predicts for the same frame (`simulator_error`, mean absolute display difference): the check that
    pipeline.py reproduces Resolve (plan step 03).
    """
    by_id = {}
    for idx in range(1, timeline.GetTrackCount("video") + 1):
        for item in timeline.GetItemListInTrack("video", idx) or []:
            by_id[item.GetUniqueId()] = item
    fps = float(record.get("fps") or 25.0)
    picks, meta = [], {}
    for entry in record.get("items", []):
        item = by_id.get(entry["id"])
        outcome = entry.get("outcome") or {}
        if item is None or not outcome.get("written"):
            continue
        version = user_version or outcome.get("user_version") or "Version 1"
        if AUTO not in (item.GetVersionNameList(0) or []) or version not in (item.GetVersionNameList(0) or []):
            continue
        clip_fps = float(entry.get("clip_fps") or fps)
        frames = entry.get("frames") or []
        for f in frames[len(frames) // 2: len(frames) // 2 + 1]:        # the middle sample
            picks.append((item, _timecode_frame(item, f, clip_fps, fps), entry["id"]))
        meta[entry["id"]] = (entry, version)
    if not picks:
        return []
    versions = {v for _, v in meta.values()}
    if len(versions) != 1:
        raise ResolveError(f"Items use different user versions ({', '.join(sorted(versions))}) – pass one")
    user = versions.pop()
    images = render_versions(resolve, project, timeline, picks, width, (user, AUTO), progress, restore_preset)
    results = []
    for item, frame, key in picks:
        ref, test = images.get((key, user)), images.get((key, AUTO))
        if ref is None or test is None:
            continue
        entry, _ = meta[key]
        corr = entry.get("correction") or {}
        results.append({"clip": entry["name"], "id": key, "frame": frame,
                        "flagged": bool(entry.get("outcome", {}).get("marker")),
                        "confidence": corr.get("overall"), "flags": corr.get("flags", []),
                        "score": asdict(score(sampling.to_float(ref), sampling.to_float(test))),
                        "simulator_error": _simulator_error(entry, test, cache)})
    return results


def _simulator_error(entry: dict, rendered, cache) -> float | None:
    """Mean absolute difference between Resolve's DAVIGEN_AUTO render and the simulation of the same frame.

    Only meaningful while nodes 05/06 of DAVIGEN_AUTO are empty (they are copied from the user's version)."""
    luts = entry.get("luts") or []
    frames = entry.get("frames") or []
    nodes = ((entry.get("correction") or {}).get("nodes")) or {}
    if cache is None or len(luts) != 2 or not all(luts) or not frames or not nodes:
        return None
    frame = frames[len(frames) // 2]
    thumb = cache.load(entry["path"]).get(frame)
    if thumb is None:
        return None
    from .preview import nodes_at  # noqa: PLC0415
    chain = nodes_at(entry, frame)                  # keyframes interpolated at the frame, as Resolve renders them
    simulated = p.apply_lut(p.apply_nodes(p.apply_lut(sampling.to_float(thumb), luts[0]), chain), luts[1])
    real = sampling.thumbnail(rendered, simulated.shape[1])
    h = min(real.shape[0], simulated.shape[0])
    return float(np.abs(sampling.to_float(real)[:h] - simulated[:h]).mean())

