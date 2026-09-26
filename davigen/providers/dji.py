"""DJI: the download center (www.dji.com/downloads) embeds its whole catalog incl. every official LUT."""

from __future__ import annotations

import io
import re
import urllib.parse
import zipfile

from . import Candidate, http_get, model_tokens, tokens

CATALOG_URL = "https://www.dji.com/downloads"
BRAND_WORDS = ["dji"]
NAME = "DJI Download Center"


def catalog(html: str | None = None) -> list[tuple[str, str]]:
    """[(url, filename)] of every LUT-like file DJI lists."""
    if html is None:
        html = http_get(CATALOG_URL).decode("utf-8", "ignore")
    html = html.replace("\\u002F", "/").replace("\\/", "/")
    urls = sorted(set(re.findall(r'https?://[a-z0-9.-]*djicdn\.com/[^"\\<>]*?\.(?:cube|zip)', html, re.I)))
    return [(u, urllib.parse.unquote(u).rsplit("/", 1)[-1]) for u in urls]


def find(camera_name: str, match: str, files: list[tuple[str, str]]) -> list[Candidate]:
    """Files whose model part equals the camera model exactly (Air 3 ≠ Air 3S), best first."""
    want = model_tokens(camera_name, BRAND_WORDS)
    out = []
    for url, name in files:
        m = re.search(match, name, re.IGNORECASE)
        if not m or model_tokens(name[:m.start()], BRAND_WORDS) != want:
            continue
        score = 100
        rest = name[m.end():].lower()
        if "vivid" in rest or "color grading" in name.lower():
            score -= 50                      # creative looks, not the technical conversion
        version = re.search(r"v(\d+)(?:\.(\d+))?", rest)
        if version:
            score += int(version.group(1)) * 10 + int(version.group(2) or 0)
        if name.lower().endswith(".zip"):
            score -= 5
        if "size33" in rest:
            score -= 2                       # prefer the 65-point variant when both exist
        out.append(Candidate(url, name, score))
    return sorted(out, key=lambda c: (-c.score, c.filename))


def download(candidate: Candidate) -> bytes:
    data = http_get(candidate.url, timeout=120)
    if candidate.filename.lower().endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            cubes = [n for n in z.namelist() if n.lower().endswith(".cube") and "rec" in n.lower()]
            if not cubes:
                raise ValueError(f"{candidate.filename} contains no matching .cube file")
            data = z.read(sorted(cubes)[0])
    return data


__all__ = ["catalog", "find", "download", "tokens"]
