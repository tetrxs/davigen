"""The real state of a project's assets: what is still in Resolve, what is on disk, what each action has done.

Used before every run (so a clip removed in Resolve is noticed and nothing tries to work on it) and by the assets
page (one row per asset, one status per action)."""

from __future__ import annotations

import os
from pathlib import Path

from .. import media_pool, transfer
from .assets import EXT, Asset, AssetStore, fingerprint
from .core import ASSET, Context, all_actions
from .actions.intake import pool_items

BIN_KINDS = {"MUSIC": "music", "VO": "voice", "DIALOGUE": "voice", "SFX": "sfx", "AMBIENCE": "sfx",
             "LOGOS": "logo", "92_STOCK": "stock", "09_STILLS": "still"}


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


def table(ctx: Context, live: bool = True) -> dict:
    """Every asset with a status per action that applies to it (done / todo / n/a), for the assets page.
    live=False: from the record only (while a run is using Resolve)."""
    actions = [a for a in all_actions().values() if a.scope == ASSET and a.id not in ("bring_in",)]
    rows = []
    for asset in ctx.store:
        status = {}
        if not asset.removed:
            for a in actions:
                if not a.applies(ctx, asset):
                    continue
                if not live:
                    status[a.id] = "done" if a.id in asset.done else "?"
                    continue
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


def adopt(ctx: Context) -> int:
    """Take clips into the record that davigen didn't import through the pipeline: camera clips of projects made
    by an older davigen (they carry davigen's colour-group metadata), and files in davigen's own bins. Returns how
    many were added. Nothing in Resolve changes."""
    store: AssetStore = ctx.store
    known = store.by_path()
    added = 0

    def walk(folder, trail):
        nonlocal added
        for clip in folder.GetClipList() or []:
            path = clip.GetClipProperty("File Path") or ""
            if not path or path in known or not os.path.exists(path):
                continue
            kind = _kind_of(clip, path, trail)
            if not kind:
                continue
            try:
                asset = Asset(id=fingerprint(path), kind=kind, name=Path(path).name, source=path,
                              size=os.path.getsize(path), path=path)
            except OSError:
                continue
            asset.mode = transfer.LINK if os.path.islink(path) else \
                transfer.MOVE if ctx.base is not None and Path(path).is_relative_to(ctx.base) else transfer.LEAVE
            asset.media_id = clip.GetUniqueId() if hasattr(clip, "GetUniqueId") else ""
            if kind == "camera":
                asset.group = clip.GetThirdPartyMetadata(media_pool.META_GROUP) or ""
                asset.profile = clip.GetThirdPartyMetadata(media_pool.META_PROFILE) or ""
                asset.camera_key = clip.GetThirdPartyMetadata(media_pool.META_CAMERA) or ""
                asset.camera_name = clip.GetThirdPartyMetadata(media_pool.META_CAMERA_NAME) or asset.camera_key
                asset.created = str(clip.GetClipProperty("Date Recorded") or clip.GetClipProperty("Date Created")
                                    or "")
            store.put(asset)
            known[path] = asset
            added += 1
        for sub in folder.GetSubFolderList() or []:
            walk(sub, [*trail, sub.GetName()])

    proj = ctx.project
    if proj is not None:
        walk(proj.GetMediaPool().GetRootFolder(), [])
    if added:
        store.save()
    return added


def _kind_of(clip, path: str, trail: list[str]) -> str:
    """What an existing clip is: davigen's metadata first, then the bin it sits in."""
    tagged = clip.GetThirdPartyMetadata(media_pool.META_KIND) if hasattr(clip, "GetThirdPartyMetadata") else ""
    if tagged:
        return tagged
    if clip.GetThirdPartyMetadata(media_pool.META_GROUP):
        return "camera"
    for name in reversed(trail):
        if name in BIN_KINDS:
            return BIN_KINDS[name]
    if trail and trail[0] in ("05_GRAPHICS",) and EXT.get(Path(path).suffix.lower()) in ("graphic", "still"):
        return "graphic"
    return ""
