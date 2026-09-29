"""Put the corrections into Resolve without ever touching the user's grade (concept §8, plan step 07).

Every write goes into the local version DAVIGEN_AUTO. AddVersion copies the current grade and makes the copy
active (step 01), so the user's own version keeps everything, and switching versions on the Color page is a
before/after. Nodes are found by label, never by position. Flags become timeline-item markers.

A clip whose light changes (dynamic.py) gets its values as keyframes. SetCDL can't write keyframes, so its grade
in DAVIGEN_AUTO is replaced by a keyframed copy of davigen's six-node structure (drx.make_keyframe_drx), and only
when DAVIGEN_AUTO has exactly that structure (concept §12, §14). Keyframes can't be removed again by a grade
file, so such a DAVIGEN_AUTO is always rebuilt as a fresh copy of the user's version first.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .. import color, drx
from ..media_pool import META_GROUP
from . import correct as c
from . import pipeline as p
from . import dynamic
from .dynamic import Keyframes

AUTO = "DAVIGEN_AUTO"
MARKER_DATA = "davigen-basic"
NOT_IN_GROUP = "not in its colour group"
NO_NODES = "node structure missing"
STRUCTURE = (*c.NODES, "05_SECONDARIES", "06_FINISH")     # the clip nodes of templates/drx/CLIP_STRUCTURE.drx
KEYFRAME_MODE = 2                   # ApplyGradeFromDRX: keyframes at their source frames (verified, concept §12)


@dataclass
class Outcome:
    """What happened to one timeline item."""
    written: dict[str, bool] = field(default_factory=dict)     # node label → SetCDL result
    skipped: str = ""                                           # why nothing was written
    user_version: str = ""
    marker: str = ""
    warnings: list[str] = field(default_factory=list)
    keyframes: int = 0                                          # keyframes written (0: constant values)


def ensure_group(project, item) -> bool:
    """The item is in the colour group its clip's davigen metadata names (assigned now if needed)."""
    mpi = item.GetMediaPoolItem()
    name = mpi.GetThirdPartyMetadata(META_GROUP) if mpi else ""
    if not name:
        return False
    current = item.GetColorGroup()
    if current is not None and current.GetName() == name:
        return True
    group = next((g for g in project.GetColorGroupsList() or [] if g.GetName() == name), None)
    return bool(group is not None and item.AssignToColorGroup(group))


def node_indices(graph) -> dict[str, int]:
    """Label → node index for the davigen clip nodes."""
    out = {}
    for i in range(1, (graph.GetNumNodes() or 0) + 1):
        label = graph.GetNodeLabel(i) or ""
        if label in c.NODES and label not in out:
            out[label] = i
    return out


def _version_name(item) -> str:
    current = item.GetCurrentVersion() or {}
    return current.get("versionName", "") if isinstance(current, dict) else str(current)


def write_item(project, item, correction: c.Correction | None, recompute: bool = False, dry_run: bool = False,
               previous_user_version: str = "", keyframes: Keyframes | None = None,
               drx_folder: Path | None = None, had_keyframes: bool = False) -> Outcome:
    """Write one item's four nodes into DAVIGEN_AUTO, keyframed when `keyframes` is given. had_keyframes: the
    last run keyframed DAVIGEN_AUTO (SetCDL can't write over keyframes, so it starts from the plain structure).
    Never raises for Resolve refusals; they end up in Outcome."""
    out = Outcome()
    if correction is None:
        out.skipped = "not measured"
        return out
    if not ensure_group(project, item):
        out.skipped = NOT_IN_GROUP
        return out
    graph = item.GetNodeGraph()
    if not node_indices(graph):
        # a clip nobody graded yet gets the standard structure, the same as 'Assign groups & nodes'
        if color._is_blank(graph) and color.CLIP_TEMPLATE.exists():
            graph.ApplyGradeFromDRX(str(color.CLIP_TEMPLATE), 0)
            graph = item.GetNodeGraph()
    if not node_indices(graph):
        out.skipped = NO_NODES
        return out

    names = item.GetVersionNameList(0) or []
    current = _version_name(item)
    out.user_version = previous_user_version if current == AUTO else current
    if AUTO in names and not recompute:
        out.skipped = f"{AUTO} exists (recompute to overwrite)"
        return out
    if dry_run:
        out.skipped = "dry run"
        return out
    # keyframes survive every later grade file and SetCDL only writes into one of them (verified 2026-09-29):
    # a DAVIGEN_AUTO that had or gets keyframes is built again as a fresh copy of the user's version
    if AUTO in names and (had_keyframes or keyframes is not None):
        source = out.user_version if out.user_version in names and out.user_version != AUTO else \
            next((n for n in names if n != AUTO), "")
        if not (source and item.LoadVersionByName(source, 0) and _version_name(item) == source
                and item.DeleteVersionByName(AUTO, 0)):
            out.skipped = f"Resolve didn't let davigen rebuild {AUTO} – nothing written"
            return out
        names = [n for n in names if n != AUTO]
    if AUTO not in names and not item.AddVersion(AUTO, 0):
        out.skipped = f"Resolve didn't add the version {AUTO}"
        return out
    item.LoadVersionByName(AUTO, 0)
    if _version_name(item) != AUTO:
        # safety net: never write while the user's version is active
        out.skipped = f"{AUTO} isn't the active version – nothing written"
        return out

    if keyframes is not None and drx_folder is not None:
        if _write_keyframes(item, keyframes, drx_folder, out):
            return out
    indices = node_indices(item.GetNodeGraph())
    for label in c.NODES:
        if label not in indices:
            out.warnings.append(f"node {label} is missing – not written")
            continue
        try:
            ok = bool(item.SetCDL(correction.nodes[label].to_resolve(indices[label])))
        except Exception as e:  # noqa: BLE001 - a refusal must not stop the timeline
            ok = False
            out.warnings.append(f"SetCDL on {label}: {e}")
        out.written[label] = ok
        if not ok:
            out.warnings.append(f"Resolve refused the values for {label}")
    return out


def labels(graph) -> tuple[str, ...]:
    return tuple(graph.GetNodeLabel(i) or "" for i in range(1, (graph.GetNumNodes() or 0) + 1))


def params(label: str, cdl: p.Cdl) -> dict[int, float]:
    """A node's CDL as primaries parameters (concept §12): nodes 01–02 are offsets (exact), 03 contrast around a
    pivot (an S-curve in Resolve, so davigen writes node 03 with SetCDL), 04 saturation with Lum Mix 0."""
    if label in (c.EXPOSURE, c.WHITE_BALANCE):
        return {pid: o / drx.OFFSET_SCALE for pid, o in zip(drx.P_OFFSET, cdl.offset)}
    if label == c.CONTRAST:
        k = cdl.slope[0]
        pivot = cdl.offset[0] / (1.0 - k) if abs(1.0 - k) > 1e-6 else p.GREY
        return {drx.P_CONTRAST: k, drx.P_PIVOT: pivot}
    if label == c.SATURATION:
        return {drx.P_SATURATION: cdl.sat, drx.P_LUM_MIX: 0.0}
    raise ValueError(label)


KEYFRAMED = (c.EXPOSURE, c.WHITE_BALANCE, c.SATURATION)   # Offset, Contrast ≤ 1, Saturation: exact (concept §12)


def _write_keyframes(item, kf: Keyframes, folder: Path, out: Outcome) -> bool:
    """Replace DAVIGEN_AUTO's grade by davigen's structure with keyframes on nodes 01, 02 and 04, then SetCDL the
    constant node 03 (dynamic.resolve_nodes: Resolve's Contrast is exact only up to 1). False: fall back to
    constant values."""
    graph = item.GetNodeGraph()
    if labels(graph) != STRUCTURE:
        out.warnings.append("keyframes skipped: the nodes differ from davigen's structure – constant values written")
        return False
    folder.mkdir(parents=True, exist_ok=True)
    name = "".join(ch if ch.isalnum() else "_" for ch in str(item.GetUniqueId() if hasattr(item, "GetUniqueId")
                                                            else item.GetName()))
    target = folder / f"{name}.drx"
    node03, node04 = dynamic.resolve_nodes(kf)
    nodes = {label: [params(label, cdl) for cdl in kf.nodes[label]] for label in (c.EXPOSURE, c.WHITE_BALANCE)}
    nodes[c.SATURATION] = [{drx.P_CONTRAST: r["contrast"], drx.P_PIVOT: r["pivot"], drx.P_SATURATION: r["sat"],
                            drx.P_LUM_MIX: 0.0} for r in node04]
    static = tuple(label for label in STRUCTURE if label not in KEYFRAMED)
    target.write_text(drx.make_keyframe_drx(drx.KEYFRAME_TEMPLATE.read_text(encoding="utf-8"), kf.frames, nodes,
                                            static=static), encoding="utf-8")
    try:
        ok = bool(graph.ApplyGradeFromDRX(str(target), KEYFRAME_MODE))
    except Exception as e:  # noqa: BLE001
        ok = False
        out.warnings.append(f"ApplyGradeFromDRX: {e}")
    graph = item.GetNodeGraph()                         # the old graph object is stale after a DRX
    if not ok or labels(graph) != STRUCTURE:
        out.warnings.append("Resolve didn't take the keyframed grade – constant values written")
        if labels(graph) != STRUCTURE and color.CLIP_TEMPLATE.exists():
            graph.ApplyGradeFromDRX(str(color.CLIP_TEMPLATE), 0)
        return False
    indices = node_indices(graph)
    out.written = {label: True for label in KEYFRAMED}
    try:
        done = bool(item.SetCDL(node03.to_resolve(indices[c.CONTRAST])))
    except Exception as e:  # noqa: BLE001
        done = False
        out.warnings.append(f"SetCDL on {c.CONTRAST}: {e}")
    out.written[c.CONTRAST] = done
    if not done:
        out.warnings.append(f"Resolve refused the values for {c.CONTRAST}")
    out.keyframes = len(kf.frames)
    return True


# ------------------------------------------------------------------------------------------------ markers

def clear_markers(items) -> None:
    for item in items:
        try:
            item.DeleteMarkerByCustomData(MARKER_DATA)
        except (AttributeError, TypeError):
            pass


def mark(item, flags: list[str], confidence: float | None, threshold: float, skipped: str = "") -> str:
    """One marker at the item's first frame when it needs a look. Returns its name ('' if none)."""
    problem = skipped in (NOT_IN_GROUP, NO_NODES)
    if not problem and (confidence is None or confidence >= threshold):
        return ""                           # confident: its flags are in the report, no marker needed
    main = skipped if problem else (flags[0] if flags else "low confidence")
    name = f"davigen: {main}"
    note = "; ".join(flags) + (f" · confidence {confidence:.2f}" if confidence is not None else "")
    colour = "Red" if problem else "Yellow"
    try:
        item.AddMarker(0, colour, name, note or skipped, 1, MARKER_DATA)
    except TypeError:
        item.AddMarker(0, colour, name, note or skipped, 1)
    return name


# ------------------------------------------------------------------------------------------------- record

def save_record(base: Path, timeline_name: str, record: dict) -> Path:
    folder = base / "00_ADMIN" / "PROJECT_INFO" / "basic_correction"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{timeline_name}.json"
    target.write_text(json.dumps(record, indent=1, ensure_ascii=False, default=_plain), encoding="utf-8")
    return target


def save_look(base: Path, look: dict) -> Path:
    """The project's look (concept §15), next to its reports."""
    folder = base / "00_ADMIN" / "PROJECT_INFO" / "basic_correction"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "look.json"
    target.write_text(json.dumps(look, indent=1), encoding="utf-8")
    return target


def load_look(base: Path) -> dict:
    try:
        return json.loads((base / "00_ADMIN" / "PROJECT_INFO" / "basic_correction" / "look.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def load_record(base: Path, timeline_name: str) -> dict:
    target = base / "00_ADMIN" / "PROJECT_INFO" / "basic_correction" / f"{timeline_name}.json"
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _plain(value):
    if isinstance(value, p.Cdl):
        return value.to_dict()
    if hasattr(value, "tolist"):
        return value.tolist()
    if hasattr(value, "item"):
        return value.item()
    return str(value)
