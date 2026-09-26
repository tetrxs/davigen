"""Which Color Space Transform options does the *installed* Resolve offer?

Read straight from Resolve's library, so profiles Blackmagic adds in an update (e.g. a future
D-Log M) are picked up without changing davigen. Cached per Resolve build.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from .config import DATA_DIR

FUSION_LIB = Path("/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/libfusionsystem.dylib")
_TOKEN = re.compile(rb"\b([A-Z][A-Z0-9_]{2,40}_(?:COLORSPACE|GAMMA))\b")


@lru_cache(maxsize=1)
def cst_enums(lib: Path = FUSION_LIB) -> dict[str, list[str]]:
    """{'color_spaces': [...], 'gammas': [...]} available in this Resolve installation."""
    if not lib.exists():
        return {"color_spaces": [], "gammas": []}
    stamp = f"{lib.stat().st_size}-{int(lib.stat().st_mtime)}"
    cache = DATA_DIR / "resolve_cst_enums.json"
    if cache.exists():
        data = json.loads(cache.read_text(encoding="utf-8"))
        if data.get("stamp") == stamp:
            return data["enums"]
    found = set(_TOKEN.findall(lib.read_bytes()))
    names = sorted(t.decode() for t in found)
    enums = {"color_spaces": [n for n in names if n.endswith("_COLORSPACE")],
             "gammas": [n for n in names if n.endswith("_GAMMA")]}
    DATA_DIR.mkdir(exist_ok=True)
    cache.write_text(json.dumps({"stamp": stamp, "enums": enums}, indent=1), encoding="utf-8")
    return enums


def supports(color_space: str, gamma: str) -> bool:
    enums = cst_enums()
    return color_space in enums["color_spaces"] and gamma in enums["gammas"]
