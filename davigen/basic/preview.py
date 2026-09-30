"""Before | after pictures of one clip for the report (concept §11), made the way Resolve makes them.

A frame of the camera file is decoded with ffmpeg (full range, as the analysis render; within 0.001 of Resolve in
step 01), the group's input LUT, the clip's nodes from the record (keyframes interpolated at that frame) and the
output LUT are applied in davigen's simulator. Without ffmpeg the analysis thumbnail from the cache is used.
"""

from __future__ import annotations

import subprocess
import zlib
from pathlib import Path

import numpy as np

from . import pipeline as p
from . import sampling


def nodes_at(entry: dict, frame: int) -> list[p.Cdl]:
    """The four node CDLs of a record entry at a source frame: keyframes interpolated linearly, held outside."""
    def cdl(d: dict) -> p.Cdl:
        return p.Cdl(tuple(d["slope"]), tuple(d["offset"]), tuple(d["power"]), d["sat"])
    kf = entry.get("keyframes")
    if kf and kf.get("frames") and kf.get("resolve"):
        # as Resolve interpolates: offsets of 01/02, node 03 constant, node 04's contrast, pivot and saturation
        frames = kf["frames"]
        out = []
        for label in ("01_EXPOSURE", "02_WHITE_BALANCE"):
            rows = kf["nodes"][label]
            out.append(p.Cdl(offset=tuple(float(np.interp(frame, frames, [r["offset"][i] for r in rows]))
                                          for i in range(3))))
        out.append(cdl(kf["resolve"]["03"]))
        rows = kf["resolve"]["04"]
        at = {key: float(np.interp(frame, frames, [r[key] for r in rows])) for key in ("contrast", "pivot", "sat")}
        k = at["contrast"]
        out.append(p.Cdl(slope=(k,) * 3, offset=(at["pivot"] * (1.0 - k),) * 3, sat=at["sat"]))
        return out
    if kf and kf.get("frames"):
        frames = kf["frames"]
        out = []
        for label in sorted(kf["nodes"]):
            rows = kf["nodes"][label]
            def at(key, i):
                return float(np.interp(frame, frames, [r[key][i] if isinstance(r[key], list) else r[key]
                                                       for r in rows]))
            out.append(p.Cdl(tuple(at("slope", i) for i in range(3)), tuple(at("offset", i) for i in range(3)),
                             tuple(at("power", i) for i in range(3)), at("sat", 0)))
        return out
    nodes = (entry.get("correction") or {}).get("nodes", {})
    return [cdl(v) for _, v in sorted(nodes.items())]


def decode(path: str, frame: int, fps: float, width: int) -> np.ndarray | None:
    """One frame as camera code values 0–1, or None without ffmpeg."""
    from ..decode import tool  # noqa: PLC0415
    exe = tool("ffmpeg")
    if not exe:
        return None
    vf = f"scale={width}:-2:in_color_matrix=bt709:out_range=pc:flags=area,format=rgb48le"
    res = subprocess.run([exe, "-v", "error", "-ss", f"{frame / max(fps, 1.0):.6f}", "-i", path, "-vf", vf,
                          "-frames:v", "1", "-f", "rawvideo", "-"], capture_output=True, timeout=60)
    raw = res.stdout
    if res.returncode != 0 or not raw:
        return None
    return np.frombuffer(raw, dtype="<u2").reshape(len(raw) // (width * 6), width, 3).astype("float64") / 65535.0


_FRAMES: dict[tuple, np.ndarray] = {}


def _camera_frame(entry: dict, frame: int, cache_folder: Path, width: int) -> np.ndarray:
    """Camera code values of a frame: ffmpeg, or the nearest cached analysis thumbnail. Kept in memory."""
    key = (entry["path"], frame, width)
    if key not in _FRAMES:
        log = decode(entry["path"], frame, float(entry.get("clip_fps") or 25.0), width)
        if log is None:
            thumbs = sampling.Cache(cache_folder).load(entry["path"])
            if not thumbs:
                raise FileNotFoundError("no ffmpeg and no cached frames for this clip")
            log = sampling.to_float(thumbs[min(thumbs, key=lambda f: abs(f - frame))])
        if len(_FRAMES) > 64:
            _FRAMES.clear()
        _FRAMES[key] = log
    return _FRAMES[key]


def look_preview(entry: dict, cache_folder: Path, workflow: dict, look: dict | None, width: int = 360) -> np.ndarray:
    """One clip corrected with a look (concept §15), from its cached samples: display RGB of its middle sample.
    look None: the colour group only (the 'before')."""
    from . import correct as c, measure as ms, settings as settings_mod  # noqa: PLC0415
    in_lut, out_lut = (Path(x) for x in entry["luts"])
    frames = entry.get("frames") or []
    frame = frames[len(frames) // 2] if frames else entry.get("source_start", 0)
    dwg = p.apply_lut(_camera_frame(entry, frame, cache_folder, width), in_lut)
    if look is None:
        return np.clip(p.apply_lut(dwg, out_lut), 0, 1)
    s = settings_mod.load(workflow, look=look)
    key = ("measured", entry["path"], tuple(frames))
    if key not in _FRAMES:                  # the measurement doesn't depend on the look: once per clip
        cached = sampling.Cache(cache_folder).load(entry["path"])
        use = [f for f in frames if f in cached]
        use = use[:: max(1, len(use) // 5)]             # five samples are plenty for a picture
        thumbs = [p.apply_lut(sampling.to_float(cached[f]), in_lut) for f in use]
        if not thumbs:
            raise FileNotFoundError("no analysed frames for this clip – run Basic correction once")
        meta = ms.ClipMeta(**{k: v for k, v in (entry.get("meta") or {}).items()
                              if k in ms.ClipMeta.__dataclass_fields__})
        _FRAMES[key] = (thumbs, ms.measure(thumbs, meta, out_lut, s))
    thumbs, m = _FRAMES[key]
    corr = c.correct(m, thumbs, out_lut, s)
    return np.clip(p.apply_lut(p.apply_nodes(dwg, corr.chain()), out_lut), 0, 1)


def before_after(entry: dict, frame: int, cache_folder: Path, width: int = 480) -> np.ndarray:
    """Display-referred RGB 0–1: the frame as it comes out of the colour group, a gap, and with DAVIGEN_AUTO."""
    in_lut, out_lut = (Path(x) if x else None for x in entry.get("luts", ["", ""]))
    if in_lut is None or out_lut is None or not in_lut.exists() or not out_lut.exists():
        raise FileNotFoundError("the colour group's LUTs are missing")
    log = decode(entry["path"], frame, float(entry.get("clip_fps") or 25.0), width)
    if log is None:
        thumbs = sampling.Cache(cache_folder).load(entry["path"])
        if not thumbs:
            raise FileNotFoundError("no ffmpeg and no cached frames for this clip")
        near = min(thumbs, key=lambda f: abs(f - frame))
        log = sampling.to_float(thumbs[near])
    dwg = p.apply_lut(log, in_lut)
    before = p.apply_lut(dwg, out_lut)
    after = p.apply_lut(p.apply_nodes(dwg, nodes_at(entry, frame)), out_lut)
    gap = np.ones((before.shape[0], max(2, before.shape[1] // 100), 3))
    return np.clip(np.concatenate([before, gap, after], axis=1), 0.0, 1.0)


def png(image: np.ndarray) -> bytes:
    """8-bit RGB PNG with the standard library."""
    img = (np.clip(image, 0, 1) * 255).round().astype("uint8")
    h, w, _ = img.shape
    raw = b"".join(b"\x00" + img[y].tobytes() for y in range(h))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return len(data).to_bytes(4, "big") + kind + data + zlib.crc32(kind + data).to_bytes(4, "big")
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", w.to_bytes(4, "big") + h.to_bytes(4, "big") + bytes([8, 2, 0, 0, 0]))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))
