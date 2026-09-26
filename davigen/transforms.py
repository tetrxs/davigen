"""Decide – per log profile and camera – where the input transform comes from.

Order: 1. Resolve's own CST (if the installed Resolve offers it)
       2. published formula via colour-science
       3. manufacturer's official LUT from its online catalog (cached, needs "online sources")
       4. a LUT file the user dropped in data/vendor_luts/<PROFILE>__<CAMERA>.cube
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import colormath, providers, resolve_catalog
from .config import Config, Profile, settings

CST, MATH, VENDOR, MANUAL, MISSING = "cst", "math", "vendor", "manual", "missing"
LABELS = {
    CST: "Resolve Color Space Transform",
    MATH: "Published formula (colour-science)",
    VENDOR: "Official manufacturer LUT",
    MANUAL: "Your LUT file",
    MISSING: "No source",
}


@dataclass
class Source:
    kind: str
    detail: str = ""
    needs_online: bool = False      # vendor LUT exists online but online sources are switched off
    path: Path | None = None        # vendor/manual LUT already on disk

    @property
    def label(self) -> str:
        return LABELS[self.kind]

    def as_dict(self) -> dict:
        return {"kind": self.kind, "label": self.label, "detail": self.detail, "needs_online": self.needs_online}


def vendor_key(profile: Profile, camera_key: str) -> str:
    return f"{profile.id}__{camera_key}"


def manual_lut(profile: Profile, camera_key: str) -> Path | None:
    for name in (vendor_key(profile, camera_key), profile.id):
        for ext in (".cube", ".CUBE"):
            p = providers.VENDOR_DIR / f"{name}{ext}"
            if p.exists():
                return p
    return None


def source_for(cfg: Config, profile: Profile, camera_key: str, camera_name: str, online: bool | None = None) -> Source:
    if profile.color_space and profile.gamma and resolve_catalog.supports(profile.color_space, profile.gamma):
        return Source(CST, f"{profile.color_space} / {profile.gamma}")
    if profile.math_curve and profile.math_gamut and colormath.available():
        return Source(MATH, f"{profile.math_curve} · {profile.math_gamut}")
    if profile.vendor:
        cached = providers.cached(vendor_key(profile, camera_key))
        if cached:
            entry = providers.manifest()[vendor_key(profile, camera_key)]
            return Source(VENDOR, f"{cached.name} (downloaded {entry['fetched']})", path=cached)
        provider = providers.get(profile.vendor)
        if provider is not None:
            allowed = settings()["online_sources"] if online is None else online
            if allowed:
                return Source(VENDOR, f"downloaded from the {provider.NAME}")
            return Source(VENDOR, f"available in the {provider.NAME} – allow online sources", needs_online=True)
    manual = manual_lut(profile, camera_key)
    if manual:
        return Source(MANUAL, manual.name, path=manual)
    return Source(MISSING, "Neither Resolve, a published formula nor a manufacturer catalog covers this profile – "
                           "choose the manufacturer's LUT file")


def fetch_vendor_lut(cfg: Config, profile: Profile, camera_key: str, camera_name: str) -> Path:
    """Download (once) the manufacturer's LUT for this camera model."""
    key = vendor_key(profile, camera_key)
    cached = providers.cached(key)
    if cached:
        return cached
    provider = providers.get(profile.vendor)
    if provider is None:
        raise LookupError(f"No online catalog for {profile.vendor}")
    candidates = provider.find(camera_name, profile.vendor_match, provider.catalog())
    if not candidates:
        raise LookupError(f"{camera_name}: no '{profile.label}' LUT in the {provider.NAME}")
    best = candidates[0]
    return providers.store(key, provider.download(best), best.url)
