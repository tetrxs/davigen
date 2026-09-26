"""Create the standard timelines with named tracks and per-timeline resolutions."""

from __future__ import annotations

from . import formats
from .config import Config
from .formats import Format
from .resolve_api import ResolveError, ensure_bin, find_timeline


def create_all(project, cfg: Config, fmt: Format, assembly_clips: list) -> tuple[dict[str, object], list[str]]:
    """Create every timeline for this format. Returns ({name: timeline}, warnings)."""
    media_pool = project.GetMediaPool()
    created, warnings = {}, []
    for spec in formats.timelines(cfg, fmt):
        name = spec["name"]
        if find_timeline(project, name):
            warnings.append(f"Timeline {name} already exists – skipped")
            continue
        media_pool.SetCurrentFolder(ensure_bin(media_pool, spec["bin"]))
        if spec.get("fill_with_footage") and assembly_clips:
            tl = media_pool.CreateTimelineFromClips(name, assembly_clips)
        else:
            tl = media_pool.CreateEmptyTimeline(name)
        if tl is None:
            raise ResolveError(f"Resolve couldn't create the timeline {name}")
        if spec["resolution"] != "master":
            warnings += _custom_resolution(tl, *spec["resolution"])
        warnings += name_tracks(tl, cfg)
        created[name] = tl
    return created, warnings


def _custom_resolution(tl, width: int, height: int) -> list[str]:
    tl.SetSetting("useCustomSettings", "1")
    # like the project setting: height first, then width
    tl.SetSetting("timelineResolutionHeight", str(height))
    tl.SetSetting("timelineResolutionWidth", str(width))
    # custom timelines don't inherit the project's mismatch handling: fill the frame (crop from the
    # master) instead of letterboxing, then reframe shot by shot
    tl.SetSetting("timelineInputResMismatchBehavior", "scaleToCrop")
    got = (tl.GetSetting("timelineResolutionWidth"), tl.GetSetting("timelineResolutionHeight"))
    warnings = []
    if got != (str(width), str(height)):
        warnings.append(f"{tl.GetName()}: resolution is {got[0]}×{got[1]} instead of {width}×{height}")
    if tl.GetSetting("timelineInputResMismatchBehavior") != "scaleToCrop":
        warnings.append(f"{tl.GetName()}: please set 'Scale full frame with crop' in the timeline settings")
    return warnings


def name_tracks(tl, cfg: Config) -> list[str]:
    warnings = []
    wanted = cfg.workflow["tracks"]
    for kind, names in (("video", wanted["video"]), ("audio", wanted["audio"])):
        while tl.GetTrackCount(kind) < len(names):
            ok = tl.AddTrack(kind) if kind == "video" else tl.AddTrack(kind, "stereo")
            if not ok:
                warnings.append(f"{tl.GetName()}: couldn't add a {kind} track")
                break
        for idx, label in enumerate(names, 1):
            if idx <= tl.GetTrackCount(kind):
                tl.SetTrackName(kind, idx, label)
    return warnings
