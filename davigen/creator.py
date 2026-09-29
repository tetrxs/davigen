"""The flows the UI can start: create a project, and maintain the open project.

Every flow reports into a Reporter (polled by the UI via /api/progress). Footage transfers are
journaled (transfer.py): if a flow fails, every moved/copied file is put back automatically.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import color, deliver, filesystem, formats, media_pool, naming, project as project_mod, timelines, transfer
from .config import Config
from .formats import Format
from .resolve_api import ResolveError, capabilities, ensure_bin, find_timeline, is_studio
from .scanner import CameraGroup, ClipInfo

NEW_PROJECT_STEPS = [
    ("folders", "Project folder"),
    ("transfer", "Footage into the project"),
    ("project", "Resolve project + settings"),
    ("bins", "Media Pool bins"),
    ("import", "Import + sort clips"),
    ("timelines", "Timelines + tracks"),
    ("color", "Color groups + LUTs"),
    ("assign", "Groups + node structure"),
    ("basic", "Basic correction"),
    ("deliver", "Render presets"),
    ("save", "Save"),
]
ADD_FOOTAGE_STEPS = [
    ("folders", "Camera folders"),
    ("transfer", "Footage into the project"),
    ("bins", "Camera bins"),
    ("import", "Import + sort clips"),
    ("timelines", "Append to assembly timeline"),
    ("color", "Color groups + LUTs"),
    ("assign", "Groups + node structure"),
    ("save", "Save"),
]
COLOR_STEPS = [("color", "Rebuild color groups + LUTs"), ("assign", "Groups + node structure"), ("save", "Save")]
ASSIGN_STEPS = [("assign", "Groups + node structure on all timelines"), ("basic", "Basic correction"), ("save", "Save")]
QUEUE_STEPS = [("deliver", "Add render jobs")]

TRANSFER_LABEL = {transfer.MOVE: "moved", transfer.COPY: "copied", transfer.LEAVE: "left in place"}


@dataclass
class GroupPlan:
    camera_key: str
    camera_name: str
    profile: str
    clips: list[ClipInfo] = field(default_factory=list)
    make: str = ""
    model: str = ""

    def group_name(self, cfg: Config) -> str:
        return naming.group_name(self.camera_key, cfg.profiles[self.profile].short)

    def as_camera_group(self) -> CameraGroup:
        return CameraGroup(self.camera_key, self.camera_name, self.make, self.model, self.profile, "", self.clips)


@dataclass
class Plan:
    project: str
    root: str
    groups: list[GroupPlan]
    fmt: Format | None = None
    transfer: str = transfer.MOVE
    basic_correction: bool = False


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


def _destination(target_dir: Path, clip: ClipInfo) -> Path:
    """<camera folder>/<YYYY-MM-DD>_<SOURCE FOLDER>/<original file name>; never overwrites."""
    day = (clip.created or "")[:10] or "UNDATED"
    source = naming.normalize(Path(clip.path).parent.name) or "SOURCE"
    folder = target_dir / f"{day}_{source}"
    n = 2
    while (folder / Path(clip.path).name).exists():
        folder = target_dir / f"{day}_{source}_{n}"
        n += 1
    return folder / Path(clip.path).name


def _transfer(cfg: Config, base: Path, plan: Plan, rep: Reporter, skip: set[str]) -> dict[int, list[str]]:
    """Move / copy / leave the footage. Returns the clip paths to import, per group."""
    paths_by_group: dict[int, list[str]] = {}
    todo = [(gi, c) for gi, g in enumerate(plan.groups) for c in g.clips if c.path not in skip]
    if plan.transfer == transfer.LEAVE or not todo:
        for gi, g in enumerate(plan.groups):
            paths_by_group[gi] = [c.path for c in g.clips]
        rep.finish("transfer", "footage stays where it is" if todo else "no new footage", state="skipped")
        return paths_by_group

    rep.start("transfer")
    files = [Path(c.path) for _, c in todo]
    need = transfer.space_needed(files, base, plan.transfer)
    free = shutil.disk_usage(base).free
    if need > free * 0.98:
        raise ResolveError(f"Not enough space in {base}: {need / 1e9:.1f} GB needed, {free / 1e9:.1f} GB free")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    journal = transfer.Journal(base / "00_ADMIN" / "PROJECT_INFO" / f"transfer_{stamp}.json", plan.transfer)
    rep.journal = journal
    journal.begin()
    for gi, g in enumerate(plan.groups):
        target = filesystem.media_folder_for(cfg, base, g.camera_key)
        paths_by_group[gi] = []
        for i, clip in enumerate(g.clips, 1):
            if clip.path in skip:
                paths_by_group[gi].append(clip.path)
                continue
            rep.detail("transfer", f"{g.camera_name}: {i}/{len(g.clips)} · {Path(clip.path).name}")
            dst = journal.transfer(Path(clip.path), _destination(target, clip))
            paths_by_group[gi].append(str(dst))
    how = TRANSFER_LABEL[plan.transfer]
    rep.finish("transfer", f"{len(todo)} files {how} into 01_MEDIA ({need / 1e9:.1f} GB checked by checksum)"
               if need else f"{len(todo)} files {how} into 01_MEDIA (same drive – instant)")
    return paths_by_group


def _camera_bins(cfg: Config, base: Path, plan: Plan) -> dict[str, str]:
    return {g.camera_key: filesystem.media_folder_for(cfg, base, g.camera_key).name for g in plan.groups}


def _import(proj, cfg: Config, plan: Plan, paths_by_group: dict[int, list[str]], camera_bins: dict[str, str],
            rep: Reporter, existing: set[str]) -> list:
    """Import every group into its camera bin. Returns MediaPoolItems sorted by recording time."""
    rep.start("import")
    mp = proj.GetMediaPool()
    footage = cfg.workflow["bins"]["footage"]
    keys = list(camera_bins)
    ordered: list[tuple[str, object]] = []
    for gi, g in enumerate(plan.groups):
        pairs = [(c, p) for c, p in zip(g.clips, paths_by_group.get(gi, [])) if p not in existing]
        skipped = len(paths_by_group.get(gi, [])) - len(pairs)
        if skipped:
            rep.warn([f"{g.camera_name}: {skipped} clips are already in the project – skipped"])
        if not pairs:
            continue
        clips, paths = [c for c, _ in pairs], [p for _, p in pairs]
        rep.detail("import", f"{g.camera_name}: {len(paths)} clips")
        folder = ensure_bin(mp, f"{footage}/{camera_bins[g.camera_key]}")
        clip_color = media_pool.CLIP_COLORS[keys.index(g.camera_key) % len(media_pool.CLIP_COLORS)]
        items = media_pool.import_group(mp, folder, g.as_camera_group(), paths, clip_color,
                                        cfg.profiles[g.profile].label, g.group_name(cfg))
        lost = media_pool.missing(paths, items)
        if lost:
            raise ResolveError(f"Resolve couldn't import {len(lost)} clips of {g.camera_name}: "
                               + ", ".join(Path(p).name for p in lost[:5]))
        by_path = {i.GetClipProperty("File Path"): i for i in items}
        for clip, path in zip(clips, paths):
            if path in by_path:
                ordered.append(((clip.created or "") + clip.name, by_path[path]))
    items = [item for _, item in sorted(ordered, key=lambda t: t[0])]
    rep.finish("import", f"{len(items)} clips in {len(set(camera_bins.values()))} camera bins")
    return items


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


def _specs(cfg: Config, groups: list[GroupPlan]) -> list[dict]:
    return list({g.group_name(cfg): {"group_name": g.group_name(cfg), "profile": g.profile,
                                     "camera_key": g.camera_key, "camera_name": g.camera_name}
                 for g in groups}.values())


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


def _assembly(proj, cfg: Config, fmt: Format):
    spec = next((t for t in formats.timelines(cfg, fmt) if t.get("fill_with_footage")), None)
    return find_timeline(proj, spec["name"]) if spec else None


def _any_clip(proj):
    clips = media_pool.davigen_clips(proj.GetMediaPool().GetRootFolder())
    return clips[0] if clips else None


# ============================================================================== flows

def new_project(resolve, cfg: Config, plan: Plan, rep: Reporter) -> None:
    if not plan.project:
        raise ResolveError("Project name missing")
    base = filesystem.project_dir(plan.root, plan.project)
    if (base / "00_ADMIN" / "PROJECT_INFO" / "davigen.json").exists():
        raise ResolveError(f"{base} already contains a davigen project")
    studio = is_studio(resolve)
    fmt = plan.fmt or formats.default_format(cfg, studio)
    if not studio and not formats.fits_free(cfg, fmt.width, fmt.height):
        fmt.width, fmt.height = formats.clamp_free(cfg, fmt.width, fmt.height)

    rep.start("folders", str(base))
    filesystem.create_tree(cfg, base, list(dict.fromkeys(g.camera_key for g in plan.groups)),
                           [d["folder"] for d in formats.deliveries(cfg, fmt, plan.project)])
    rep.finish("folders", str(base))

    paths_by_group = _transfer(cfg, base, plan, rep, skip=set())

    rep.start("project", plan.project)
    proj, warns = project_mod.create(resolve, cfg, plan.project, base, fmt)
    rep.warn(warns)
    w, h = proj.GetSetting("timelineResolutionWidth"), proj.GetSetting("timelineResolutionHeight")
    rep.finish("project", f"{w}×{h} · {fmt.resolve_fps} fps · DaVinci YRGB · DaVinci Wide Gamut / Intermediate")

    rep.start("bins")
    camera_bins = _camera_bins(cfg, base, plan)
    media_pool.build_bins(proj.GetMediaPool(), cfg, list(dict.fromkeys(camera_bins.values())))
    rep.finish("bins")

    items = _import(proj, cfg, plan, paths_by_group, camera_bins, rep, existing=set())

    rep.start("timelines")
    created, warns = timelines.create_all(proj, cfg, fmt, items)
    rep.warn(warns)
    rep.finish("timelines", f"{len(created)} timelines")

    _color(resolve, proj, cfg, _specs(cfg, plan.groups), items[0] if items else None, rep)
    assembly = _assembly(proj, cfg, fmt)
    touched = _assign(proj, rep, [assembly] if assembly else [])
    try:
        _basic(resolve, cfg, rep, touched, base, plan.basic_correction)
    except Exception as e:  # noqa: BLE001 - the project itself is fine; Basic Correction can run again later
        rep.finish("basic", f"stopped: {e}", state="error")
        rep.warn([f"Basic correction stopped: {e}. The project is complete – run it again from the home screen."])

    rep.start("deliver")
    rep.warn(deliver.ensure_presets(proj, cfg, fmt, studio))
    rep.finish("deliver", " · ".join(d["label"].split(" · ")[0] for d in formats.deliveries(cfg, fmt, plan.project)))

    filesystem.write_project_info(base, {
        "project": plan.project, "resolve": resolve.GetVersionString(), "studio": studio, "format": fmt.as_dict(),
        "transfer": plan.transfer, "basic_correction": plan.basic_correction,
        "groups": [{"group": g.group_name(cfg), "camera": g.camera_name, "profile": g.profile,
                    "clips": len(g.clips)} for g in plan.groups],
    })
    filesystem.register_project(plan.project, base, project_mod.PM_FOLDER)
    _save(resolve, rep, str(base), assembly)
    rep.warn([
        "MANUAL: Color page → Group Post-Clip: add your look nodes BEFORE the output node "
        "(select the output node → Color → Nodes → Add Serial Before Current)",
        f"MANUAL: Project Settings → Master Settings → Working Folders: proxy location {base / '03_WORK' / 'PROXIES'}, "
        f"cache location {base / '03_WORK' / 'CACHE'} (Resolve doesn't keep these when a script sets them)",
        "MANUAL: Proxies: Media Pool → select clips → right-click → Generate Proxy Media",
        "MANUAL: Built new timelines (edit, master, deliveries)? run 'Assign groups & nodes' in davigen – Resolve keeps "
        "color groups and grades per timeline clip, not per Media Pool clip",
    ])
    rep.result = {"project": plan.project, "folder": str(base)}


def add_footage(resolve, cfg: Config, plan: Plan, rep: Reporter) -> None:
    proj = resolve.GetProjectManager().GetCurrentProject()
    base = project_base(proj)
    fmt = project_format(cfg, proj, base)
    existing = media_pool.all_clip_paths(proj.GetMediaPool().GetRootFolder())

    rep.start("folders")
    filesystem.create_tree(cfg, base, list(dict.fromkeys(g.camera_key for g in plan.groups)))
    rep.finish("folders", str(base / cfg.workflow["folders"]["media_root"]))

    paths_by_group = _transfer(cfg, base, plan, rep, skip=existing)

    rep.start("bins")
    camera_bins = _camera_bins(cfg, base, plan)
    media_pool.build_bins(proj.GetMediaPool(), cfg, list(dict.fromkeys(camera_bins.values())))
    rep.finish("bins")

    items = _import(proj, cfg, plan, paths_by_group, camera_bins, rep, existing)

    rep.start("timelines")
    assembly = _assembly(proj, cfg, fmt)
    if not items:
        rep.finish("timelines", "no new clips", state="skipped")
    elif assembly is None:
        rep.finish("timelines", "no assembly timeline found", state="skipped")
    else:
        proj.SetCurrentTimeline(assembly)
        proj.GetMediaPool().AppendToTimeline(items)
        rep.finish("timelines", f"{len(items)} clips appended to {assembly.GetName()}")

    _color(resolve, proj, cfg, _specs(cfg, plan.groups), items[0] if items else _any_clip(proj), rep)
    _assign(proj, rep, [assembly] if assembly else [])
    _save(resolve, rep, proj.GetName(), assembly)


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
