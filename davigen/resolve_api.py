"""Thin helpers around the Resolve scripting API, plus version/feature detection.

Resolve (free) only allows scripts started from Workspace → Scripts (the `resolve` object is
injected there). Resolve Studio also accepts external connections via DaVinciResolveScript.
"""

from __future__ import annotations

import re
import sys
import time

API_MODULES = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules"


class ResolveError(RuntimeError):
    pass


def connect(injected=None):
    """Return the Resolve object: injected by the Scripts menu, or an external connection (Studio)."""
    if injected is not None:
        return injected
    if API_MODULES not in sys.path:
        sys.path.append(API_MODULES)
    try:
        import DaVinciResolveScript as dvr  # type: ignore
    except ImportError as e:
        raise ResolveError("DaVinciResolveScript not found – is DaVinci Resolve installed?") from e
    resolve = dvr.scriptapp("Resolve")
    if resolve is None:
        raise ResolveError("Can't reach Resolve. In the free version start davigen from "
                           "Workspace → Scripts → davigen inside Resolve.")
    return resolve


def is_studio(resolve) -> bool:
    return "studio" in (resolve.GetProductName() or "").lower()


def version(resolve) -> str:
    return f"{resolve.GetProductName()} {resolve.GetVersionString()}"


def version_tuple(resolve) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", resolve.GetVersionString() or "0")[:3])


def capabilities(resolve, project=None) -> dict[str, bool]:
    """What this Resolve version offers. Older versions simply get fewer automatic steps."""
    v = version_tuple(resolve)
    caps = {
        "color_groups": v >= (18, 5),        # Project.AddColorGroup / group node graphs
        "group_graphs": v >= (19, 0),        # ColorGroup.GetPreClipNodeGraph / GetPostClipNodeGraph
        "export_lut": v >= (18, 0),          # TimelineItem.ExportLUT
        "third_party_metadata": v >= (18, 0),
        "studio": is_studio(resolve),
    }
    if project is not None:
        try:
            project.GetColorGroupsList()
        except (AttributeError, TypeError):
            caps["color_groups"] = caps["group_graphs"] = False
    return caps


# ------------------------------------------------------------------ project folders

def open_pm_folder(pm, path: str) -> None:
    """Navigate the Project Manager into e.g. 'VIDEO_PROJECTS/ACTIVE', creating folders."""
    pm.GotoRootFolder()
    for part in [p for p in path.split("/") if p]:
        if part not in (pm.GetFolderListInCurrentFolder() or []):
            pm.CreateFolder(part)
        if not pm.OpenFolder(part):
            raise ResolveError(f"Can't open project manager folder '{part}'")


def find_project(pm, name: str, folder: str = "") -> bool:
    """Open the Project Manager folder that contains `name` (searches the given folder, then root)."""
    for path in filter(None, [folder, "/"]):
        try:
            open_pm_folder(pm, path) if path != "/" else pm.GotoRootFolder()
        except ResolveError:
            continue
        if name in (pm.GetProjectListInCurrentFolder() or []):
            return True
    return False


# ---------------------------------------------------------------------- media pool

def subfolder(folder, name: str):
    return next((f for f in folder.GetSubFolderList() or [] if f.GetName() == name), None)


def ensure_bin(media_pool, path: str):
    """Return the Media Pool folder at 'A/B/C', creating missing levels."""
    folder = media_pool.GetRootFolder()
    for part in [p for p in path.split("/") if p]:
        child = subfolder(folder, part)
        if child is None:
            child = media_pool.AddSubFolder(folder, part)
            if child is None:
                raise ResolveError(f"Can't create bin '{part}'")
        folder = child
    return folder


def find_timeline(project, name: str):
    for i in range(1, project.GetTimelineCount() + 1):
        tl = project.GetTimelineByIndex(i)
        if tl and tl.GetName() == name:
            return tl
    return None


def color_group(project, name: str, create: bool = True):
    group = next((g for g in project.GetColorGroupsList() or [] if g.GetName() == name), None)
    if group is None and create:
        group = project.AddColorGroup(name)
    return group


def wait_until(predicate, timeout: float = 10.0, interval: float = 0.2) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(interval)
    return False
