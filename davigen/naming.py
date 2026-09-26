"""Naming conventions: UPPER_SNAKE names, no spaces, versioned with _vNNN."""

from __future__ import annotations

import re
import unicodedata

_UMLAUTS = {"Ä": "AE", "Ö": "OE", "Ü": "UE", "ß": "SS", "ä": "AE", "ö": "OE", "ü": "UE"}


def normalize(text: str) -> str:
    """'Italy Summer 2026' -> 'ITALY_SUMMER_2026'."""
    text = "".join(_UMLAUTS.get(c, c) for c in text)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")
    return re.sub(r"_+", "_", text).upper()


def validate_project_name(name: str) -> list[str]:
    problems = []
    if not name:
        problems.append("Name missing")
    elif name != normalize(name):
        problems.append(f"Will be saved as {normalize(name)} (capitals, underscores, no spaces)")
    if re.search(r"FINAL", name or "", re.IGNORECASE):
        problems.append("Avoid FINAL in names – versions are numbered _v001, _v002 …")
    return problems


def camera_key(make: str, model: str) -> str:
    """Fallback key for unknown cameras, e.g. ('Panasonic', 'DC-S9') -> 'PANASONIC_DC_S9'."""
    make_n = normalize(make.split()[0]) if make else ""
    model_n = normalize(model)
    if make_n and model_n.startswith(make_n):
        return model_n
    return "_".join(p for p in (make_n, model_n) if p) or "UNKNOWN_CAMERA"


def group_name(camera_key: str, profile_short: str) -> str:
    return f"G_{camera_key}_{profile_short}"


def media_folder(index: int, camera_key: str) -> str:
    return f"{index:02d}_{camera_key}"
