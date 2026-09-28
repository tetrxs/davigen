"""Put the corrections into Resolve without ever touching the user's grade (concept §8, plan step 07).

Every write goes into the local version DAVIGEN_AUTO. AddVersion copies the current grade and makes the copy
active (step 01), so the user's own version keeps everything, and switching versions on the Color page is a
before/after. Nodes are found by label, never by position. Flags become timeline-item markers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .. import color
from ..media_pool import META_GROUP
from . import correct as c
from . import pipeline as p

AUTO = "DAVIGEN_AUTO"
MARKER_DATA = "davigen-basic"
NOT_IN_GROUP = "not in its colour group"
NO_NODES = "node structure missing"


@dataclass
class Outcome:
    """What happened to one timeline item."""
    written: dict[str, bool] = field(default_factory=dict)     # node label → SetCDL result
    skipped: str = ""                                           # why nothing was written
    user_version: str = ""
    marker: str = ""
    warnings: list[str] = field(default_factory=list)


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
               previous_user_version: str = "") -> Outcome:
    """Write one item's four nodes into DAVIGEN_AUTO. Never raises for Resolve refusals; they end up in Outcome."""
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
    if AUTO not in names and not item.AddVersion(AUTO, 0):
        out.skipped = f"Resolve didn't add the version {AUTO}"
        return out
    item.LoadVersionByName(AUTO, 0)
    if _version_name(item) != AUTO:
        # safety net: never write while the user's version is active
        out.skipped = f"{AUTO} isn't the active version – nothing written"
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
