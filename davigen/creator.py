"""Flows outside the pipeline that maintain the open project (rebuild colour groups, assign groups on every
timeline, queue renders) and the helpers every flow uses: where the project folder is, its format.

New projects and every import run on the pipeline (davigen/pipeline). Every flow reports into a Reporter or a
pipeline Run, both polled by the UI via /api/progress."""

from __future__ import annotations

import os
import subprocess
import traceback
from pathlib import Path

from . import color, deliver, filesystem, formats, media_pool, transfer
from .config import Config
from .formats import Format
from .resolve_api import ResolveError, capabilities

COLOR_STEPS = [("color", "Rebuild color groups + LUTs"), ("assign", "Groups + node structure"), ("save", "Save")]
ASSIGN_STEPS = [("assign", "Groups + node structure on all timelines"), ("basic", "Basic correction"), ("save", "Save")]
QUEUE_STEPS = [("deliver", "Add render jobs")]

TRANSFER_LABEL = {transfer.MOVE: "moved", transfer.COPY: "copied", transfer.LEAVE: "left in place"}


class Reporter:
    """Collects step states for the UI."""

    def __init__(self, steps: list[tuple[str, str]]):
        self.steps = {sid: {"id": sid, "label": label, "state": "pending", "detail": ""} for sid, label in steps}
        self.warnings: list[str] = []
        self.manual: list[str] = []
        self.done = False
        self.error = ""
        self.result: dict = {}
        self.journal: transfer.Journal | None = None
        self.live: list[dict] = []              # the latest pictures of a flow (Basic correction: clip by clip)
        self.images: dict[int, bytes] = {}
        self._shown = 0

    def show(self, info: dict, png: bytes | None = None, keep: int = 12) -> None:
        """A picture of what a flow is doing right now, for the UI's live view (kept: the last `keep`)."""
        self._shown += 1
        if png:
            self.images[self._shown] = png
        self.live = (self.live + [{**info, "n": self._shown, "image": bool(png)}])[-keep:]
        for n in [n for n in self.images if n <= self._shown - keep]:
            del self.images[n]

    def start(self, sid: str, detail: str = ""):
        self.steps[sid].update(state="running", detail=detail)

    def detail(self, sid: str, detail: str):
        self.steps[sid]["detail"] = detail

    def finish(self, sid: str, detail: str = "", state: str = "done"):
        self.steps[sid].update(state=state, detail=detail or self.steps[sid]["detail"])

    def warn(self, items: list[str]):
        for w in items:
            target = self.manual if w.startswith("MANUAL: ") else self.warnings
            text = w.removeprefix("MANUAL: ")
            if text not in target:
                target.append(text)

    def snapshot(self) -> dict:
        return {"steps": list(self.steps.values()), "warnings": self.warnings, "manual": self.manual,
                "done": self.done, "error": self.error, "result": self.result, "live": self.live}


def run(flow, *args, rep: Reporter) -> None:
    """Run a flow; on any error put transferred footage back and report."""
    try:
        flow(*args, rep)
        if rep.journal is not None:
            rep.journal.commit()
    except Exception as e:  # noqa: BLE001 - surfaced in the UI
        running = next((s for s in rep.steps.values() if s["state"] == "running"), None)
        if running:
            running["state"] = "error"
        rep.error = f"{e}" if isinstance(e, ResolveError) else f"{e}\n\n{traceback.format_exc()}"
        if rep.journal is not None and not rep.journal.committed:
            result = rep.journal.rollback()
            rep.error += (f"\n\nFootage put back where it was: {result['restored']} restored, "
                          f"{result['removed']} copies removed.")
            if result["problems"]:
                rep.error += "\nNeeds a look: " + "; ".join(result["problems"])
    finally:
        rep.done = True


# ============================================================================ helpers

def project_base(project) -> Path:
    """The project folder on disk: davigen points the gallery stills at <base>/02_RESOLVE/03_GALLERY
    (Resolve keeps that setting; the cache location it resets on reload)."""
    gallery = project.GetSetting("colorGalleryStillsLocation") or ""
    base = Path(gallery).parent.parent if gallery else None
    if not base or not (base / "00_ADMIN" / "PROJECT_INFO" / "davigen.json").exists():
        raise ResolveError("The open project wasn't created with davigen (project folder unknown)")
    return base


def project_format(cfg: Config, project, base: Path) -> Format:
    """The format stored when the project was created (falls back to the project settings)."""
    info = filesystem.read_project_info(base)
    f = info.get("format")
    if f:
        return Format(f["width"], f["height"], f["fps"], f["aspect"], f.get("deliveries", []))
    w, h = int(project.GetSetting("timelineResolutionWidth")), int(project.GetSetting("timelineResolutionHeight"))
    return Format(w, h, float(project.GetSetting("timelineFrameRate")), formats.aspect_of(cfg, w, h))


def _color(resolve, proj, cfg: Config, specs: list[dict], scratch, rep: Reporter) -> None:
    rep.start("color")
    caps = capabilities(resolve, proj)
    if not caps["color_groups"]:
        rep.finish("color", "this Resolve version has no color group scripting (needs 18.5+)", state="skipped")
        rep.warn(["MANUAL: Create the color groups by hand (Color page → right-click clip → Groups)"])
        return
    if scratch is None:
        rep.finish("color", "no clips in the project yet – LUTs are built with the first import", state="skipped")
        return
    groups, warns = color.setup_groups(resolve, proj, cfg, specs, scratch,
                                       progress=lambda d: rep.detail("color", d))
    rep.warn(warns)
    rep.finish("color", ", ".join(groups))


def _assign(proj, rep: Reporter, timelines_to_check: list) -> list:
    """Colour group per clip + the standard node structure on clips that have no grade yet.

    Returns the timelines where clips got the node structure (Basic Correction follows up on those)."""
    rep.start("assign")
    total_ok = total_skip = structured = 0
    touched = []
    for tl in timelines_to_check:
        ok, skip = color.assign(proj, tl)
        total_ok, total_skip = total_ok + ok, total_skip + skip
        applied = color.apply_clip_structure(tl)[0]
        structured += applied
        if applied:
            touched.append(tl)
    if not total_ok and not total_skip:
        rep.finish("assign", "no clips on the timelines", state="skipped")
    else:
        rep.finish("assign", f"{total_ok} clips in their groups"
                   + (f", {total_skip} without a davigen group" if total_skip else "")
                   + (f", {structured} got the node structure" if structured else ""))
    return touched


class _StepReporter:
    """Lets a sub-flow (Basic Correction) report into one step of the running flow."""

    def __init__(self, rep: Reporter, sid: str, labels: dict[str, str]):
        self.rep, self.sid, self.labels = rep, sid, labels
        self.result: dict = {}

    def start(self, sub: str, detail: str = ""):
        self.rep.detail(self.sid, f"{self.labels.get(sub, sub)}{' · ' + detail if detail else ''}")

    detail = start

    def finish(self, sub: str, detail: str = "", state: str = "done"):
        self.start(sub, detail)

    def warn(self, items: list[str]):
        self.rep.warn(items)

    def show(self, info: dict, png: bytes | None = None) -> None:
        self.rep.show(info, png)


def _basic(resolve, cfg: Config, rep: Reporter, timelines_to_correct: list, base: Path, enabled: bool) -> None:
    """Basic Correction on the given timelines, if the project has it switched on."""
    if not enabled or not timelines_to_correct:
        rep.finish("basic", "switched off for this project" if not enabled else "no new clips", state="skipped")
        return
    from .basic import run as basic  # noqa: PLC0415 - needs numpy, only loaded when used
    rep.start("basic")
    sub = _StepReporter(rep, "basic", dict(basic.STEPS))
    written = 0
    for tl in timelines_to_correct:
        record = basic.basic_correction(resolve, cfg, sub, timeline_name=tl.GetName(), base=base)
        written += sum(1 for e in record["items"] if e["outcome"].get("written"))
    rep.finish("basic", f"{written} clips got a {'DAVIGEN_AUTO'} version – Color page → right-click a clip → "
               "Local Versions to compare")


def basic_enabled(base: Path) -> bool:
    return bool(filesystem.read_project_info(base).get("basic_correction"))


def _save(resolve, rep: Reporter, detail: str = "", timeline=None) -> None:
    rep.start("save")
    proj = resolve.GetProjectManager().GetCurrentProject()
    if timeline is not None:
        proj.SetCurrentTimeline(timeline)
    resolve.GetProjectManager().SaveProject()
    # re-open the page so Resolve's UI shows the final state (scratch timelines were deleted)
    resolve.OpenPage("media")
    resolve.OpenPage("edit")
    rep.finish("save", detail)


def project_groups(cfg: Config, proj) -> dict[str, dict]:
    """Group specs of the open project, reconstructed from the davigen metadata on its clips."""
    specs: dict[str, dict] = {}
    for c in media_pool.davigen_clips(proj.GetMediaPool().GetRootFolder()):
        name, profile = c.GetThirdPartyMetadata(media_pool.META_GROUP), c.GetThirdPartyMetadata(media_pool.META_PROFILE)
        if not name or profile not in cfg.profiles or name in specs:
            continue
        key = c.GetThirdPartyMetadata(media_pool.META_CAMERA) or ""
        cam = next((x for x in cfg.cameras if x.key == key), None)
        cam_name = c.GetThirdPartyMetadata(media_pool.META_CAMERA_NAME) or (cam.name if cam else key)
        specs[name] = {"group_name": name, "profile": profile, "camera_key": key, "camera_name": cam_name}
    return specs


def _all_timelines(proj) -> list:
    return [proj.GetTimelineByIndex(i) for i in range(1, proj.GetTimelineCount() + 1)]


def _any_clip(proj):
    clips = media_pool.davigen_clips(proj.GetMediaPool().GetRootFolder())
    return clips[0] if clips else None


# ============================================================================== flows

def refresh_color(resolve, cfg: Config, rep: Reporter) -> None:
    """(Re)build LUTs for every group used by clips in the project, e.g. after adding a LUT file."""
    proj = resolve.GetProjectManager().GetCurrentProject()
    specs = project_groups(cfg, proj)
    if not specs:
        raise ResolveError("No davigen clips in the open project")
    _color(resolve, proj, cfg, list(specs.values()), _any_clip(proj), rep)
    _assign(proj, rep, _all_timelines(proj))
    _save(resolve, rep, proj.GetName())


def assign_all(resolve, cfg: Config, rep: Reporter) -> None:
    proj = resolve.GetProjectManager().GetCurrentProject()
    touched = _assign(proj, rep, _all_timelines(proj))
    try:
        base = project_base(proj)
    except ResolveError:
        base = None
    _basic(resolve, cfg, rep, touched, base, bool(base and basic_enabled(base)))
    _save(resolve, rep, proj.GetName())


def queue_renders(resolve, cfg: Config, rep: Reporter) -> None:
    proj = resolve.GetProjectManager().GetCurrentProject()
    base = project_base(proj)
    rep.start("deliver")
    jobs, warns = deliver.queue_all(proj, cfg, project_format(cfg, proj, base), base)
    rep.warn(warns)
    rep.finish("deliver", f"{len(jobs)} render jobs queued – Deliver page → Render All")


def reveal(path: str) -> None:
    """Show a folder in Finder."""
    if os.path.exists(path):
        subprocess.run(["open", path], check=False)
