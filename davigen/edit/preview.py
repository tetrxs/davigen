"""A watchable preview of the rough cut, made without Resolve: every shot graded as DAVIGEN_AUTO grades it, cut on
the beat, with the music – so the cut can be judged in davigen before opening the timeline.

Per shot, the whole grade (the colour group's input LUT, the clip's nodes at the shot's middle frame, the output
LUT) is baked into one 33-point .cube that ffmpeg's lut3d applies. Clips without a Basic correction get their
colour group only. The picture fills the project's aspect ratio (centre crop), like a timeline would.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from .. import posters
from ..basic import pipeline as p, preview as basic_preview
from . import decode

SIZE = 33
WORKERS = 2                     # shots rendered at a time (the hardware decoder is shared)


def bake(in_lut: Path | None, nodes: list[p.Cdl], out_lut: Path | None, target: Path) -> Path:
    grid = np.linspace(0, 1, SIZE)
    b, g, r = np.meshgrid(grid, grid, grid, indexing="ij")          # .cube: red changes fastest
    rgb = np.stack([r, g, b], axis=-1).reshape(-1, 3)
    if in_lut is not None and out_lut is not None:
        rgb = p.apply_lut(p.apply_nodes(p.apply_lut(rgb, in_lut), nodes), out_lut)
    out = np.clip(rgb, 0, 1)
    target.write_text(f"LUT_3D_SIZE {SIZE}\n" + "\n".join(f"{x:.6f} {y:.6f} {z:.6f}" for x, y, z in out) + "\n",
                      encoding="utf-8")
    return target


def _size(base: Path, width: int) -> tuple[int, int]:
    try:
        fmt = json.loads((base / "00_ADMIN" / "PROJECT_INFO" / "davigen.json").read_text(encoding="utf-8"))["format"]
        w, h = int(fmt["width"]), int(fmt["height"])
    except (OSError, ValueError, KeyError, TypeError):
        w, h = 16, 9
    height = int(round(width * h / w / 2)) * 2
    return width, max(2, height)


def _encoder() -> list[str]:
    """Apple's hardware encoder when ffmpeg has it (much faster), else x264."""
    exe = decode.tool("ffmpeg")
    res = subprocess.run([exe, "-hide_banner", "-encoders"], capture_output=True, text=True, check=False)
    if "h264_videotoolbox" in res.stdout:
        return ["-c:v", "h264_videotoolbox", "-b:v", "6M"]
    return ["-c:v", "libx264", "-crf", "21", "-preset", "veryfast"]


def render(base: Path, record: dict, target: Path, width: int = 960, fps: int = 25, progress=None) -> Path:
    """The rough cut of an Edit Assist record as an MP4 at `target`. progress(i, n, clip name)."""
    exe = decode.tool("ffmpeg")
    if not exe:
        raise RuntimeError("The preview needs ffmpeg – install it with 'brew install ffmpeg'")
    shots = record.get("shots") or []
    if not shots:
        raise RuntimeError("The last Edit Assist run has no rough cut – make one with a song first")
    music = record.get("music_file") or ""
    if not Path(music).is_file():
        raise RuntimeError("The song of the rough cut isn't where it was anymore")
    paths = {c["id"]: c["path"] for c in record.get("clips", []) if c.get("path")}
    w, h = _size(base, width)
    enc = _encoder()
    offset = float((record.get("music_window") or [0.0])[0] or 0.0)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="davigen_cut_") as tmp_name:
        tmp = Path(tmp_name)
        for shot in shots:
            path = paths.get(shot["clip_id"])
            if not path or not Path(path).is_file():
                raise RuntimeError(f"{shot['clip_name']} isn't where it was anymore")
        done = [0]

        def one(n: int) -> Path:
            shot = shots[n]
            path = paths[shot["clip_id"]]
            entry = posters._entry_for(base, path)          # noqa: SLF001 - the same lookup as the frames
            clip_fps = float(entry.get("clip_fps") or 25.0)
            luts = [Path(x) if x else None for x in entry.get("luts", ["", ""])]
            in_lut, out_lut = (x if x is not None and x.exists() else None for x in luts)
            mid = int(round((shot["source_start"] + shot["source_end"]) / 2 * clip_fps))
            nodes = basic_preview.nodes_at(entry, mid) if entry.get("correction") else []
            cube = bake(in_lut, nodes, out_lut, tmp / f"{n:03d}.cube")
            length = shot["record_end"] - shot["record_start"]
            vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase:in_color_matrix=bt709:out_range=pc:"
                  f"flags=bicubic,crop={w}:{h},format=rgb48le,lut3d=file={cube}:interp=tetrahedral,fps={fps},"
                  f"setsar=1,format=yuv420p")
            part = tmp / f"{n:03d}.mp4"
            res = None
            # hardware decoding is ~3× faster on 6K HEVC; software and x264 are the fallbacks
            for hw, codec in ((["-hwaccel", "videotoolbox"], enc), ([], enc),
                              ([], ["-c:v", "libx264", "-crf", "21", "-preset", "veryfast"])):
                res = subprocess.run([exe, "-v", "error", "-y", *hw, "-ss", f"{shot['source_start']:.3f}", "-i", path,
                                      "-t", f"{length:.3f}", "-an", "-vf", vf, *codec, str(part)],
                                     capture_output=True, text=True, check=False)
                if res.returncode == 0:
                    break
            if res is None or res.returncode != 0:
                raise RuntimeError(f"ffmpeg couldn't render {shot['clip_name']}: {res.stderr.strip()[-300:]}")
            done[0] += 1
            if progress:
                progress(done[0], len(shots), shot["clip_name"])
            return part

        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            parts = list(pool.map(one, range(len(shots))))
        if progress:
            progress(len(shots), len(shots), "music")
        listing = tmp / "list.txt"
        listing.write_text("".join(f"file '{x}'\n" for x in parts), encoding="utf-8")
        total = shots[-1]["record_end"]
        out = tmp / "cut.mp4"
        res = subprocess.run([exe, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
                              "-ss", f"{offset:.3f}", "-i", music, "-t", f"{total:.3f}", "-map", "0:v", "-map", "1:a",
                              "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                              "-af", f"afade=t=out:st={max(0.0, total - 1.5):.2f}:d=1.5",
                              "-movflags", "+faststart", str(out)], capture_output=True, text=True, check=False)
        if res.returncode != 0:
            raise RuntimeError(f"ffmpeg couldn't join the shots: {res.stderr.strip()[-300:]}")
        shutil.move(str(out), target)
    return target
