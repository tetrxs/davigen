"""Collect linked files (concept §3.1): a link in the project becomes the real file, at the same place.

Resolve only knows the path inside the project, so nothing changes for it. Each file is copied next to the link,
verified by checksum, and put in the link's place in one step; with 'move' the original is removed afterwards."""

from __future__ import annotations

import os
from pathlib import Path

from ... import transfer
from ..core import DONE, NA, TODO, Action, ActionError, Check, Input, Option, register

ALL_KINDS = frozenset({"camera", "stock", "still", "graphic", "logo", "music", "voice", "sfx", "lut", "font",
                       "document"})


def _links(path: Path) -> list[Path]:
    """The link and its sidecar links (DJI .SRT, Sony M01.XML …) next to it."""
    out = [path]
    for sib in path.parent.glob(path.stem + "*"):
        if sib != path and sib.is_symlink() and sib.name[len(path.stem):] in transfer.SIDECAR_SUFFIXES:
            out.append(sib)
    return out


@register
class Collect(Action):
    id, label, kinds, mandatory = "collect", "Collect linked files", ALL_KINDS, False
    about = "Replace the links in the project by the files themselves – Resolve keeps working without relinking."
    after = ("import_media",)
    on_import = False                               # only makes sense for files already linked
    on_error = "skip"
    inputs = (Input("how", "How", "choice", [Option("copy", "Copy", "the original stays where it is"),
                                            Option("move", "Move", "the original is removed after checking")],
                    default="copy"),)

    def applies(self, ctx, asset):
        return super().applies(ctx, asset) and asset.mode == transfer.LINK

    def check(self, ctx, asset):
        if not asset.path:
            return Check(NA)
        return Check(TODO if Path(asset.path).is_symlink() else DONE)

    def estimate(self, ctx, assets):
        return sum(a.size for a in assets) / 250e6 + len(assets) * 0.1

    def run_one(self, ctx, asset, values, progress):
        how = values.get("how") or "copy"
        for link in _links(Path(asset.path)):
            src = Path(os.readlink(link))
            if not src.exists():
                raise ActionError(f"the original isn't reachable ({src.parent}) – is the card plugged in?")
            part = link.with_name(link.name + ".collect")
            digest = transfer._copy_hashed(src, part)          # verified copy next to the link …
            os.replace(part, link)                               # … takes the link's place in one step
            if how == "move" and transfer._hash_file(link) == digest:
                src.unlink()
        asset.mode = transfer.MOVE if how == "move" else transfer.COPY
        ctx.store.put(asset)
        return None

    def finish(self, ctx, assets, values):
        return f"{len(assets)} files {'moved' if values.get('how') == 'move' else 'copied'} into the project"
