"""Low-resolution video and mono audio from camera files, via ffmpeg (hardware decoding where the Mac has it)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np

SEARCH = ["/opt/homebrew/bin", "/usr/local/bin", str(Path(__file__).resolve().parents[2] / "runtime" / "ffmpeg")]


class DecodeError(RuntimeError):
    pass


def tool(name: str) -> str | None:
    """ffmpeg / ffprobe from PATH or the usual Homebrew places (Resolve starts scripts with a minimal PATH)."""
    return shutil.which(name) or shutil.which(name, path=":".join(SEARCH))


def available() -> bool:
    return bool(tool("ffmpeg") and tool("ffprobe"))


def probe(path: str) -> dict:
    """Duration (s), frame rate, size and whether there is audio."""
    exe = tool("ffprobe")
    if not exe:
        raise DecodeError("ffprobe not found – install ffmpeg (brew install ffmpeg)")
    res = subprocess.run([exe, "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height,"
                          "r_frame_rate", "-of", "json", path], capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=60)
    data = json.loads(res.stdout or "{}")
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    num, _, den = (video.get("r_frame_rate") or "0/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 0.0
    return {"duration": float(data.get("format", {}).get("duration") or 0), "fps": fps,
            "width": int(video.get("width") or 0), "height": int(video.get("height") or 0),
            "audio": any(s.get("codec_type") == "audio" for s in data.get("streams", []))}


def _run(args: list[str], timeout: float) -> bytes:
    exe = tool("ffmpeg")
    if not exe:
        raise DecodeError("ffmpeg not found – install ffmpeg (brew install ffmpeg)")
    res = subprocess.run([exe, "-v", "error", *args], capture_output=True, timeout=timeout)
    if res.returncode != 0:
        raise DecodeError(res.stderr.decode("utf-8", "replace").strip()[-300:])
    return res.stdout


def frames(path: str, fps: float = 5.0, width: int = 320, duration: float = 0.0) -> np.ndarray:
    """Grey frames (n, h, w) as float 0–1, `fps` per second of the clip."""
    vf = f"fps={fps},scale={width}:-2:flags=area,format=gray"
    timeout = max(120.0, duration * 4)
    for hw in (["-hwaccel", "videotoolbox"], []):          # the hardware decoder can refuse; software always works
        try:
            raw = _run([*hw, "-i", path, "-an", "-vf", vf, "-f", "rawvideo", "-"], timeout)
            if raw:
                break
        except (DecodeError, subprocess.TimeoutExpired):
            raw = b""
    if not raw:
        raise DecodeError(f"couldn't decode {Path(path).name}")
    info = probe(path)
    w, h = max(info["width"], 1), max(info["height"], 1)
    # ffmpeg applies rotation metadata, so a phone's portrait clip comes out the other way round
    candidates = [round(width * h / w / 2) * 2, round(width * w / h / 2) * 2]
    height = next((c for c in candidates if c and len(raw) % (width * c) == 0), 0)
    if not height:
        raise DecodeError(f"unexpected frame size decoding {Path(path).name}")
    n = len(raw) // (width * height)
    return np.frombuffer(raw[: n * width * height], dtype=np.uint8).reshape(n, height, width).astype("float32") / 255


def audio(path: str, rate: int = 16000, duration: float = 0.0) -> np.ndarray:
    """Mono audio as float32 −1…1 (empty if the file has none)."""
    try:
        raw = _run(["-i", path, "-vn", "-ac", "1", "-ar", str(rate), "-f", "f32le", "-"], max(120.0, duration * 2))
    except DecodeError:
        return np.zeros(0, dtype="float32")
    return np.frombuffer(raw, dtype="<f4").copy()
