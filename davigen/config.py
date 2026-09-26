"""Load davigen's TOML configuration (standard library only, runs inside Resolve)."""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
TEMPLATE_DIR = ROOT / "templates"
USER_CAMERAS = DATA_DIR / "user_cameras.json"
SETTINGS = DATA_DIR / "settings.json"
LUT_DIR = Path("/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT/davigen")


def _load(name: str) -> dict:
    with open(CONFIG_DIR / name, "rb") as f:
        return tomllib.load(f)


@dataclass(frozen=True)
class Profile:
    id: str
    label: str
    short: str
    color_space: str = ""            # Resolve CST enums (source 1)
    gamma: str = ""
    math_curve: str = ""             # colour-science formula (source 2)
    math_gamut: str = ""
    vendor: str = ""                 # manufacturer catalog provider (source 3)
    vendor_match: str = ""
    vendor_lut_output: str = ""
    tone_mapping: str = "none"       # "none" | "default"
    brands: tuple[str, ...] = ()
    hints: tuple[str, ...] = ()


@dataclass(frozen=True)
class Output:
    id: str
    label: str
    color_space: str
    gamma: str
    tone_mapping: str = "default"


@dataclass
class Camera:
    key: str
    name: str
    brand: str
    match: str
    profiles: list[str]
    image: str = ""
    user: bool = False

    def matches(self, model: str) -> bool:
        return bool(model) and re.search(self.match, model, re.IGNORECASE) is not None


@dataclass
class Config:
    workflow: dict = field(default_factory=lambda: _load("workflow.toml"))
    _profiles: dict = field(default_factory=lambda: _load("profiles.toml"))
    _cameras: dict = field(default_factory=lambda: _load("cameras.toml"))

    @cached_property
    def profiles(self) -> dict[str, Profile]:
        out = {}
        for pid, p in self._profiles["profiles"].items():
            p = dict(p)
            out[pid] = Profile(id=pid, **{k: tuple(v) if isinstance(v, list) else v for k, v in p.items()})
        return out

    @cached_property
    def outputs(self) -> dict[str, Output]:
        return {oid: Output(id=oid, label=o["label"], color_space=o["color_space"], gamma=o["gamma"],
                            tone_mapping=o.get("tone_mapping", "default"))
                for oid, o in self._profiles["outputs"].items()}

    @property
    def working(self) -> dict:
        return self._profiles["working"]

    @property
    def brands(self) -> dict[str, list[str]]:
        return self._cameras["brands"]

    @cached_property
    def cameras(self) -> list[Camera]:
        cams = [Camera(**c) for c in self._cameras["camera"]]
        if USER_CAMERAS.exists():
            cams = [Camera(**c, user=True) for c in json.loads(USER_CAMERAS.read_text(encoding="utf-8"))] + cams
        return cams

    def brand_of(self, make: str) -> str:
        make = (make or "").lower()
        for brand, needles in self.brands.items():
            if any(n in make for n in needles):
                return brand
        return ""

    def profiles_for_brand(self, brand: str) -> list[str]:
        return [p.id for p in self.profiles.values() if brand in p.brands or "*" in p.brands]

    def find_camera(self, make: str, model: str) -> Camera | None:
        brand = self.brand_of(make)
        for cam in self.cameras:
            if cam.matches(model) and (not brand or cam.brand == brand):
                return cam
        return None

    def save_user_camera(self, cam: Camera) -> None:
        DATA_DIR.mkdir(exist_ok=True)
        existing = json.loads(USER_CAMERAS.read_text(encoding="utf-8")) if USER_CAMERAS.exists() else []
        existing = [c for c in existing if c["key"] != cam.key]
        data = {k: v for k, v in cam.__dict__.items() if k != "user"}
        USER_CAMERAS.write_text(json.dumps([data, *existing], indent=2, ensure_ascii=False), encoding="utf-8")
        self.__dict__.pop("cameras", None)


def settings() -> dict:
    """User settings: online sources (manufacturer LUTs, camera catalog), default project location."""
    defaults = {"online_sources": None, "default_root": ""}   # None = not decided yet (asked on first start)
    if SETTINGS.exists():
        defaults.update(json.loads(SETTINGS.read_text(encoding="utf-8")))
    return defaults


def save_settings(**changes) -> dict:
    data = settings() | changes
    DATA_DIR.mkdir(exist_ok=True)
    SETTINGS.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data
