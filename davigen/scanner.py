"""Detect camera, model and log profile of video clips from their metadata (ffprobe)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import mediainfo, naming, transforms
from .config import Config

VIDEO_EXT = {".mov", ".mp4", ".mxf", ".m4v", ".mts", ".braw", ".insv", ".avi", ".mkv"}
SKIP_SUFFIX = ("_proxy",)
TOOL_PATHS = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"]

# confidence levels shown in the UI
METADATA, INFERRED, GUESS = "metadata", "inferred", "guess"


@dataclass
class ClipInfo:
    path: str
    make: str = ""
    model: str = ""
    camera_key: str = ""
    camera_name: str = ""
    profile: str = ""
    confidence: str = GUESS
    width: int = 0
    height: int = 0
    fps: float = 0.0
    bit_depth: int = 8
    codec: str = ""
    transfer: str = ""
    duration: float = 0.0
    created: str = ""
    lens: str = ""
    iso: str = ""
    fnumber: float = 0.0            # exposure data for Basic Correction (0 / "" = not recorded)
    exposure_time: float = 0.0      # seconds
    white_balance: str = ""         # "Auto", "Manual", "Daylight", …
    kelvin: int = 0
    notes: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def name(self) -> str:
        return Path(self.path).name


# ------------------------------------------------------------------ file listing

def find_videos(paths: list[str]) -> list[str]:
    out = []
    for p in paths:
        p = os.path.expanduser(p)
        if os.path.isfile(p):
            if _is_video(p):
                out.append(p)
            continue
        for root, dirs, files in os.walk(p):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            out += [os.path.join(root, f) for f in files if _is_video(f)]
    return sorted(set(out))


def _is_video(name: str) -> bool:
    base = os.path.basename(name)
    stem, ext = os.path.splitext(base)
    return (ext.lower() in VIDEO_EXT and not base.startswith(".")
            and not stem.lower().endswith(SKIP_SUFFIX))


# ----------------------------------------------------------------------- probing

def _ffprobe_bit_depth(path: str) -> int | None:
    """Fallback for formats without an MP4/MOV configuration box (only if ffprobe happens to exist)."""
    exe = shutil.which("ffprobe") or shutil.which("ffprobe", path=os.pathsep.join(TOOL_PATHS))
    if not exe:
        return None
    res = subprocess.run([exe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=pix_fmt",
                          "-of", "json", path], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    try:
        fmt = json.loads(res.stdout)["streams"][0]["pix_fmt"]
    except (ValueError, KeyError, IndexError):
        return None
    m = re.search(r"p(\d+)(le|be)?$", fmt)
    return int(m.group(1)) if m else 8


def analyse(path: str, cfg: Config, tags: dict) -> ClipInfo:
    """Build ClipInfo from exiftool tags (camera, profile, format) plus the real bit depth."""
    info = ClipInfo(path=path)
    if not tags or tags.get("ExifTool:Error"):
        info.error = (tags or {}).get("ExifTool:Error", "No readable metadata")
        return info
    track = mediainfo.video_track(tags)
    info.width, info.height = int(track.get("ImageWidth") or 0), int(track.get("ImageHeight") or 0)
    info.fps = round(mediainfo.number(track.get("VideoFrameRate")), 3)
    info.codec = str(track.get("CompressorID") or "")
    info.duration = mediainfo.number(mediainfo.first(tags, "Duration"))
    # QuickTime:CreateDate is UTC in every camera's MP4/MOV – the only clock all cameras share for sorting
    utc = tags.get("QuickTime:CreateDate")
    info.created = (_iso(str(utc)) + "Z") if utc and not str(utc).startswith("0000") else \
        _iso(mediainfo.first(tags, "DateTimeOriginal", "CreationDate", "CreateDate", "MediaCreateDate"))
    info.transfer = mediainfo.first(tags, "TransferCharacteristics")
    info.bit_depth = mediainfo.bit_depth(path) or _ffprobe_bit_depth(path) or 8
    info.iso = mediainfo.first(tags, "ISO", "ISOSensitivity")
    info.lens = mediainfo.first(tags, "LensModel", "LensType", "LensID")
    exposure(info, tags)

    info.make = mediainfo.first(tags, "Make", "Manufacturer", "DeviceManufacturer")
    info.model = mediainfo.first(tags, "Model", "CameraModelName", "ModelName", "DeviceModelName", "Encoder")
    if not info.make and info.model:
        # e.g. DJI writes only "Encoder: DJI Air3"
        brand = cfg.brand_of(info.model.split()[0])
        if brand:
            info.make, info.model = info.model.split()[0], " ".join(info.model.split()[1:]) or info.model
    hint = " | ".join(filter(None, [mediainfo.profile_text(tags), _panasonic_xml(tags), _sony_sidecar(info, path)]))
    if cfg.brand_of(info.make) == "dji":
        info.lens = _dji_lens(path) or info.lens
    _resolve_camera(info, cfg)
    _resolve_profile(info, cfg, hint)
    return info


def exposure(info: ClipInfo, tags: dict) -> ClipInfo:
    """Aperture, shutter, white balance mode and Kelvin, where the camera wrote them (Lumix does, DJI doesn't)."""
    info.fnumber = mediainfo.number(mediainfo.first(tags, "FNumber"))
    info.exposure_time = _seconds(mediainfo.first(tags, "ExposureTime", "ShutterSpeed"))
    info.white_balance = mediainfo.first(tags, "WhiteBalance")
    info.kelvin = int(mediainfo.number(mediainfo.first(tags, "ColorTempKelvin", "ColorTemperature", "WBTemperature")))
    return info


def _seconds(value: str) -> float:
    """'1/50' -> 0.02, '0.5' -> 0.5, '' -> 0."""
    try:
        if "/" in value:
            num, den = value.split("/", 1)
            return float(num) / float(den)
        return float(value) if value else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def _iso(value: str) -> str:
    """'2026:09:25 10:44:21+01:00' -> '2026-09-25T10:44:21+01:00' (sortable, date first)."""
    m = re.match(r"(\d{4}):(\d{2}):(\d{2})[ T](.*)", value or "")
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}T{m.group(4)}" if m else value


# ---------------------------------------------------------------- vendor extras
# exiftool covers make/model/maker notes of all brands; these add what lives outside its tags.

def _panasonic_xml(tags: dict) -> str:
    """Panasonic's embedded clip XML states gamma and gamut (CaptureGamma: V-Log)."""
    xml = next((v for k, v in tags.items() if "metadataxml" in k.lower() and isinstance(v, str)), "")
    if not xml:
        return ""
    try:
        root = ET.fromstring(xml.encode())
    except ET.ParseError:
        return ""
    values = [e.text or "" for e in root.iter() if e.tag.split("}")[-1] in ("CaptureGamma", "CaptureGamut")]
    return " | ".join(v for v in values if v)


def _sony_sidecar(info: ClipInfo, path: str) -> str:
    """Sony writes gamma/gamut into C0001M01.XML next to the clip."""
    p = Path(path)
    cand = next((c for c in (p.with_name(p.stem + "M01.XML"), p.with_name(p.stem + "M01.xml")) if c.exists()), None)
    if cand is None:
        return ""
    try:
        root = ET.parse(cand).getroot()
    except ET.ParseError:
        return ""
    hints = []
    for e in root.iter():
        tag = e.tag.split("}")[-1]
        if tag == "Device" and not info.model:
            info.make, info.model = e.get("manufacturer", "Sony"), e.get("modelName", "")
        elif tag == "Item" and e.get("name") in ("CaptureGammaEquation", "CaptureColorPrimaries"):
            hints.append(e.get("value", ""))
    return " | ".join(hints)


def _dji_lens(path: str) -> str:
    """Air 3 & co. write the sensor/camera id (e.g. FC8282 wide, FC8284 tele) into the file."""
    try:
        with open(path, "rb") as f:
            chunk = f.read(4_000_000)
    except OSError:
        return ""
    m = re.search(rb"DJI (FC\d{4}[A-Z]?)", chunk)
    return m.group(1).decode() if m else ""


# ------------------------------------------------------------------- resolution

def _resolve_camera(info: ClipInfo, cfg: Config) -> None:
    cam = cfg.find_camera(info.make, info.model)
    if cam:
        info.camera_key, info.camera_name = cam.key, cam.name
    elif info.make or info.model:
        info.camera_key = naming.camera_key(info.make, info.model)
        info.camera_name = f"{info.make} {info.model}".strip()
    else:
        info.camera_key, info.camera_name = "UNKNOWN_CAMERA", "Unknown camera"
        info.notes.append("No camera metadata found")


def profile_from_hint(cfg: Config, hint: str, brand: str = "") -> str | None:
    """Match a gamma/profile string from the metadata against the hints in profiles.toml.

    The longest matching hint wins ('s-log3-cine' beats 's-log3', 'd-log m' beats 'd-log'); profiles of
    the camera's brand win ties.
    """
    text = re.sub(r"[_\s]+", " ", hint.lower())
    best: tuple[int, int, str] | None = None
    for p in cfg.profiles.values():
        for h in p.hints:
            if h and h in text:
                rank = (len(h), 1 if brand and brand in p.brands else 0, p.id)
                best = max(best, rank) if best else rank
    return best[2] if best else None


def _resolve_profile(info: ClipInfo, cfg: Config, hint: str | None) -> None:
    cam = cfg.find_camera(info.make, info.model)
    candidates = cam.profiles if cam else cfg.profiles_for_brand(cfg.brand_of(info.make))

    pid = profile_from_hint(cfg, hint, cfg.brand_of(info.make)) if hint else None
    if pid:
        info.profile, info.confidence = pid, METADATA
        return

    if info.transfer == "arib-std-b67":
        info.profile, info.confidence = "HLG", METADATA
        return
    if info.transfer == "smpte2084":
        info.profile, info.confidence = "REC709", GUESS
        info.notes.append("PQ/HDR footage – please check the profile")
        return

    if info.bit_depth >= 10:
        log = next((p for p in candidates if p not in ("HLG", "REC709")), None)
        if log:
            info.profile = log
            # DJI states nothing but only offers D-Log M/HLG in 10-bit: HLG is tagged, so the rest is log.
            info.confidence = INFERRED if cfg.brand_of(info.make) == "dji" else GUESS
            return
    info.profile, info.confidence = "REC709", GUESS if info.bit_depth >= 10 else INFERRED


# ------------------------------------------------------------------------ batch

def scan(paths: list[str], cfg: Config, workers: int = 8, progress=None) -> list[ClipInfo]:
    files = find_videos(paths)
    if not mediainfo.available():
        raise RuntimeError("exiftool is missing in runtime/ – please run install.sh")
    tags: dict[str, dict] = {}
    for i in range(0, len(files), mediainfo.BATCH):
        tags.update(mediainfo.read(files[i:i + mediainfo.BATCH]))
        if progress:
            progress(min(i + mediainfo.BATCH, len(files)) // 2, len(files), None)
    results: list[ClipInfo] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        infos = pool.map(lambda p: analyse(p, cfg, tags.get(os.path.normpath(p), {})), files)
        for i, info in enumerate(infos, 1):
            results.append(info)
            if progress:
                progress(len(files) // 2 + (i + 1) // 2, len(files), info)
    return results


@dataclass
class CameraGroup:
    camera_key: str
    camera_name: str
    make: str
    model: str
    profile: str
    confidence: str
    clips: list[ClipInfo]

    @property
    def id(self) -> str:
        return f"{self.camera_key}:{self.profile}"

    def summary(self, cfg: Config) -> dict:
        fps = sorted({c.fps for c in self.clips})
        res = sorted({f"{c.width}x{c.height}" for c in self.clips})
        warnings = []                 # frame-rate mismatches are shown by the UI (project fps is chosen there)
        prof = cfg.profiles.get(self.profile)
        source = transforms.source_for(cfg, prof, self.camera_key, self.camera_name) if prof else None
        if source and source.kind == transforms.MISSING:
            warnings.append(f"{prof.label}: {source.detail}")
        return {
            "id": self.id, "camera_key": self.camera_key, "camera_name": self.camera_name,
            "make": self.make, "model": self.model, "profile": self.profile,
            "profile_label": prof.label if prof else self.profile,
            "confidence": self.confidence, "count": len(self.clips),
            "duration": round(sum(c.duration for c in self.clips)),
            "fps": [_fmt_fps(f) for f in fps], "fps_values": fps, "resolutions": res,
            "bit_depth": sorted({c.bit_depth for c in self.clips}),
            "lenses": sorted({c.lens for c in self.clips if c.lens}),
            "group_name": naming.group_name(self.camera_key, prof.short if prof else self.profile),
            "profiles_available": _available_profiles(cfg, self),
            "source": source.as_dict() if source else None,
            "warnings": warnings,
        }


def _available_profiles(cfg: Config, group: CameraGroup) -> list[dict]:
    cam = cfg.find_camera(group.make, group.model)
    ids = cam.profiles if cam else cfg.profiles_for_brand(cfg.brand_of(group.make)) or list(cfg.profiles)
    return [{"id": i, "label": cfg.profiles[i].label} for i in ids if i in cfg.profiles]


def _fmt_fps(f: float) -> str:
    return str(int(f)) if f == int(f) else f"{f:.2f}"


def group_clips(clips: list[ClipInfo]) -> list[CameraGroup]:
    groups: dict[tuple[str, str], CameraGroup] = {}
    order = {METADATA: 0, INFERRED: 1, GUESS: 2}
    for c in clips:
        if c.error:
            continue
        key = (c.camera_key, c.profile)
        g = groups.get(key)
        if not g:
            g = groups[key] = CameraGroup(c.camera_key, c.camera_name, c.make, c.model, c.profile, c.confidence, [])
        g.clips.append(c)
        if order[c.confidence] > order[g.confidence]:
            g.confidence = c.confidence
    return sorted(groups.values(), key=lambda g: (-len(g.clips), g.camera_key))


def to_dict(info: ClipInfo) -> dict:
    return asdict(info) | {"name": info.name}
