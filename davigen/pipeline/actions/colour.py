"""Camera clips: onto the assembly timeline, into their colour group with input and output transform and the node
structure, and – when chosen – a Basic correction (the existing one, unchanged, docs/concepts/BASIC_CORRECTION.md)."""

from __future__ import annotations

import re
from functools import cached_property

from ... import color, filesystem, formats
from ...config import Config
from ...resolve_api import ResolveError, capabilities, find_timeline
from ..core import DONE, NA, TODO, Action, ActionError, Check, Input, Option, register
from .intake import item_of

CAMERA = frozenset({"camera"})


def assembly(ctx):
    """The assembly timeline of the open project (every camera clip is on it)."""
    def build():
        spec = next((t for t in formats.timelines(ctx.cfg, ctx.fmt) if t.get("fill_with_footage")), None)
        return find_timeline(ctx.project, spec["name"]) if spec else None
    return ctx.cached("assembly", build)


def assembly_items(ctx) -> dict[str, object]:
    """Timeline items of the assembly by the file path of their clip."""
    def build():
        out = {}
        tl = assembly(ctx)
        if tl is None:
            return None
        for idx in range(1, (tl.GetTrackCount("video") or 0) + 1):
            for ti in tl.GetItemListInTrack("video", idx) or []:
                mpi = ti.GetMediaPoolItem()
                path = mpi.GetClipProperty("File Path") if mpi else ""
                if path and path not in out:
                    out[path] = ti
        return out
    found = ctx.cached("assembly_items", build)
    return found if found is not None else {}


def _forget_timeline(ctx) -> None:
    ctx.forget("assembly", "assembly_items")


def _on_assembly(ctx, asset):
    """The asset's assembly item, or a Check saying why there is none."""
    if not asset.media_id and item_of(ctx, asset) is None:
        return None, Check(TODO, "not imported yet")
    ti = assembly_items(ctx).get(asset.resolve_path)
    if ti is None:
        return None, Check(NA, "not on the assembly timeline")
    return ti, None


# --------------------------------------------------------------------------------------------- assembly

@register
class Assembly(Action):
    id, label, kinds = "assembly", "Onto the assembly timeline", CAMERA
    about = "New camera clips at the end of the assembly timeline, in the order they were recorded."
    after = ("import_media",)
    batch = True
    seconds_per_unit = 0.1

    def check(self, ctx, asset):
        if not asset.media_id and item_of(ctx, asset) is None:
            return Check(TODO, "not imported yet")
        return Check(DONE if asset.resolve_path in assembly_items(ctx) else TODO)

    def prepare(self, ctx, assets, values):
        if assembly(ctx) is None:
            raise ActionError("The assembly timeline isn't in this project anymore – davigen adds new clips there")

    def run_batch(self, ctx, assets, values, progress):
        tl = assembly(ctx)
        pairs = sorted(((a.created or "") + a.name, a, item_of(ctx, a)) for a in assets)
        items = [i for _, _, i in pairs if i is not None]
        ctx.project.SetCurrentTimeline(tl)
        progress(fraction=0.2, detail=f"{len(items)} clips")
        if items and not ctx.project.GetMediaPool().AppendToTimeline(items):
            raise ActionError("Resolve didn't put the clips on the assembly timeline")
        _forget_timeline(ctx)
        on = assembly_items(ctx)
        return {a.id: "not on the timeline after appending" for _, a, _ in pairs if a.resolve_path not in on}

    def finish(self, ctx, assets, values):
        tl = assembly(ctx)
        return f"{len(assets)} clips appended to {tl.GetName() if tl else 'the assembly'}"


# ----------------------------------------------------------------------------------------------- colour

@register
class Colour(Action):
    id, label, kinds = "colour", "Colour groups + node structure", CAMERA
    about = ("One colour group per camera and log profile, with the input transform (log → DaVinci Wide Gamut) and "
             "the output transform (→ Rec.709); every new clip in its group with the standard node structure.")
    after = ("assembly",)
    batch = True
    seconds_per_unit = 0.2

    def check(self, ctx, asset):
        ti, why = _on_assembly(ctx, asset)
        if ti is None:
            return why
        group = ti.GetColorGroup()
        return Check(DONE if group is not None and group.GetName() == asset.group else TODO)

    def estimate(self, ctx, assets):
        existing = {g.GetName() for g in ctx.project.GetColorGroupsList() or []} if ctx.project else set()
        new_groups = {a.group for a in assets} - existing
        return 6.0 * len(new_groups) + self.seconds_per_unit * len(assets)

    def run_batch(self, ctx, assets, values, progress):
        proj = ctx.project
        if not capabilities(ctx.resolve, proj)["color_groups"]:
            ctx.warn("MANUAL: Create the color groups by hand (Color page → right-click clip → Groups)")
            return {a.id: "no colour group scripting in this Resolve" for a in assets}
        existing = {g.GetName(): g for g in proj.GetColorGroupsList() or []}
        specs = {}
        for a in assets:
            g = existing.get(a.group)
            has_lut = bool(g is not None and hasattr(g, "GetPreClipNodeGraph") and g.GetPreClipNodeGraph().GetLUT(1))
            if not has_lut and a.profile in ctx.cfg.profiles:
                specs[a.group] = {"group_name": a.group, "profile": a.profile, "camera_key": a.camera_key,
                                  "camera_name": a.camera_name}
        if specs:
            scratch = item_of(ctx, assets[0])
            _, warns = color.setup_groups(ctx.resolve, proj, ctx.cfg, list(specs.values()), scratch,
                                          progress=lambda d: progress(fraction=0.3, detail=d))
            ctx.warn(*warns)
            _forget_timeline(ctx)                        # the scratch timeline was deleted: refetch
        tl = assembly(ctx)
        progress(fraction=0.8, detail="groups and nodes on the assembly")
        color.assign(proj, tl)
        applied, _ = color.apply_clip_structure(tl)
        ctx.shared["structured"] = ctx.shared.get("structured", 0) + applied
        _forget_timeline(ctx)
        _update_groups(ctx)
        on = assembly_items(ctx)
        failed = {}
        for a in assets:
            ti = on.get(a.resolve_path)
            g = ti.GetColorGroup() if ti is not None else None
            if g is None or g.GetName() != a.group:
                failed[a.id] = "not in its colour group"
        return failed

    def finish(self, ctx, assets, values):
        groups = sorted({a.group for a in assets})
        structured = ctx.shared.get("structured", 0)
        return ", ".join(groups) + (f" · {structured} clips got the node structure" if structured else "")


def _update_groups(ctx) -> None:
    """davigen.json lists the colour groups with their camera, profile and clip count (for the overview)."""
    info = filesystem.read_project_info(ctx.base)
    counts: dict[str, dict] = {}
    for a in ctx.store:
        if a.kind == "camera" and not a.removed and a.group:
            entry = counts.setdefault(a.group, {"group": a.group, "camera": a.camera_name, "profile": a.profile,
                                                "clips": 0})
            entry["clips"] += 1
    info["groups"] = sorted(counts.values(), key=lambda g: -g["clips"])
    info.pop("created", None)
    created = filesystem.read_project_info(ctx.base).get("created")
    target = filesystem.write_project_info(ctx.base, info)
    if created:                                          # keep the date the project was made
        import json  # noqa: PLC0415
        data = json.loads(target.read_text(encoding="utf-8"))
        data["created"] = created
        target.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# ------------------------------------------------------------------------------------- basic correction

class _Sub:
    """Basic correction reports into Reporter-like calls; this turns them into the step's progress."""

    def __init__(self, run_step_progress, run, labels: dict[str, str]):
        self.progress, self.run, self.labels = run_step_progress, run, labels
        self.order = list(labels)
        self.result: dict = {}

    def _at(self, sub: str, detail: str) -> None:
        n = self.order.index(sub) if sub in self.order else 0
        inner = re.match(r"\s*(\d+)\s*/\s*(\d+)", detail or "")      # "12/57 · P1000070"
        part = int(inner.group(1)) / max(1, int(inner.group(2))) if inner else 0.0
        self.progress(fraction=(n + min(part, 1.0)) / len(self.order), detail=f"{self.labels.get(sub, sub)}"
                      + (f" · {detail}" if detail else ""))

    def start(self, sub: str, detail: str = ""):
        self._at(sub, detail)

    detail = start

    def finish(self, sub: str, detail: str = "", state: str = "done"):
        self._at(sub, detail)

    def warn(self, items):
        if self.run is not None:
            self.run.ctx.warn(*items)

    def show(self, info: dict, png: bytes | None = None) -> None:
        if self.run is not None:
            self.run.show(info, png)


@register
class BasicCorrection(Action):
    id, label, kinds, mandatory = "basic_correction", "Basic correction", CAMERA, False
    about = ("Exposure, white balance, contrast and saturation of every clip to one look, with keyframes where the "
             "light changes – in its own version DAVIGEN_AUTO, so your grade stays untouched.")
    after = ("colour",)
    batch = True
    form = "basic_look"
    seconds_per_unit = 4.0

    @cached_property
    def _looks(self) -> dict:
        from ...basic import settings as basic_settings  # noqa: PLC0415
        s = basic_settings.load(Config().workflow)
        return {"defaults": s["look"], "looks": s["looks"], "dims": basic_settings.LOOK_DIMENSIONS}

    @property
    def inputs(self):
        looks, labels = self._looks, {"brightness": "Brightness", "contrast": "Contrast", "warmth": "Warmth",
                                      "saturation": "Saturation"}
        return tuple(Input(dim, labels[dim], "choice",
                           [Option(k, v["label"], v.get("about", "")) for k, v in looks["looks"][dim].items()],
                           default=looks["defaults"][dim]) for dim in looks["dims"])

    def defaults(self, ctx):
        from ...basic import write  # noqa: PLC0415
        saved = write.load_look(ctx.base) if ctx.base else {}
        return {i.id: saved.get(i.id, i.default) for i in self.inputs}

    def check(self, ctx, asset):
        from ...basic import write  # noqa: PLC0415
        ti, why = _on_assembly(ctx, asset)
        if ti is None:
            return why
        return Check(DONE if write.AUTO in (ti.GetVersionNameList(0) or []) else TODO)

    def run_batch(self, ctx, assets, values, progress):
        from ...basic import carry, run as basic, write  # noqa: PLC0415
        write.save_look(ctx.base, {**write.load_look(ctx.base), **{k: v for k, v in values.items() if v}})
        tl = assembly(ctx)
        if tl is None:
            raise ActionError("Basic correction runs on the assembly timeline, which isn't in this project anymore")
        sub = _Sub(progress, ctx.shared.get("run"), dict(basic.STEPS))
        paths = {a.resolve_path for a in assets}
        record = basic.basic_correction(ctx.resolve, ctx.cfg, sub, timeline_name=tl.GetName(), base=ctx.base,
                                        only_paths=paths)
        by_path = {e.get("path"): e for e in record.get("items", [])}
        failed = {}
        for a in assets:
            outcome = (by_path.get(a.resolve_path) or {}).get("outcome") or {}
            if not outcome.get("written"):
                failed[a.id] = outcome.get("skipped") or "not measured"
        # the same grade wherever these clips are used (rough cuts, your edits, delivery timelines)
        proj = ctx.project
        current = proj.GetCurrentTimeline()
        progress(fraction=0.95, detail="the same on every other timeline")
        counts = carry.carry_over(proj, ctx.base, [proj.GetTimelineByIndex(i)
                                                   for i in range(1, proj.GetTimelineCount() + 1)])
        ctx.warn(*counts["warnings"][:10])
        if current is not None:
            proj.SetCurrentTimeline(current)
        _forget_timeline(ctx)
        ctx.shared["basic_written"] = len(assets) - len(failed)
        ctx.shared["basic_carried"] = counts["written"]
        return failed

    def finish(self, ctx, assets, values):
        from ...basic import write  # noqa: PLC0415
        n, carried = ctx.shared.get("basic_written", 0), ctx.shared.get("basic_carried", 0)
        return (f"{n} clips got {write.AUTO}" + (f", on {carried} more timeline clips too" if carried else "")
                + " – Color page → right-click a clip → Local Versions to compare")


__all__ = ["Assembly", "Colour", "BasicCorrection", "ResolveError"]
