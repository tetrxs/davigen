"""Project folder tree on disk, project info file and the list of recent projects."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from . import naming
from .config import DATA_DIR, Config

PROJECTS = DATA_DIR / "projects.json"


def project_dir(root: str, project: str) -> Path:
    return Path(os.path.expanduser(root)) / project


def create_tree(cfg: Config, base: Path, cameras: list[str], extra: list[str] | None = None) -> list[Path]:
    """Create the standard tree, one 01_MEDIA/<NN>_<CAMERA> folder per camera and extra folders (exports)."""
    folders = cfg.workflow["folders"]
    paths = [base / p for p in [*folders["tree"], *(extra or [])]]
    media = base / folders["media_root"]
    cams = [p for p in media.glob("[0-8][0-9]_*") if p.is_dir()] if media.exists() else []
    existing = {p.name.split("_", 1)[1] for p in cams}
    next_index = 1 + len(cams)  # cameras use 01–89, 90+ are reserved (audio, stills, stock)
    for key in cameras:
        if key in existing:
            continue
        paths.append(media / naming.media_folder(next_index, key))
        next_index += 1
    for p in paths:
        p.mkdir(parents=True, exist_ok=True)
    return paths


def media_folder_for(cfg: Config, base: Path, camera_key: str) -> Path | None:
    media = base / cfg.workflow["folders"]["media_root"]
    return next((p for p in media.glob(f"[0-9][0-9]_{camera_key}") if p.is_dir()), None)


def info_file(base: Path) -> Path:
    return base / "00_ADMIN" / "PROJECT_INFO" / "davigen.json"


def write_project_info(base: Path, info: dict) -> Path:
    target = info_file(base)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = {"created": datetime.now().isoformat(timespec="seconds"), **info}
    target.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return target


def read_project_info(base: Path) -> dict:
    f = info_file(base)
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


# ------------------------------------------------------------------ recent projects

def register_project(name: str, base: Path, pm_folder: str) -> None:
    entries = [e for e in recent_projects(check=False) if e["folder"] != str(base)]
    entries.insert(0, {"name": name, "folder": str(base), "pm_folder": pm_folder,
                       "created": datetime.now().isoformat(timespec="seconds")})
    DATA_DIR.mkdir(exist_ok=True)
    PROJECTS.write_text(json.dumps(entries[:50], indent=2, ensure_ascii=False), encoding="utf-8")


def recent_projects(check: bool = True) -> list[dict]:
    """Projects davigen created, newest first. With check=True only those whose folder still exists."""
    entries = json.loads(PROJECTS.read_text(encoding="utf-8")) if PROJECTS.exists() else []
    if not check:
        return entries
    out = []
    for e in entries:
        info = read_project_info(Path(e["folder"]))
        if info:
            out.append({**e, "format": info.get("format", {}), "groups": info.get("groups", [])})
    return out


def folder_size(path: Path, limit_files: int = 20000) -> int:
    total = 0
    for i, f in enumerate(path.rglob("*")):
        if i > limit_files:
            break
        if f.is_file():
            total += f.stat().st_size
    return total
