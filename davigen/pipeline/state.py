"""The real state of a project's assets: what is still in Resolve, what is on disk, what each action has done.

Used before every run (so a clip removed in Resolve is noticed and nothing tries to work on it) and by the assets
page (one row per asset, one status per action)."""

from __future__ import annotations

import os

from .assets import AssetStore
from .core import ASSET, Context, all_actions
from .actions.intake import pool_items


def reconcile(ctx: Context) -> dict:
    """Mark assets removed from the Media Pool (and back again), note files that are gone. Returns counts."""
    store: AssetStore = ctx.store
    ctx.forget("pool")
    pool = pool_items(ctx)
    counts = {"removed": 0, "back": 0, "missing": 0}
    for a in store:
        if a.media_id or a.done.get("import_media"):
            there = f"id:{a.media_id}" in pool or f"path:{a.resolve_path}" in pool
            if not there and not a.removed:
                a.removed = True
                counts["removed"] += 1
            elif there and a.removed:
                a.removed = False
                counts["back"] += 1
        missing = bool(a.path) and not os.path.exists(a.path)       # a link whose card is out counts too
        if missing != bool(a.info.get("missing")):
            a.info["missing"] = missing
        counts["missing"] += missing
    store.save()
    return counts


def table(ctx: Context) -> dict:
    """Every asset with a status per action that applies to it (done / todo / n/a), for the assets page."""
    actions = [a for a in all_actions().values() if a.scope == ASSET and a.id not in ("bring_in",)]
    rows = []
    for asset in ctx.store:
        status = {}
        if not asset.removed:
            for a in actions:
                if a.applies(ctx, asset):
                    try:
                        status[a.id] = a.check(ctx, asset).state
                    except Exception:  # noqa: BLE001 - a status is information, never a failure
                        status[a.id] = "?"
        rows.append({"id": asset.id, "name": asset.name, "kind": asset.kind, "path": asset.path,
                     "mode": asset.mode, "size": asset.size, "added": asset.added, "created": asset.created,
                     "camera": asset.camera_name, "group": asset.group, "removed": asset.removed,
                     "missing": bool(asset.info.get("missing")), "link": asset.mode == "link",
                     "info": {k: v for k, v in asset.info.items() if k not in ("probe",)}, "status": status})
    return {"assets": sorted(rows, key=lambda r: (r["kind"], r["created"] or "", r["name"])),
            "actions": [{"id": a.id, "label": a.label, "kinds": sorted(a.kinds), "mandatory": a.mandatory}
                        for a in actions]}
