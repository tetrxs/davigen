"""Create the Resolve project and apply the master settings."""

from __future__ import annotations

from pathlib import Path

from .config import Config
from .formats import Format
from .resolve_api import ResolveError, open_pm_folder

PM_FOLDER = "VIDEO_PROJECTS/ACTIVE"


def create(resolve, cfg: Config, name: str, base: Path, fmt: Format) -> tuple[object, list[str]]:
    """Create + load the project. Returns (project, warnings)."""
    pm = resolve.GetProjectManager()
    if pm.GetCurrentProject():
        pm.SaveProject()
    open_pm_folder(pm, PM_FOLDER)
    if name in (pm.GetProjectListInCurrentFolder() or []):
        raise ResolveError(f"A project called '{name}' already exists in {PM_FOLDER}")
    project = pm.CreateProject(name)
    if project is None:
        raise ResolveError(f"Resolve couldn't create the project '{name}'")
    return project, apply_settings(project, cfg, base, fmt)


def apply_settings(project, cfg: Config, base: Path, fmt: Format) -> list[str]:
    """Apply settings in the order Resolve needs; return human-readable warnings."""
    warnings: list[str] = []

    def setting(key: str, value, quiet: bool = False) -> bool:
        ok = project.SetSetting(key, str(value))
        got = str(project.GetSetting(key))
        # Resolve sometimes accepts a value but keeps the old one; numbers may come back as '25.0'
        if ok and (got == str(value) or _same_number(got, value)):
            return True
        if not quiet:
            warnings.append(f"Resolve didn't take the setting {key} = {value} (it is {got})")
        return False

    # Frame rate must be set before any media or timeline exists.
    setting("timelineFrameRate", fmt.resolve_fps)
    # Resolve validates each dimension against the other: height first, then width.
    setting("timelineResolutionHeight", fmt.height, quiet=True)
    setting("timelineResolutionWidth", fmt.width, quiet=True)
    got = (project.GetSetting("timelineResolutionWidth"), project.GetSetting("timelineResolutionHeight"))
    if got != (str(fmt.width), str(fmt.height)):
        warnings.append(f"Timeline resolution is {got[0]}×{got[1]} instead of {fmt.width}×{fmt.height}")

    for key, value in cfg.workflow["project"]["settings"].items():
        setting(key, value)
    setting("videoMonitorFormat", f"HD 1080p {fmt.resolve_fps}", quiet=True)   # only matters with a monitor card
    setting("colorGalleryStillsLocation", base / cfg.workflow["folders"]["gallery"])

    if not _same_number(project.GetSetting("timelinePlaybackFrameRate"), fmt.resolve_fps):
        warnings.append(f"MANUAL: Project Settings → Master Settings → set Playback frame rate to {fmt.resolve_fps} "
                        "(Resolve's scripting API can't set it)")
    return warnings


def _same_number(a, b) -> bool:
    try:
        return abs(float(a) - float(b)) < 1e-3
    except (TypeError, ValueError):
        return False
