"""Project-wide actions: the folder, the Resolve project with every setting, render presets, saving."""

from __future__ import annotations

from ... import deliver, filesystem, formats, media_pool, project as project_mod, template, timelines
from ...resolve_api import ResolveError, find_timeline, is_studio
from ..core import DONE, NA, PROJECT, TODO, Action, ActionError, Check, register


def _new(ctx) -> bool:
    return bool(ctx.spec.get("new"))


@register
class ProjectFolder(Action):
    id, label, scope = "project_folder", "Project folder", PROJECT
    about = "The standard folder tree on disk, with export folders for the chosen deliveries."
    seconds_per_unit = 0.2

    def applies(self, ctx, asset):
        return asset is None and _new(ctx)

    def check(self, ctx, asset):
        return Check(DONE if filesystem.info_file(ctx.base).exists() else TODO)

    def prepare(self, ctx, assets, values):
        if filesystem.info_file(ctx.base).exists():
            raise ActionError(f"{ctx.base} already contains a davigen project")

    def run_one(self, ctx, asset, values, progress):
        progress(detail=str(ctx.base))
        filesystem.create_tree(ctx.cfg, ctx.base, [],
                               [d["folder"] for d in formats.deliveries(ctx.cfg, ctx.fmt, ctx.spec["name"])])
        return None

    def finish(self, ctx, assets, values):
        return str(ctx.base)


@register
class ResolveProject(Action):
    id, label, scope = "resolve_project", "Resolve project + settings", PROJECT
    about = ("From davigen's project template (playback frame rate and proxy folder included), then every other "
             "setting, the Media Pool bins and the timelines with named tracks.")
    after = ("project_folder",)
    seconds_per_unit = 8.0

    def applies(self, ctx, asset):
        return asset is None and _new(ctx)

    def check(self, ctx, asset):
        current = ctx.project
        return Check(DONE if current is not None and current.GetName() == ctx.spec["name"]
                     and filesystem.read_project_info(ctx.base).get("project") == ctx.spec["name"] else TODO)

    def run_one(self, ctx, asset, values, progress):
        cfg, fmt, base, name = ctx.cfg, ctx.fmt, ctx.base, ctx.spec["name"]
        progress(detail="project from the template")
        proj, notes = template.create_project(ctx.resolve, name, project_mod.PM_FOLDER, float(fmt.resolve_fps),
                                              base / project_mod.PROXY_FOLDER)
        ctx.warn(*notes)
        progress(detail="settings")
        ctx.warn(*project_mod.apply_settings(proj, cfg, base, fmt))
        progress(detail="bins")
        media_pool.build_bins(proj.GetMediaPool(), cfg, [])
        progress(detail="timelines")
        created, warns = timelines.create_all(proj, cfg, fmt, [])
        ctx.warn(*warns)
        studio = is_studio(ctx.resolve)
        filesystem.write_project_info(base, {
            "project": name, "resolve": ctx.resolve.GetVersionString(), "studio": studio, "format": fmt.as_dict(),
            "transfer": ctx.settings.get("transfer", ""), "groups": []})
        filesystem.register_project(name, base, project_mod.PM_FOLDER)
        ctx.shared["timelines"] = len(created)
        return None

    def finish(self, ctx, assets, values):
        proj = ctx.project
        w, h = proj.GetSetting("timelineResolutionWidth"), proj.GetSetting("timelineResolutionHeight")
        return (f"{w}×{h} · {ctx.fmt.resolve_fps} fps (playback {proj.GetSetting('timelinePlaybackFrameRate')}) · "
                f"{ctx.shared.get('timelines', 0)} timelines")


@register
class RenderPresets(Action):
    id, label, scope = "render_presets", "Render presets", PROJECT
    about = "One render preset per delivery, at the resolution of its timeline."
    after = ("resolve_project",)
    seconds_per_unit = 2.0

    def applies(self, ctx, asset):
        return asset is None and _new(ctx)

    def check(self, ctx, asset):
        return Check(DONE if ctx.shared.get("presets_done") else TODO)

    def run_one(self, ctx, asset, values, progress):
        ctx.warn(*deliver.ensure_presets(ctx.project, ctx.cfg, ctx.fmt, is_studio(ctx.resolve)))
        ctx.shared["presets_done"] = True
        return None

    def finish(self, ctx, assets, values):
        return " · ".join(d["label"].split(" · ")[0] for d in formats.deliveries(ctx.cfg, ctx.fmt, ctx.spec["name"]))


@register
class Save(Action):
    id, label, scope = "save", "Save", PROJECT
    about = "Save the project in Resolve and show the assembly timeline."
    after = ("project_folder", "resolve_project", "render_presets", "bring_in", "make_importable", "import_media",
             "assembly", "colour", "basic_correction", "song_markers", "collect")
    seconds_per_unit = 1.0

    def check(self, ctx, asset):
        return Check(TODO if ctx.project is not None else NA)

    def run_one(self, ctx, asset, values, progress):
        proj = ctx.project
        if proj is None:
            raise ResolveError("No project is open in Resolve")
        spec = next((t for t in formats.timelines(ctx.cfg, ctx.fmt) if t.get("fill_with_footage")), None) \
            if ctx.fmt else None
        tl = find_timeline(proj, spec["name"]) if spec else None
        if tl is not None:
            proj.SetCurrentTimeline(tl)
        ctx.resolve.GetProjectManager().SaveProject()
        ctx.resolve.OpenPage("media")          # Resolve's UI shows the final state (scratch timelines are gone)
        ctx.resolve.OpenPage("edit")
        return None

    def finish(self, ctx, assets, values):
        return ctx.project.GetName() if ctx.project else ""
