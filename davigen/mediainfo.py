"""Read clip metadata with the bundled exiftool (pure Perl, runs on the Perl every Mac ships).

exiftool knows the maker notes of practically every camera; davigen only needs a few generic
fields from it. The real video bit depth (exiftool reports 24 = 3×8 for HEVC) is read directly
from the HEVC/AVC configuration box in the file.
"""

from __future__ import annotations

import json
import os
import struct
import subprocess

from .config import ROOT

EXIFTOOL = ROOT / "runtime" / "exiftool" / "exiftool"
PERL = "/usr/bin/perl"
BATCH = 40


def available() -> bool:
    return EXIFTOOL.exists() and os.path.exists(PERL)


def read(paths: list[str]) -> dict[str, dict]:
    """{path: {'Group:Tag': value}} for all files (printable values, grouped tag names)."""
    out: dict[str, dict] = {}
    for i in range(0, len(paths), BATCH):
        chunk = paths[i:i + BATCH]
        res = subprocess.run(
            [PERL, str(EXIFTOOL), "-j", "-G1", "-a", "-api", "LargeFileSupport=1", "-charset", "filename=utf8",
             *chunk], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
        try:
            data = json.loads(res.stdout or "[]")
        except json.JSONDecodeError:
            data = []
        for entry in data:
            out[os.path.normpath(entry.get("SourceFile", ""))] = entry
    return out


# ------------------------------------------------------------- ISO-BMFF bit depth

_CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl"}


def bit_depth(path: str) -> int | None:
    """Luma bit depth from the hvcC/avcC box of an MP4/MOV (None if not found)."""
    try:
        with open(path, "rb") as f:
            size = os.fstat(f.fileno()).st_size
            return _walk(f, 0, size)
    except OSError:
        return None


def _boxes(f, start: int, end: int):
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        header = f.read(16)
        if len(header) < 8:
            return
        size, kind = struct.unpack(">I4s", header[:8])
        head = 8
        if size == 1 and len(header) >= 16:
            size, head = struct.unpack(">Q", header[8:16])[0], 16
        elif size == 0:
            size = end - pos
        if size < head:
            return
        yield kind, pos + head, pos + size
        pos += size


def _walk(f, start: int, end: int) -> int | None:
    for kind, body, box_end in _boxes(f, start, end):
        if kind in _CONTAINERS:
            depth = _walk(f, body, box_end)
            if depth:
                return depth
        elif kind == b"stsd":
            return _sample_description(f, body, box_end)
    return None


def _sample_description(f, body: int, end: int) -> int | None:
    # stsd: version/flags(4) + entry count(4), then sample entries; video entries have 78 bytes of fields
    for kind, entry, entry_end in _boxes(f, body + 8, end):
        for sub, sub_body, sub_end in _boxes(f, entry + 78, entry_end):
            f.seek(sub_body)
            data = f.read(min(sub_end - sub_body, 64))
            if sub == b"hvcC" and len(data) > 17:
                return (data[17] & 0x07) + 8          # bitDepthLumaMinus8
            if sub == b"avcC" and len(data) > 1:
                return 10 if data[1] in (110, 122, 244) else 8   # High 10 / 4:2:2 / 4:4:4 profiles
        if kind in (b"apch", b"apcn", b"apcs", b"apco", b"ap4h", b"ap4x"):
            return 10                                 # ProRes
    return None


def first(tags: dict, *suffixes: str) -> str:
    """First non-empty value whose tag name ends with one of the suffixes (group-agnostic)."""
    for suffix in suffixes:
        for key, value in tags.items():
            if key.split(":")[-1] == suffix and value not in ("", None, 0):
                return str(value).strip()
    return ""


PROFILE_TAG_WORDS = ("gamma", "photostyle", "pictureprofile", "picturestyle", "filmmode", "filmsimulation",
                     "colorprofile", "colormode", "colorspace", "protune", "loglut", "logmode", "look",
                     "picturemode", "capturegamut", "gamut", "curve")


def profile_text(tags: dict) -> str:
    """All values of profile-related tags, joined – matched against the hints in profiles.toml."""
    parts = []
    for key, value in tags.items():
        name = key.split(":")[-1].lower()
        if (isinstance(value, str) and any(w in name for w in PROFILE_TAG_WORDS) and len(value) < 200
                and name != "colorprofiles"):
            parts.append(value)
    return " | ".join(parts)


def video_track(tags: dict) -> dict:
    """Tags of the first track that carries picture size and frame rate."""
    groups: dict[str, dict] = {}
    for key, value in tags.items():
        if ":" in key:
            g, name = key.split(":", 1)
            groups.setdefault(g, {})[name] = value
    for g in sorted(groups):
        t = groups[g]
        if t.get("ImageWidth") and t.get("VideoFrameRate"):
            return t
    return {}


def number(value) -> float:
    """'59.94', '25', '26.88 s', '0:01:35.73' -> float seconds/rate (0.0 if unknown)."""
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").replace(" s", "").strip()
    try:
        if ":" in text:
            total = 0.0
            for part in text.split(":"):
                total = total * 60 + float(part)
            return total
        return float(text.split()[0]) if text else 0.0
    except ValueError:
        return 0.0
