"""Pictures of a project for the UI: poster frames for the gallery and the overview, small thumbnails for the
Basic correction strip.

With a Basic correction record the frames are graded as DAVIGEN_AUTO grades them (davigen's simulator: input LUT,
the clip's nodes, output LUT). Without one they come from the camera files through the colour group's LUTs only.
Posters are cached in data/posters, keyed by the record or the file they come from.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .config import DATA_DIR
from .scanner import VIDEO_EXT

CACHE = DATA_DIR / "posters"
LUT_DIR = Path("/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT/davigen")


def records(base: Path) -> list[Path]:
    """Basic correction records of a project, the newest first."""
    folder = base / "00_ADMIN" / "PROJECT_INFO" / "basic_correction"
    files = [f for f in folder.glob("*.json") if not f.stem.endswith("_evaluation") and f.stem != "look"]
    return sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)


def load_record(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def pick(record: dict, count: int = 4) -> list[dict]:
    """Clips that show a project well: the longest shot of the biggest scenes, one per camera group first."""
    items = [e for e in record.get("items", []) if e.get("correction") and e.get("frames")]
    items.sort(key=lambda e: (-(e.get("source_frames") or 0) / max(e.get("clip_fps") or 25.0, 1.0)))
    out, groups, scenes = [], set(), set()
    for rule in ("group", "scene", "any"):
        for e in items:
            if len(out) >= count or e in out:
                continue
            if rule == "group" and e.get("group") in groups:
                continue
            if rule == "scene" and e.get("scene") in scenes:
                continue
            out.append(e)
            groups.add(e.get("group"))
            scenes.add(e.get("scene"))
    return out


def _media(base: Path, count: int) -> list[dict]:
    """Camera files, taken in turn from each camera folder, with the colour group's LUTs when they are known."""
    info_file = base / "00_ADMIN" / "PROJECT_INFO" / "davigen.json"
    try:
        groups = json.loads(info_file.read_text(encoding="utf-8")).get("groups", [])
    except (OSError, ValueError):
        groups = []
    out_lut = LUT_DIR / "DAVIGEN_OUT_DWG_TO_REC709_G24.cube"
    per_camera = []
    for folder in sorted((base / "01_MEDIA").glob("[0-8][0-9]_*")):
        key = folder.name.split("_", 1)[1]
        group = next((g for g in groups if g.get("group", "").startswith(f"G_{key}_")), {})
        in_lut = LUT_DIR / f"DAVIGEN_IN_{group.get('profile', '')}_TO_DWG.cube"
        files = sorted(f for f in folder.rglob("*") if f.suffix.lower() in VIDEO_EXT and not f.name.startswith("."))
        luts = [str(in_lut), str(out_lut)] if group and in_lut.exists() and out_lut.exists() else ["", ""]
        per_camera.append([{"name": f.stem, "group": group.get("group", key), "path": str(f), "luts": luts}
                           for f in files[1:: max(1, len(files) // count)] or files])
    out = []
    while len(out) < count and any(per_camera):
        for files in per_camera:
            if files and len(out) < count:
                out.append(files.pop(0))
    return out


_SOURCES: dict[tuple, list[dict]] = {}


def sources(base: Path, count: int = 4) -> list[dict]:
    """What the posters show: [{name, group, entry}] – from the newest record, else from the camera files."""
    recs = records(base)
    key = (str(base), count, tuple((str(r), r.stat().st_mtime) for r in recs))
    if key not in _SOURCES:
        if len(_SOURCES) > 32:
            _SOURCES.clear()
        _SOURCES[key] = _sources(base, recs, count)
    return _SOURCES[key]


def _sources(base: Path, recs: list[Path], count: int) -> list[dict]:
    for rec in recs:
        entries = pick(load_record(rec), count)
        if entries:
            return [{"name": e["name"], "group": e.get("group", ""), "entry": e, "key": f"{rec}:{rec.stat().st_mtime}"}
                    for e in entries]
    return [{"name": m["name"], "group": m["group"], "entry": m, "key": m["path"]} for m in _media(base, count)]


def poster(base: Path, index: int, width: int = 640) -> bytes:
    """PNG of the index-th poster of a project; FileNotFoundError when there is nothing to show."""
    src = sources(base)
    if not src:
        raise FileNotFoundError("no pictures in this project yet")
    s = src[index % len(src)]
    stamp = hashlib.sha1(f"{base}|{s['key']}|{s['name']}|{width}".encode()).hexdigest()[:16]
    cached = CACHE / f"{stamp}.png"
    if cached.exists():
        return cached.read_bytes()
    png = _render(s["entry"], base, width)
    CACHE.mkdir(parents=True, exist_ok=True)
    for old in sorted(CACHE.glob("*.png"), key=lambda f: f.stat().st_mtime)[:-200]:
        old.unlink(missing_ok=True)                     # keep the cache small
    cached.write_bytes(png)
    return png


def _render(entry: dict, base: Path, width: int) -> bytes:
    import numpy as np  # noqa: PLC0415 - numpy only when a picture is made
    from .basic import pipeline as p, preview  # noqa: PLC0415
    frames = entry.get("frames") or []
    if frames:
        frame = frames[len(frames) // 2]
    else:                                           # a camera file: a few seconds in
        entry = {**entry, "clip_fps": 25.0}
        frame = 75
    log = preview._camera_frame(entry, frame, base / "03_WORK" / "ANALYSIS", width)
    in_lut, out_lut = (Path(x) if x else None for x in entry.get("luts", ["", ""]))
    if in_lut is None or out_lut is None or not in_lut.exists() or not out_lut.exists():
        img = log                                   # no LUTs known: the log picture as it is
    else:
        dwg = p.apply_lut(log, in_lut)
        if entry.get("correction"):
            dwg = p.apply_nodes(dwg, preview.nodes_at(entry, frame))
        img = p.apply_lut(dwg, out_lut)
    return preview.png(np.clip(img, 0, 1))


def thumb(entry: dict, base: Path) -> bytes:
    """Small PNG of a clip from the analysis cache (no ffmpeg): its middle sample, graded with its nodes."""
    import numpy as np  # noqa: PLC0415
    from .basic import pipeline as p, preview, sampling  # noqa: PLC0415
    cached = sampling.Cache(base / "03_WORK" / "ANALYSIS").load(entry["path"])
    if not cached:
        raise FileNotFoundError("no analysed frames for this clip")
    frames = entry.get("frames") or sorted(cached)
    frame = min(cached, key=lambda f: abs(f - frames[len(frames) // 2]))
    img = sampling.to_float(cached[frame])
    in_lut, out_lut = (Path(x) if x else None for x in entry.get("luts", ["", ""]))
    if in_lut and out_lut and in_lut.exists() and out_lut.exists():
        dwg = p.apply_lut(img, in_lut)
        if entry.get("correction"):
            dwg = p.apply_nodes(dwg, preview.nodes_at(entry, frame))
        img = p.apply_lut(dwg, out_lut)
    return preview.png(np.clip(img, 0, 1))
