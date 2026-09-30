"""Assets: every file a project owns, what kind it is, and what has been done to it (concept §3).

The record (00_ADMIN/PROJECT_INFO/assets.json) is davigen's memory of the project. It is never trusted blindly:
every action checks the real state in Resolve and on disk; the record makes those checks fast, keeps the settings
an action ran with, and recognises a file that is brought in a second time.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path

from .. import mediainfo, naming, scanner
from ..config import Config

RECORD = Path("00_ADMIN") / "PROJECT_INFO" / "assets.json"
VIDEO, AUDIO, STILL_IMAGE, GRAPHIC_IMAGE = "video", "audio", "still", "graphic"

EXT = {
    **{e: VIDEO for e in scanner.VIDEO_EXT},
    **{e: AUDIO for e in (".wav", ".aif", ".aiff", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".caf", ".opus",
                          ".wma", ".bwf")},
    **{e: STILL_IMAGE for e in (".jpg", ".jpeg", ".heic", ".heif", ".tif", ".tiff", ".dng", ".arw", ".cr2", ".cr3",
                                ".nef", ".rw2", ".raf", ".orf", ".bmp", ".exr", ".dpx", ".avif")},
    **{e: GRAPHIC_IMAGE for e in (".png", ".psd", ".svg", ".gif", ".webp", ".ai", ".eps", ".tga")},
    **{e: "lut" for e in (".cube", ".3dl")},
    **{e: "font" for e in (".ttf", ".otf", ".ttc", ".woff", ".woff2")},
    **{e: "document" for e in (".pdf", ".txt", ".md", ".rtf", ".doc", ".docx", ".pages", ".srt", ".vtt",
                               ".csv", ".xlsx", ".numbers", ".key", ".pptx")},
}
SKIP_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}
SKIP_DIRS = {"MISC", "PRIVATE", "AVF_INFO", "CANONMSC", "THMBNL", "CLIP_INFO"}   # card housekeeping folders
KINDS = ("camera", "stock", "still", "graphic", "logo", "music", "voice", "sfx", "lut", "font", "document")
VIDEO_KINDS = {"camera", "stock"}
AUDIO_KINDS = {"music", "voice", "sfx"}


@dataclass
class Asset:
    id: str                                 # fingerprint: survives the move into the project
    kind: str
    name: str
    source: str                             # where it came from (absolute)
    size: int = 0
    path: str = ""                          # where it is now (absolute; a link for mode "link")
    import_path: str = ""                   # the file Resolve imports (a converted copy, else path)
    mode: str = ""                          # move | copy | link | leave
    created: str = ""                       # recording time (sortable) where known
    added: str = ""                         # when davigen took it in
    media_id: str = ""                      # Resolve MediaPoolItem unique id
    camera_key: str = ""
    camera_name: str = ""
    make: str = ""
    model: str = ""
    profile: str = ""
    group: str = ""                         # colour group (camera clips)
    info: dict = field(default_factory=dict)          # fps, size, duration, sample rate … (for estimates and UI)
    done: dict = field(default_factory=dict)          # action id → {"at", "values", "detail"}
    removed: bool = False                   # gone from the Media Pool (noticed, nothing redone)

    @property
    def resolve_path(self) -> str:
        return self.import_path or self.path or self.source

    def mark(self, action_id: str, values: dict | None = None, detail: str = "") -> None:
        self.done[action_id] = {"at": datetime.now().isoformat(timespec="seconds"), "values": values or {},
                                "detail": detail}

    def unmark(self, action_id: str) -> None:
        self.done.pop(action_id, None)

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Asset":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


# ------------------------------------------------------------------------------------------------ identity

def fingerprint(path: str | Path, chunk: int = 1 << 20) -> str:
    """Size + first and last megabyte: fast on a 10 GB clip, the same before and after a move or copy."""
    p = Path(path)
    size = p.stat().st_size
    h = hashlib.blake2b(digest_size=10)
    h.update(str(size).encode())
    with open(p, "rb") as f:
        h.update(f.read(chunk))
        if size > 2 * chunk:
            f.seek(size - chunk)
            h.update(f.read(chunk))
    return h.hexdigest()


# ------------------------------------------------------------------------------------------------- record

class AssetStore:
    """The project's asset record. Written atomically after every change."""

    def __init__(self, base: Path):
        self.base = Path(base)
        self.file = self.base / RECORD
        self.assets: dict[str, Asset] = {}
        if self.file.exists():
            data = json.loads(self.file.read_text(encoding="utf-8"))
            self.assets = {a["id"]: Asset.from_dict(a) for a in data.get("assets", [])}

    def __iter__(self):
        return iter(self.assets.values())

    def __len__(self):
        return len(self.assets)

    def get(self, asset_id: str) -> Asset | None:
        return self.assets.get(asset_id)

    def put(self, asset: Asset) -> None:
        self.assets[asset.id] = asset

    def drop(self, asset_id: str) -> None:
        self.assets.pop(asset_id, None)

    def by_path(self) -> dict[str, Asset]:
        out = {}
        for a in self.assets.values():
            for p in {a.path, a.import_path, a.source}:
                if p:
                    out[p] = a
        return out

    def save(self) -> None:
        from ..transfer import _write_json  # noqa: PLC0415 - atomic write with fsync
        _write_json(self.file, {"version": 1, "assets": [a.as_dict() for a in self.assets.values()]})


# ---------------------------------------------------------------------------------------------- listing

def find_files(paths: list[str]) -> list[str]:
    """Every file below the given files and folders that davigen knows how to handle (sidecars excluded: they
    travel with their clip)."""
    found: list[str] = []
    for p in paths:
        p = os.path.expanduser(p)
        if os.path.isfile(p):
            found.append(p)
            continue
        for root, dirs, files in os.walk(p):
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d.upper() not in SKIP_DIRS)
            found += [os.path.join(root, f) for f in sorted(files)]
    out = []
    for f in sorted(set(found)):
        name = os.path.basename(f)
        if name.startswith(".") or name in SKIP_NAMES or scanner.SKIP_SUFFIX and \
                os.path.splitext(name)[0].lower().endswith(scanner.SKIP_SUFFIX):
            continue
        if os.path.splitext(name)[1].lower() not in EXT:
            continue
        out.append(f)
    videos = {os.path.splitext(f)[0].lower() for f in out if EXT[os.path.splitext(f)[1].lower()] == VIDEO}
    return [f for f in out if not _is_sidecar(f, videos)]


def _is_sidecar(path: str, video_stems: set[str]) -> bool:
    """DJI .SRT, Sony M01.XML … belong to a clip next to them (transfer.sidecars moves them along)."""
    stem, ext = os.path.splitext(path)
    if ext.lower() not in (".srt", ".xml", ".lrf", ".thm"):
        return False
    stem = stem.lower()
    return stem in video_stems or (stem.endswith("m01") and stem[:-3] in video_stems)


# ------------------------------------------------------------------------------------------ classifying

def _words(name: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", name.lower()) if w}


def _hint(name: str, hints: list[str]) -> bool:
    words = _words(Path(name).stem)
    return any(h in words or (len(h) > 3 and h in Path(name).stem.lower()) for h in hints)


def audio_kind(cfg: Config, name: str, tags: dict) -> str:
    """Music, voice or sound effect – from the name, the tags and the length. The user can change it."""
    rules = cfg.workflow["assets"]
    if _hint(name, rules["voice_hints"]):
        return "voice"
    if _hint(name, rules["sfx_hints"]):
        return "sfx"
    if _hint(name, rules["music_hints"]) or mediainfo.first(tags, "Artist", "Album", "Genre", "Band"):
        return "music"
    if mediainfo.first(tags, "Originator", "OriginatorReference", "iXML", "Scene", "Take"):
        return "voice"                                  # field recorders write BWF / iXML
    duration = mediainfo.number(mediainfo.first(tags, "Duration"))
    if duration and duration < rules["sfx_below"]:
        return "sfx"
    if duration >= rules["music_from"]:
        return "music"
    return "voice"


def image_kind(cfg: Config, name: str, tags: dict) -> str:
    if _hint(name, cfg.workflow["assets"]["logo_hints"]):
        return "logo"
    ext = Path(name).suffix.lower()
    if EXT.get(ext) == STILL_IMAGE:
        return "still"
    if ext == ".png":
        colour = mediainfo.first(tags, "ColorType").lower()
        return "graphic" if "alpha" in colour or mediainfo.first(tags, "Transparency") else "still"
    return "graphic"


def audio_info(tags: dict) -> dict:
    return {"duration": mediainfo.number(mediainfo.first(tags, "Duration")),
            "rate": int(mediainfo.number(mediainfo.first(tags, "SampleRate", "AudioSampleRate"))),
            "channels": int(mediainfo.number(mediainfo.first(tags, "NumChannels", "AudioChannels"))),
            "codec": mediainfo.first(tags, "AudioFormat", "Encoding", "FileType"),
            "bits": int(mediainfo.number(mediainfo.first(tags, "BitsPerSample", "AudioBitsPerSample"))),
            "picture": bool(mediainfo.first(tags, "Picture", "CoverArt", "PictureMIMEType", "PictureType"))}


def classify(paths: list[str], cfg: Config, progress=None) -> tuple[list[Asset], list[scanner.ClipInfo], list[dict]]:
    """Look at every file once. Returns (assets, clip infos of the videos, files that couldn't be read)."""
    files = find_files(paths)
    if not mediainfo.available():
        raise RuntimeError("exiftool is missing in runtime/ – please run install.sh")
    tags: dict[str, dict] = {}
    for i in range(0, len(files), mediainfo.BATCH):
        tags.update(mediainfo.read(files[i:i + mediainfo.BATCH]))
        if progress:
            progress(min(i + mediainfo.BATCH, len(files)) // 2, len(files))
    assets, clips, errors = [], [], []
    for n, f in enumerate(files, 1):
        if progress:
            progress(len(files) // 2 + (n + 1) // 2, len(files))
        t = tags.get(os.path.normpath(f), {})
        ext = Path(f).suffix.lower()
        try:
            asset = Asset(id=fingerprint(f), kind="", name=Path(f).name, source=f, size=Path(f).stat().st_size)
        except OSError as e:
            errors.append({"name": Path(f).name, "error": str(e)})
            continue
        family = EXT[ext]
        if family == VIDEO:
            info = scanner.analyse(f, cfg, t)
            if info.error:
                errors.append({"name": info.name, "error": info.error})
                continue
            clips.append(info)
            asset.kind = "stock" if info.camera_key == "UNKNOWN_CAMERA" else "camera"
            asset.created = info.created
            asset.camera_key, asset.camera_name, asset.make, asset.model = (info.camera_key, info.camera_name,
                                                                             info.make, info.model)
            asset.profile = info.profile
            asset.info = {"fps": info.fps, "width": info.width, "height": info.height, "duration": info.duration,
                          "codec": info.codec, "bit_depth": info.bit_depth}
        elif family == AUDIO:
            asset.kind = audio_kind(cfg, asset.name, t)
            asset.info = audio_info(t)
        elif family in (STILL_IMAGE, GRAPHIC_IMAGE):
            asset.kind = image_kind(cfg, asset.name, t)
            asset.info = {"width": int(mediainfo.number(mediainfo.first(t, "ImageWidth"))),
                          "height": int(mediainfo.number(mediainfo.first(t, "ImageHeight"))),
                          "frames": int(mediainfo.number(mediainfo.first(t, "FrameCount", "AnimationFrames")))}
            asset.created = _created(t)
        else:
            asset.kind = family
        if not asset.created:
            asset.created = _created(t)
        assets.append(asset)
    return assets, clips, errors


def _created(tags: dict) -> str:
    value = mediainfo.first(tags, "DateTimeOriginal", "CreateDate", "CreationDate", "FileModifyDate")
    m = re.match(r"(\d{4}):(\d{2}):(\d{2})[ T](.*)", value or "")
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}T{m.group(4)}" if m else ""


# ------------------------------------------------------------------------------------------- where it goes

def kind_spec(cfg: Config, kind: str) -> dict:
    return cfg.workflow["assets"]["kinds"][kind]


def destination(cfg: Config, base: Path, asset: Asset, camera_folder: Path | None = None,
                taken: set[str] | None = None) -> Path:
    """Where a file lives in the project. Never an existing file (or one already planned in this run)."""
    taken = taken if taken is not None else set()
    day = (asset.created or "")[:10] or "UNDATED"
    source = naming.normalize(Path(asset.source).parent.name) or "SOURCE"
    if asset.kind == "camera":
        root = camera_folder or base / cfg.workflow["folders"]["media_root"]
        stem = f"{day}_{source}"
    else:
        root = base / kind_spec(cfg, asset.kind)["folder"]
        stem = source if asset.kind in VIDEO_KINDS | {"still"} else ""
    folder = root / stem if stem else root
    n = 2
    target = folder / asset.name
    while target.exists() or target.is_symlink() or str(target) in taken:
        if stem:
            folder = root / f"{stem}_{n}"
            target = folder / asset.name
        else:
            target = folder / f"{Path(asset.name).stem}_{n}{Path(asset.name).suffix}"
        n += 1
    taken.add(str(target))
    return target


def bin_for(cfg: Config, asset: Asset, camera_bin: str = "") -> str:
    if asset.kind == "camera":
        return f"{cfg.workflow['bins']['footage']}/{camera_bin}" if camera_bin else cfg.workflow["bins"]["footage"]
    return kind_spec(cfg, asset.kind)["bin"]


def summary(assets: list[Asset], cfg: Config) -> list[dict]:
    """Assets grouped by kind, for the import screen."""
    out = []
    for kind in KINDS:
        items = [a for a in assets if a.kind == kind]
        if items:
            out.append({"kind": kind, "label": kind_spec(cfg, kind)["label"], "count": len(items),
                        "size": sum(a.size for a in items),
                        "assets": [{"id": a.id, "name": a.name, "source": a.source, "size": a.size,
                                    "info": a.info, "camera": a.camera_name, "profile": a.profile}
                                   for a in items]})
    return out
