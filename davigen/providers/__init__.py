"""Manufacturer catalogs for official LUTs (used when neither Resolve nor a public formula covers a profile).

A provider knows where a manufacturer lists its downloads and how to pick the file for one camera
model. Adding a manufacturer = adding one module here; cameras and models are never hard-coded.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ..config import DATA_DIR

VENDOR_DIR = DATA_DIR / "vendor_luts"
MANIFEST = VENDOR_DIR / "manifest.json"
USER_AGENT = "Mozilla/5.0 (Macintosh; davigen; +https://github.com/tetrxs/davigen)"


@dataclass
class Candidate:
    url: str
    filename: str
    score: int


def tokens(text: str) -> list[str]:
    """'DJI Air 3S' -> ['dji', 'air', '3', 's']; letters and digits are separate tokens."""
    return re.findall(r"[a-z]+|\d+", text.lower())


def model_tokens(camera_name: str, brand_words: list[str]) -> list[str]:
    return [t for t in tokens(camera_name) if t not in brand_words]


def http_get(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - fixed https vendor URLs
        return r.read()


def manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}


def cached(key: str) -> Path | None:
    entry = manifest().get(key)
    path = VENDOR_DIR / entry["file"] if entry else None
    return path if path and path.exists() else None


def store(key: str, data: bytes, source_url: str, suffix: str = ".cube") -> Path:
    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    path = VENDOR_DIR / f"{key}{suffix}"
    path.write_bytes(data)
    m = manifest()
    m[key] = {"file": path.name, "url": source_url, "sha256": hashlib.sha256(data).hexdigest(),
              "fetched": date.today().isoformat()}
    MANIFEST.write_text(json.dumps(m, indent=2), encoding="utf-8")
    return path


def get(vendor: str):
    """Provider module for a manufacturer, or None if davigen has no catalog for it yet."""
    from . import dji  # noqa: PLC0415 - registry of implemented providers
    return {"dji": dji}.get(vendor)
