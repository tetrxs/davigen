"""Camera colour groups: input LUT in Group Pre-Clip, output LUT in Group Post-Clip.

Resolve's API can only place LUTs into group graphs, so every input transform becomes a 65-point
LUT in Resolve's LUT folder (davigen/). Where it comes from is decided in transforms.py:
Resolve's own CST (baked via a scratch clip), a published formula (computed, no clip needed), or
the manufacturer's official LUT (downloaded once, combined with Resolve's Rec.709 → DWG).
LUTs are cached and reused by every later project.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from . import colormath, drx, transforms
from .config import DATA_DIR, LUT_DIR, TEMPLATE_DIR, Config, Output, Profile
from .lut import Lut3D, compose
from .media_pool import META_GROUP
from .resolve_api import ResolveError, color_group, find_timeline

CST_TEMPLATE = TEMPLATE_DIR / "drx" / "CST_BASE.drx"
CLIP_TEMPLATE = TEMPLATE_DIR / "drx" / "CLIP_STRUCTURE.drx"    # 01_EXPOSURE … 06_FINISH, built once in Resolve
PORTABLE_LUTS = DATA_DIR / "luts"       # copy of every generated LUT, travels with the davigen folder
BAKE_TIMELINE = "ZZ_DAVIGEN_BAKE"


def input_lut_name(profile: Profile, camera_key: str = "") -> str:
    # vendor LUTs differ per camera model (e.g. every DJI drone has its own D-Log M LUT)
    suffix = f"_{camera_key}" if profile.vendor and camera_key else ""
    return f"DAVIGEN_IN_{profile.id}{suffix}_TO_DWG"


def output_lut_name(output: Output) -> str:
    return f"DAVIGEN_OUT_DWG_TO_{output.id}"


def lut_rel(name: str) -> str:
    return f"{LUT_DIR.name}/{name}.cube"


def lut_path(name: str) -> Path:
    return LUT_DIR / f"{name}.cube"


def sync_luts() -> None:
    """Keep Resolve's LUT folder and the portable copy in the davigen folder in step (missing files only)."""
    for src_dir, dst_dir in ((PORTABLE_LUTS, LUT_DIR), (LUT_DIR, PORTABLE_LUTS)):
        if not src_dir.exists():
            continue
        dst_dir.mkdir(parents=True, exist_ok=True)
        for f in src_dir.glob("DAVIGEN_*.cube"):
            if not (dst_dir / f.name).exists():
                shutil.copy2(f, dst_dir / f.name)


def _cst_values(src_space: str, src_gamma: str, dst_space: str, dst_gamma: str,
                tone_mapping: str) -> dict[str, str | None]:
    return {
        "inputColorSpace": src_space, "inputGamma": src_gamma,
        "outputColorSpace": dst_space, "outputGamma": dst_gamma,
        # absent tmType = Resolve default (DaVinci tone mapping)
        "tmType": None if tone_mapping == "default" else "TM_NONE",
    }


class Baker:
    """Produces the LUT files. Resolve CSTs are baked on one scratch clip (only if needed)."""

    def __init__(self, resolve, project, cfg: Config, scratch_clip):
        self.resolve, self.project, self.cfg, self.clip = resolve, project, cfg, scratch_clip
        self._timeline = None
        self._tmp = Path(tempfile.mkdtemp(prefix="davigen_drx_"))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self._timeline is not None:
            # by name: deleting a timeline can invalidate the other timeline objects (seen in Resolve 21)
            current = self.project.GetCurrentTimeline()
            name = current.GetName() if current is not None else ""
            self.project.GetMediaPool().DeleteTimelines([self._timeline])
            previous = find_timeline(self.project, name) if name and name != BAKE_TIMELINE else None
            if previous is not None:
                self.project.SetCurrentTimeline(previous)
        return False

    def _item(self):
        if self.clip is None:
            raise ResolveError("Baking a Resolve CST needs at least one clip in the project")
        if self._timeline is None:
            previous = self.project.GetCurrentTimeline()
            self._timeline = self.project.GetMediaPool().CreateTimelineFromClips(BAKE_TIMELINE, [self.clip])
            if self._timeline is None:
                raise ResolveError("Couldn't create the scratch timeline for baking LUTs")
            if previous is not None:
                self.project.SetCurrentTimeline(previous)
        items = self._timeline.GetItemListInTrack("video", 1)
        if not items:
            raise ResolveError("The scratch timeline is empty")
        return items[0]

    def bake_cst(self, name: str, values: dict[str, str | None]) -> Path:
        target = lut_path(name)
        if target.exists():
            return target
        LUT_DIR.mkdir(parents=True, exist_ok=True)
        drx_file = drx.write_cst_drx(CST_TEMPLATE, self._tmp / f"{name}.drx", values)
        item = self._item()
        graph = item.GetNodeGraph()
        graph.ResetAllGrades()
        if not graph.ApplyGradeFromDRX(str(drx_file), 0):
            raise ResolveError(f"Resolve didn't accept the CST for {name}")
        if not item.ExportLUT(self.resolve.EXPORT_LUT_65PTCUBE, str(target)):
            raise ResolveError(f"Resolve couldn't export the LUT {name}")
        graph.ResetAllGrades()
        return target

    def input_lut(self, profile: Profile, camera_key: str = "",
                  camera_name: str = "") -> tuple[Path | None, transforms.Source]:
        source = transforms.source_for(self.cfg, profile, camera_key, camera_name)
        name = input_lut_name(profile, camera_key)
        target = lut_path(name)
        w = self.cfg.working
        if source.kind == transforms.CST:
            try:
                return self.bake_cst(name, _cst_values(profile.color_space, profile.gamma, w["color_space"],
                                                       w["gamma"], profile.tone_mapping)), source
            except ResolveError:
                # this Resolve version won't take the CST template – use the published formula instead
                if not (profile.math_curve and colormath.available()):
                    raise
                source = transforms.Source(transforms.MATH, f"{profile.math_curve} · {profile.math_gamut} "
                                                            "(fallback: Resolve didn't accept the CST)")
        if source.kind == transforms.MATH:
            if not target.exists():
                colormath.input_lut(profile.math_curve, profile.math_gamut, target, title=f"davigen {profile.label}")
            return target, source
        if source.kind in (transforms.VENDOR, transforms.MANUAL) and not source.needs_online:
            if target.exists():
                return target, source
            vendor = source.path or transforms.fetch_vendor_lut(self.cfg, profile, camera_key, camera_name)
            lut = self._from_display_lut(vendor, target, profile)
            return lut, transforms.source_for(self.cfg, profile, camera_key, camera_name)
        return None, source

    def output_lut(self, output: Output) -> tuple[Path, str]:
        """Returns (LUT, note). Falls back to a computed conversion if Resolve won't bake the CST."""
        w = self.cfg.working
        name = output_lut_name(output)
        try:
            return self.bake_cst(name, _cst_values(w["color_space"], w["gamma"], output.color_space,
                                                   output.gamma, output.tone_mapping)), ""
        except ResolveError:
            if not colormath.available():
                raise
            target = lut_path(name + "_COMPUTED")
            if not target.exists():
                colormath.output_lut(target, title=f"davigen {output.label} (computed)")
            return target, "output LUT computed by davigen (Resolve didn't accept the CST template)"

    def _from_display_lut(self, vendor: Path, target: Path, profile: Profile) -> Path:
        """Manufacturer LUT (log → Rec.709) followed by Resolve's Rec.709 → DWG/Intermediate (inverse tone
        map), so the group output (DWG → Rec.709, DaVinci tone mapping) reproduces the manufacturer's look."""
        back_to_dwg, _ = self.input_lut(self.cfg.profiles["REC709"])
        LUT_DIR.mkdir(parents=True, exist_ok=True)
        return compose(Lut3D.read(vendor), Lut3D.read(back_to_dwg)).write(target, title=f"davigen {profile.label} → DWG")


def setup_groups(resolve, project, cfg: Config, groups: list[dict], scratch_clip,
                 progress=None) -> tuple[dict[str, object], list[str]]:
    """Create one colour group per camera+profile with input/output LUTs.

    groups: [{"group_name", "profile", "camera_key", "camera_name"}]. Returns ({name: ColorGroup}, warnings).
    """
    warnings: list[str] = []
    out_profile = cfg.outputs[cfg.workflow["color"]["output_profile"]]
    luts: dict[str, Path | None] = {}
    sync_luts()      # LUTs baked on another Mac need no clip and no rebake here
    with Baker(resolve, project, cfg, scratch_clip) as baker:
        if progress:
            progress("output LUT")
        out_lut, note = baker.output_lut(out_profile)
        if note:
            warnings.append(note)
        for g in groups:
            profile = cfg.profiles[g["profile"]]
            if progress:
                progress(f"input LUT · {profile.label}")
            try:
                lut, source = baker.input_lut(profile, g.get("camera_key", ""), g.get("camera_name", ""))
            except (LookupError, OSError, ValueError) as e:
                lut, source = None, transforms.Source(transforms.MISSING, str(e))
            luts[g["group_name"]] = lut
            if lut is None:
                warnings.append(f"MANUAL: {g['group_name']}: {profile.label} – {source.detail}")
            elif source.kind != transforms.CST:
                warnings.append(f"{g['group_name']}: input from {source.label} ({source.detail})")
    sync_luts()
    project.RefreshLUTList()

    created = {}
    for g in groups:
        name = g["group_name"]
        group = color_group(project, name)
        if group is None:
            warnings.append(f"Couldn't create the group {name}")
            continue
        created[name] = group
        in_lut = luts.get(name)
        if not hasattr(group, "GetPreClipNodeGraph"):      # Resolve < 19: group graphs aren't scriptable
            where = f"Group Pre-Clip → LUT davigen/{in_lut.name}; " if in_lut is not None else ""
            warnings.append(f"MANUAL: {name}: Color page → {where}Group Post-Clip → LUT davigen/{out_lut.name}")
            continue
        if in_lut is not None and not group.GetPreClipNodeGraph().SetLUT(1, lut_rel(in_lut.stem)):
            warnings.append(f"{name}: couldn't set the input LUT")
        if not group.GetPostClipNodeGraph().SetLUT(1, lut_rel(out_lut.stem)):
            warnings.append(f"{name}: couldn't set the output LUT")
    return created, warnings


def assign(project, timeline) -> tuple[int, int]:
    """Assign every clip on the timeline to the group stored in its davigen metadata.

    Returns (assigned, skipped). Clips already in the right group count as assigned.
    """
    groups = {g.GetName(): g for g in project.GetColorGroupsList() or []}
    assigned = skipped = 0
    for idx in range(1, timeline.GetTrackCount("video") + 1):
        for item in timeline.GetItemListInTrack("video", idx) or []:
            mpi = item.GetMediaPoolItem()
            name = mpi.GetThirdPartyMetadata(META_GROUP) if mpi else ""
            group = groups.get(name)
            current = item.GetColorGroup()
            if group is not None and (current and current.GetName() == name or item.AssignToColorGroup(group)):
                assigned += 1
            else:
                skipped += 1
    return assigned, skipped


def _is_blank(graph) -> bool:
    """A clip grade nobody has touched: one node, no label, no tools, no LUT."""
    if graph is None or graph.GetNumNodes() != 1:
        return False
    return not graph.GetNodeLabel(1) and not graph.GetToolsInNode(1) and not graph.GetLUT(1)


def apply_clip_structure(timeline) -> tuple[int, int]:
    """Give every untouched clip on the timeline the standard node structure. Returns (applied, kept)."""
    applied = kept = 0
    if not CLIP_TEMPLATE.exists():
        return applied, kept
    for idx in range(1, timeline.GetTrackCount("video") + 1):
        for item in timeline.GetItemListInTrack("video", idx) or []:
            graph = item.GetNodeGraph() if hasattr(item, "GetNodeGraph") else None
            if graph is not None and hasattr(graph, "ApplyGradeFromDRX") and _is_blank(graph) and graph.ApplyGradeFromDRX(str(CLIP_TEMPLATE), 0):
                applied += 1
            else:
                kept += 1
    return applied, kept
