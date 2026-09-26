"""Render presets (global in Resolve) and render jobs for the chosen deliveries."""

from __future__ import annotations

from pathlib import Path

from . import formats
from .config import Config
from .formats import Format
from .resolve_api import find_timeline


def _preset_names(project) -> set[str]:
    names = set()
    for p in project.GetRenderPresetList() or []:
        names.add(p if isinstance(p, str) else p.get("PresetName", ""))
    return names


def ensure_presets(project, cfg: Config, fmt: Format, studio: bool) -> list[str]:
    """Create the davigen render presets (shared by all projects) for this project's deliveries."""
    warnings = []
    existing = _preset_names(project)
    for spec in formats.deliveries(cfg, fmt, project.GetName()):
        tl = find_timeline(project, formats.timeline_for(cfg, fmt, spec))
        if spec["preset"] in existing and spec["resolution"] != "master":
            continue
        if tl is not None:
            project.SetCurrentTimeline(tl)
        if not project.SetCurrentRenderFormatAndCodec(spec["format"], spec["codec"]):
            warnings.append(f"{spec['label']}: {spec['format']}/{spec['codec']} isn't available in this Resolve")
            continue
        settings = {"ExportVideo": True, "ExportAudio": True}
        if tl is not None:
            settings["FormatWidth"] = int(tl.GetSetting("timelineResolutionWidth"))
            settings["FormatHeight"] = int(tl.GetSetting("timelineResolutionHeight"))
        project.SetRenderSettings(settings)
        if spec["preset"] in existing:
            project.DeleteRenderPreset(spec["preset"])   # master preset follows the project resolution
        if not project.SaveAsNewRenderPreset(spec["preset"]):
            warnings.append(f"Couldn't save the render preset {spec['preset']}")
    if not studio and (fmt.width, fmt.height) != formats.clamp_free(cfg, fmt.width, fmt.height):
        warnings.append("Resolve free renders up to UHD – the master will be limited accordingly")
    return warnings


def queue_all(project, cfg: Config, fmt: Format, base: Path) -> tuple[list[str], list[str]]:
    """Add one render job per delivery timeline that has content. Returns (job ids, warnings)."""
    jobs, warnings = [], []
    for spec in formats.deliveries(cfg, fmt, project.GetName()):
        name = formats.timeline_for(cfg, fmt, spec)
        tl = find_timeline(project, name)
        if tl is None or not tl.GetItemListInTrack("video", 1):
            warnings.append(f"{name} is empty or missing – no render job")
            continue
        project.SetCurrentTimeline(tl)
        if not project.LoadRenderPreset(spec["preset"]):
            warnings.append(f"Render preset {spec['preset']} is missing")
            continue
        target = base / spec["folder"]
        target.mkdir(parents=True, exist_ok=True)
        project.SetRenderSettings({"SelectAllFrames": True, "TargetDir": str(target),
                                   "CustomName": spec["filename"]})
        job = project.AddRenderJob()
        if job:
            jobs.append(job)
    return jobs, warnings
