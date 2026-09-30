"""Delete a davigen project (concept §10): the Resolve project and the whole project folder go to the Trash.

Before anything is removed, the project is exported once more into its own folder, so putting the folder back
from the Trash restores everything, the Resolve project included (File → Import Project). Files outside the folder
(footage left in place, the originals behind links) are never touched."""

from __future__ import annotations

import subprocess
from pathlib import Path

from . import filesystem
from .resolve_api import ResolveError, find_project

STEPS = [("backup", "Last export of the Resolve project"), ("resolve", "Delete it in Resolve"),
         ("trash", "Project folder to the Trash"), ("forget", "Remove from davigen's list")]


def trash(path: Path) -> None:
    """Move to the Trash through Finder, so 'Put Back' works (like uninstall.sh)."""
    res = subprocess.run(["osascript", "-e", "on run argv", "-e",
                          'tell application "Finder" to delete (POSIX file (item 1 of argv) as alias)', "-e",
                          "end run", str(path)], capture_output=True, text=True, encoding="utf-8", errors="replace",
                         timeout=120)                    # macOS may ask once whether davigen may use Finder
    if res.returncode != 0:
        raise ResolveError(f"Finder couldn't move {path.name} to the Trash: {res.stderr.strip()[-200:]}")


def flow(resolve, cfg, options: dict, rep) -> None:
    entry = options["entry"]
    base, name = Path(entry["folder"]), entry["name"]
    pm = resolve.GetProjectManager()
    rep.start("backup")
    current = pm.GetCurrentProject()
    is_open = current is not None and current.GetName() == name and _base_of(current) == base
    if is_open:
        pm.SaveProject()
    found = find_project(pm, name, entry.get("pm_folder", ""))
    backup = base / "02_RESOLVE" / "01_PROJECT_FILES" / f"{name}_before_delete.drp"
    if found and base.exists():
        backup.parent.mkdir(parents=True, exist_ok=True)
        if not pm.ExportProject(name, str(backup), True):
            raise ResolveError("Resolve couldn't export the project – nothing was deleted")
        rep.finish("backup", str(backup.relative_to(base)))
    else:
        rep.finish("backup", "not in Resolve's Project Manager anymore" if not found else "folder already gone",
                   state="skipped")
    rep.start("resolve")
    if found:
        if is_open:
            pm.CloseProject(pm.GetCurrentProject())
        find_project(pm, name, entry.get("pm_folder", ""))
        if not pm.DeleteProject(name):
            raise ResolveError(f"Resolve didn't delete '{name}' – is it open in another window? The folder is kept")
        rep.finish("resolve", name)
    else:
        rep.finish("resolve", "nothing to delete", state="skipped")
    rep.start("trash")
    if base.exists():
        trash(base)
        rep.finish("trash", f"{base} (Finder → Trash → Put Back restores it)")
    else:
        rep.finish("trash", "folder already gone", state="skipped")
    rep.start("forget")
    filesystem.forget_project(str(base))
    rep.finish("forget")
    rep.result = {"deleted": name}


def _base_of(project) -> Path | None:
    gallery = project.GetSetting("colorGalleryStillsLocation") or ""
    return Path(gallery).parent.parent if gallery else None
