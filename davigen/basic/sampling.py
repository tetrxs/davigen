"""Small, exact sample frames of every clip, rendered by Resolve itself (concept §4, plan step 03).

The API can't read pixels, so davigen builds a scratch timeline of one-frame snippets without group or grade,
renders it as an uncompressed 16-bit TIFF sequence at analysis size, and reads the camera code values back. They
are cached per media clip as 16-bit thumbnails in camera log; the input LUT is applied when they are used, so a
changed LUT needs no new render.
"""

from __future__ import annotations

import hashlib
import math
import shutil
import struct
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..resolve_api import ResolveError, find_timeline

ANALYSIS_TL = "ZZ_DAVIGEN_ANALYSIS"
THUMB_WIDTH = 96
EDGE_FRAMES = 5                         # fades and stabiliser start-up at the ends of a shot
RENDER_FORMAT, RENDER_CODEC = "tif", "RGB16"      # uncompressed 16-bit (verified in step 01)


# ----------------------------------------------------------------------------------------- frame choice

def choose_frames(start: int, duration: int, fps: float, samples: dict) -> list[int]:
    """Source frames to sample in the used range [start, start + duration): min + 1 per `per_seconds`, at most
    max, spread evenly and away from the ends. Short shots get fewer (never the same frame twice)."""
    if duration <= 0:
        return []
    seconds = duration / max(fps, 1.0)
    count = min(samples["max"], samples["min"] + int(seconds // samples["per_seconds"]))
    edge = EDGE_FRAMES if duration > 4 * EDGE_FRAMES else 0
    first, last = start + edge, start + duration - 1 - edge
    count = max(1, min(count, last - first + 1))
    if count == 1:
        return [(first + last) // 2]
    step = (last - first) / (count - 1)
    return sorted({int(round(first + i * step)) for i in range(count)})


# ------------------------------------------------------------------------------------------------ cache

class Cache:
    """One .npz per media clip in 03_WORK/ANALYSIS: {frame: uint16 thumbnail in camera log}.

    The key includes file size and modification time, so a replaced file is measured again.
    """

    def __init__(self, folder: Path):
        self.folder = folder

    def _file(self, path: str) -> Path:
        digest = hashlib.sha1(path.encode("utf-8")).hexdigest()[:12]
        return self.folder / f"{Path(path).stem}_{digest}.npz"

    @staticmethod
    def _stamp(path: str) -> str:
        try:
            st = Path(path).stat()
            return f"{st.st_size}:{int(st.st_mtime)}"
        except OSError:
            return ""

    def load(self, path: str) -> dict[int, np.ndarray]:
        f = self._file(path)
        if not f.exists():
            return {}
        try:
            with np.load(f) as data:
                if str(data["stamp"]) != self._stamp(path):
                    return {}
                return {int(k[1:]): data[k] for k in data.files if k.startswith("f")}
        except (OSError, ValueError, KeyError):
            return {}

    def save(self, path: str, frames: dict[int, np.ndarray]) -> None:
        if not frames:
            return
        self.folder.mkdir(parents=True, exist_ok=True)
        merged = {**self.load(path), **frames}
        np.savez_compressed(self._file(path), stamp=np.array(self._stamp(path)),
                            **{f"f{k}": v.astype("uint16") for k, v in merged.items()})


# ------------------------------------------------------------------------------------------------- TIFF

def read_tiff(path: Path) -> np.ndarray:
    """Uncompressed baseline RGB TIFF (8 or 16 bit, strips, either byte order) → uint16 array (h, w, 3)."""
    raw = path.read_bytes()
    endian = "<" if raw[:2] == b"II" else ">"
    (ifd,) = struct.unpack(endian + "I", raw[4:8])
    (count,) = struct.unpack(endian + "H", raw[ifd:ifd + 2])
    tags: dict[int, tuple] = {}
    for i in range(count):
        entry = raw[ifd + 2 + 12 * i: ifd + 14 + 12 * i]
        tag, typ, n = struct.unpack(endian + "HHI", entry[:8])
        size = {3: 2, 4: 4}.get(typ, 1)
        fmt = {3: "H", 4: "I"}.get(typ, "B")
        if n * size <= 4:
            vals = struct.unpack(endian + fmt * n, entry[8:8 + n * size])
        else:
            (off,) = struct.unpack(endian + "I", entry[8:12])
            vals = struct.unpack(endian + fmt * n, raw[off:off + n * size])
        tags[tag] = vals
    width, height, bits = tags[256][0], tags[257][0], tags[258][0]
    if tags.get(259, (1,))[0] != 1:
        raise ValueError(f"{path.name}: compressed TIFF – set the analysis codec to uncompressed RGB 16")
    spp = tags.get(277, (1,))[0]
    data = b"".join(raw[o:o + c] for o, c in zip(tags[273], tags[279]))
    dtype = np.dtype(endian + ("u2" if bits == 16 else "u1"))
    img = np.frombuffer(data, dtype=dtype)[:width * height * spp].reshape(height, width, spp)[..., :3]
    return img.astype("uint16") * (257 if bits == 8 else 1)


def thumbnail(img: np.ndarray, width: int = THUMB_WIDTH) -> np.ndarray:
    """Area-average down to `width` pixels wide (keeps the aspect ratio)."""
    h, w = img.shape[:2]
    k = max(1, w // width)
    h2, w2 = h // k, w // k
    small = img[:h2 * k, :w2 * k].reshape(h2, k, w2, k, 3).astype("float64").mean(axis=(1, 3))
    return np.round(small).astype("uint16")


def to_float(thumb: np.ndarray) -> np.ndarray:
    return thumb.astype("float64") / 65535.0


# ---------------------------------------------------------------------------------------------- render

@dataclass
class Request:
    key: tuple                          # anything that identifies the sample, e.g. (file path, frame)
    media_pool_item: object
    frame: int
    span: int = 1                       # source frames to append: a 60p clip needs 3 to fill one 25p frame


def span_for(clip_fps: float, timeline_fps: float) -> int:
    """Source frames that make at least one timeline frame (the snippet's first frame is the sample)."""
    if clip_fps <= 0 or timeline_fps <= 0:
        return 1
    return max(1, math.ceil(clip_fps / timeline_fps - 1e-6))


def render(resolve, project, requests: list[Request], width: int, progress=None,
           restore_preset: str = "") -> dict[tuple, np.ndarray]:
    """Render one frame per request through Resolve, without group or grade. Returns {key: uint16 thumbnail}.

    Builds and always deletes the scratch timeline, restores the current timeline, and removes the render job.
    The Deliver page's settings can't be read back: afterwards `restore_preset` is loaded if given.
    """
    if not requests:
        return {}
    mp = project.GetMediaPool()
    current = project.GetCurrentTimeline()
    previous = current.GetName() if current is not None else ""      # by name: deleting a timeline invalidates
    old = find_timeline(project, ANALYSIS_TL)                         # the other timeline objects (Resolve 21)
    if old is not None:
        mp.DeleteTimelines([old])
    tmp = Path(tempfile.mkdtemp(prefix="davigen_analysis_"))
    timeline = None
    job = None
    try:
        timeline = mp.CreateEmptyTimeline(ANALYSIS_TL)
        if timeline is None:
            raise ResolveError("Couldn't create the analysis timeline")
        project.SetCurrentTimeline(timeline)
        timeline.SetSetting("useCustomSettings", "1")
        timeline.SetSetting("timelineInputResMismatchBehavior", "scaleToCrop")   # never letterbox: bars aren't picture
        infos = [{"mediaPoolItem": r.media_pool_item, "startFrame": r.frame, "endFrame": r.frame + r.span,
                  "mediaType": 1} for r in requests]       # endFrame is exclusive (step 01)
        items = mp.AppendToTimeline(infos) or []
        if len(items) != len(requests):
            raise ResolveError(f"Resolve put {len(items)} of {len(requests)} sample frames on the analysis timeline")
        lengths = [max(1, int(i.GetDuration() or 1)) for i in items]
        if not project.SetCurrentRenderFormatAndCodec(RENDER_FORMAT, RENDER_CODEC):
            raise ResolveError("This Resolve can't render uncompressed 16-bit TIFF")
        w = int(timeline.GetSetting("timelineResolutionWidth") or 1920)
        h = int(timeline.GetSetting("timelineResolutionHeight") or 1080)
        height = max(2, round(width * h / w / 2) * 2)
        project.SetRenderSettings({"SelectAllFrames": True, "TargetDir": str(tmp), "CustomName": "a",
                                   "ExportVideo": True, "ExportAudio": False,
                                   "FormatWidth": width, "FormatHeight": height})
        job = project.AddRenderJob()
        if not job:
            raise ResolveError("Resolve didn't accept the analysis render job")
        project.StartRendering([job])
        while project.IsRenderingInProgress():
            if progress:
                status = project.GetRenderJobStatus(job) or {}
                progress(int(status.get("CompletionPercentage") or 0))
            time.sleep(0.3)
        files = sorted(tmp.glob("*.tif*"))
        if len(files) != sum(lengths):
            status = project.GetRenderJobStatus(job) or {}
            raise ResolveError(f"The analysis render gave {len(files)} frames for {sum(lengths)} "
                               f"({status.get('JobStatus', 'unknown')}: {status.get('Error', '')})")
        firsts = [sum(lengths[:n]) for n in range(len(lengths))]    # each snippet's first frame is its sample
        return {r.key: thumbnail(read_tiff(files[i])) for r, i in zip(requests, firsts)}
    finally:
        if job:
            project.DeleteRenderJob(job)
        if timeline is not None:
            mp.DeleteTimelines([timeline])
        back = find_timeline(project, previous) if previous else None
        if back is not None:
            project.SetCurrentTimeline(back)
        if restore_preset:
            project.LoadRenderPreset(restore_preset)
        shutil.rmtree(tmp, ignore_errors=True)
