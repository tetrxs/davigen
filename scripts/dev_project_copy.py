"""Development helper: make a disposable copy of the open davigen project to test against.

Run from Resolve (copy to …/Fusion/Scripts/Utility/, then Workspace → Scripts → dev_project_copy). It saves the
open project, exports it, imports the export as ZZ_DAVIGEN_TEST_<name>, opens the copy, and points the copy at its
own project folder in ~/Movies/davigen_dev/, so davigen's cache and records for the copy never land in the real
project's folder. The media files are shared (read only). Delete the copy in the Project Manager when done.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

DEV_ROOT = Path.home() / "Movies" / "davigen_dev"
LOG = DEV_ROOT / "dev_project_copy.txt"
lines: list[str] = []


def log(msg: str) -> None:
    lines.append(msg)
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "replace").decode("ascii"))


def main(resolve) -> None:
    DEV_ROOT.mkdir(parents=True, exist_ok=True)
    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject()
    name = project.GetName()
    if name.startswith("ZZ_DAVIGEN_TEST_"):
        log(f"'{name}' is already a test copy – nothing to do.")
        return
    gallery = Path(project.GetSetting("colorGalleryStillsLocation") or "")
    base = gallery.parent.parent if gallery.name else None
    info = base / "00_ADMIN" / "PROJECT_INFO" / "davigen.json" if base else None
    if info is None or not info.exists():
        log(f"'{name}' isn't a davigen project (no davigen.json next to its gallery folder).")
        return
    copy = f"ZZ_DAVIGEN_TEST_{name}"
    drp = DEV_ROOT / f"{copy}_{time.strftime('%Y%m%d_%H%M%S')}.drp"
    log(f"save '{name}': {pm.SaveProject()}")
    log(f"export -> {drp}: {pm.ExportProject(name, str(drp), False)}")
    if not drp.exists():
        log("export failed – stopping")
        return
    existing = pm.GetProjectListInCurrentFolder() or []
    if copy in existing:
        copy = f"{copy}_{time.strftime('%H%M%S')}"
    log(f"import as '{copy}': {pm.ImportProject(str(drp), copy)}")
    log(f"open '{copy}': {bool(pm.LoadProject(copy))}")
    test = pm.GetCurrentProject()
    if test is None or test.GetName() != copy:
        log("the copy isn't open – stopping (the real project was not changed)")
        return
    dev_base = DEV_ROOT / copy
    (dev_base / "00_ADMIN" / "PROJECT_INFO").mkdir(parents=True, exist_ok=True)
    (dev_base / "02_RESOLVE" / "03_GALLERY").mkdir(parents=True, exist_ok=True)
    data = json.loads(info.read_text(encoding="utf-8"))
    data.update(project=copy, copied_from=str(base))
    (dev_base / "00_ADMIN" / "PROJECT_INFO" / "davigen.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    ok = test.SetSetting("colorGalleryStillsLocation", str(dev_base / "02_RESOLVE" / "03_GALLERY"))
    log(f"copy's project folder -> {dev_base}: {ok}")
    log(f"save copy: {pm.SaveProject()}")
    log(f"open timeline: {test.GetCurrentTimeline().GetName() if test.GetCurrentTimeline() else '-'}")


try:
    if globals().get("resolve") is not None:
        main(globals()["resolve"])
finally:
    DEV_ROOT.mkdir(parents=True, exist_ok=True)
    LOG.write_text("\n".join(lines) + "\n", encoding="utf-8")
