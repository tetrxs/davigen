"""Getting assets in: bring them into the project folder, make them readable for Resolve, import and sort them.

These three are one transaction (concept §6): if any of them fails, or the run is stopped before they are all done,
every file goes back where it was and nothing half-imported stays in the Media Pool.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from ... import filesystem, media_pool, transfer
from ...config import Config
from ... import decode
from ...resolve_api import ResolveError, ensure_bin
from ..assets import AUDIO_KINDS, Asset, bin_for, destination
from ..core import ASSET, DONE, NA, TODO, Action, ActionError, Check, register

CONVERTED = "_davigen"                                   # a converted file: <name>_davigen.wav next to its asset
# Resolve 21 can't read these (checked 2026-09-30); everything else it imports, but audio other than 48 kHz PCM
# crackled on MARSEILLE (a 44.1 kHz FLAC with a cover picture), so audio is always given a 48 kHz WAV.
IMAGE_CONVERT = {".svg", ".avif", ".ai", ".eps"}
PCM = ("pcm", "linear pcm", "wav", "aiff", "bwf")


def pool_items(ctx) -> dict[str, object]:
    """Media Pool items by unique id and by file path (built once per run and project, kept up to date by the
    import)."""
    def build():
        items: dict[str, object] = {}

        def walk(folder):
            for c in folder.GetClipList() or []:
                uid = c.GetUniqueId() if hasattr(c, "GetUniqueId") else ""
                if uid:
                    items[f"id:{uid}"] = c
                path = c.GetClipProperty("File Path")
                if path:
                    items[f"path:{path}"] = c
            for sub in folder.GetSubFolderList() or []:
                walk(sub)
        walk(ctx.project.GetMediaPool().GetRootFolder())
        return items
    found = ctx.cached("pool", build)
    return found if found is not None else {}


def item_of(ctx, asset: Asset):
    items = pool_items(ctx)
    return items.get(f"id:{asset.media_id}") if asset.media_id else items.get(f"path:{asset.resolve_path}")


# ------------------------------------------------------------------------------------------------ bring in

@register
class BringIn(Action):
    id, label = "bring_in", "Into the project folder"
    about = "Move, copy or link every file to its place in the project (the davigen setting decides), or leave it."
    kinds = frozenset({"camera", "stock", "still", "graphic", "logo", "music", "voice", "sfx", "lut", "font",
                       "document"})
    transactional = True
    after = ("project_folder", "resolve_project")

    def mode(self, ctx) -> str:
        mode = ctx.settings.get("transfer") or ctx.cfg.workflow["project"]["transfer"]
        return mode if mode in transfer.MODES else transfer.MOVE

    def check(self, ctx, asset):
        known = ctx.store.get(asset.id) if ctx.store is not None else None
        if known is not None and known.path and os.path.lexists(known.path):
            return Check(DONE, "already in the project")
        return Check(TODO)

    def estimate(self, ctx, assets):
        mode = self.mode(ctx)
        if mode in (transfer.LINK, transfer.LEAVE):
            return 0.05 * len(assets)
        size = sum(a.size for a in assets if not (mode == transfer.MOVE and _same_volume(a.source, ctx.base)))
        return 0.05 * len(assets) + size / 250e6          # copy + verify at ~500 MB/s each way

    def prepare(self, ctx, assets, values):
        mode = self.mode(ctx)
        missing = [a.name for a in assets if not os.path.exists(a.source)]
        if missing:
            raise ActionError(f"{len(missing)} files aren't there anymore (card removed?): " + ", ".join(missing[:5]))
        need = transfer.space_needed([Path(a.source) for a in assets], ctx.base, mode)
        free = shutil.disk_usage(ctx.base).free
        if need > free * 0.98:
            raise ActionError(f"Not enough space in {ctx.base}: {need / 1e9:.1f} GB needed, {free / 1e9:.1f} GB free")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        journal = transfer.Journal(ctx.base / "00_ADMIN" / "PROJECT_INFO" / f"transfer_{stamp}.json", mode)
        journal.begin()
        ctx.shared["journal"] = journal
        ctx.shared.setdefault("taken", set())
        ctx.shared["bring_mode"] = mode

    def run_one(self, ctx, asset, values, progress):
        mode = ctx.shared["bring_mode"]
        journal: transfer.Journal = ctx.shared["journal"]
        camera_folder = None
        if asset.kind == "camera":
            filesystem.create_tree(ctx.cfg, ctx.base, [asset.camera_key])
            camera_folder = filesystem.media_folder_for(ctx.cfg, ctx.base, asset.camera_key)
        if mode == transfer.LEAVE:
            path = Path(asset.source)
        else:
            target = destination(ctx.cfg, ctx.base, asset, camera_folder, ctx.shared["taken"])
            path = journal.transfer(Path(asset.source), target)
        asset.path, asset.mode = str(path), mode
        asset.added = datetime.now().isoformat(timespec="seconds")
        ctx.store.put(asset)
        ctx.store.save()
        return {"path": str(path)}

    def undo_one(self, ctx, asset, info):
        if ctx.store is not None and asset is not None:
            ctx.store.drop(asset.id)
            ctx.store.save()
            asset.path = ""

    def commit(self, ctx):
        journal = ctx.shared.get("journal")
        if journal is not None:
            journal.commit()

    def abort(self, ctx):
        journal = ctx.shared.get("journal")
        if journal is not None and not journal.committed:
            result = journal.rollback()
            if result["problems"]:
                raise ActionError("Needs a look: " + "; ".join(result["problems"][:5]))

    def finish(self, ctx, assets, values):
        mode = ctx.shared.get("bring_mode", "")
        how = {"move": "moved", "copy": "copied", "link": "linked", "leave": "left in place"}.get(mode, mode)
        return f"{len(assets)} files {how}"


def _same_volume(a: str, b) -> bool:
    try:
        return transfer.same_volume(Path(a), Path(b))
    except OSError:
        return False


# ------------------------------------------------------------------------------------------ make importable

def audio_probe(path: str) -> dict:
    exe = decode.tool("ffprobe")
    if not exe:
        return {}
    res = subprocess.run([exe, "-v", "error", "-show_entries", "stream=codec_type,codec_name,sample_rate,channels:"
                          "stream_disposition=attached_pic", "-of", "json", path], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=60)
    try:
        streams = json.loads(res.stdout or "{}").get("streams", [])
    except ValueError:
        return {}
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    return {"codec": audio.get("codec_name", ""), "rate": int(audio.get("sample_rate") or 0),
            "channels": int(audio.get("channels") or 0),
            "picture": any(s.get("codec_type") == "video" for s in streams)}


def needs_conversion(cfg: Config, asset: Asset) -> str:
    """Why an asset needs a copy Resolve reads well ('' = it doesn't)."""
    ext = Path(asset.name).suffix.lower()
    if asset.kind in AUDIO_KINDS:
        probe = asset.info.get("probe")
        if probe is None:
            probe = asset.info["probe"] = audio_probe(asset.path or asset.source)
        rate = cfg.workflow["assets"]["audio_rate"]
        if not probe:
            return "" if ext in (".wav", ".aif", ".aiff") else "not PCM"
        if not probe["codec"].startswith("pcm_"):
            return f"{probe['codec'].upper() or ext} → PCM"
        if probe["rate"] != rate:
            return f"{probe['rate'] / 1000:g} kHz → {rate / 1000:g} kHz"
        if probe["picture"]:
            return "cover picture"
        return ""
    if ext in IMAGE_CONVERT:
        return f"{ext[1:].upper()} → PNG"
    return ""


@register
class MakeImportable(Action):
    id, label = "make_importable", "Make readable for Resolve"
    about = ("Audio that isn't 48 kHz PCM (or carries a cover picture) gets a 48 kHz WAV next to it; images Resolve "
             "can't read (SVG, AVIF) a PNG. The original stays.")
    kinds = frozenset({"music", "voice", "sfx", "still", "graphic", "logo"})
    transactional = True
    after = ("bring_in",)
    seconds_per_unit = 1.5

    def check(self, ctx, asset):
        why = needs_conversion(ctx.cfg, asset)
        if not why:
            return Check(NA)
        if asset.import_path and os.path.exists(asset.import_path):
            return Check(DONE, why)
        return Check(TODO, why)

    def prepare(self, ctx, assets, values):
        if not decode.available() and any(a.kind in AUDIO_KINDS for a in assets):
            raise ActionError("Converting audio needs ffmpeg: install it with 'brew install ffmpeg' (https://brew.sh)")

    def run_one(self, ctx, asset, values, progress):
        src = Path(asset.path or asset.source)
        folder = Path(asset.path).parent if asset.mode != transfer.LEAVE else \
            ctx.base / ctx.cfg.workflow["assets"]["kinds"][asset.kind]["folder"]
        folder.mkdir(parents=True, exist_ok=True)
        if asset.kind in AUDIO_KINDS:
            target = folder / f"{src.stem}{CONVERTED}.wav"
            _ffmpeg(["-i", str(src), "-map", "0:a:0", "-vn", "-ar", str(ctx.cfg.workflow["assets"]["audio_rate"]),
                     "-c:a", "pcm_s24le", "-map_metadata", "0"], target)
        else:
            target = folder / f"{src.stem}{CONVERTED}.png"
            _image_to_png(src, target)
        asset.import_path = str(target)
        ctx.store.put(asset)
        return {"file": str(target)}

    def undo_one(self, ctx, asset, info):
        f = Path((info or {}).get("file") or "")
        if f.name and CONVERTED in f.stem and f.exists():
            f.unlink()                                   # davigen's own converted copy, nothing else
        if asset is not None:
            asset.import_path = ""

    def finish(self, ctx, assets, values):
        return f"{len(assets)} files converted (originals kept)"


def _ffmpeg(args: list[str], target: Path) -> None:
    exe = decode.tool("ffmpeg")
    if not exe:
        raise ActionError("ffmpeg is missing (brew install ffmpeg)")
    part = target.with_name(target.stem + ".part" + target.suffix)
    res = subprocess.run([exe, "-v", "error", "-y", *args, str(part)], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=1800)
    if res.returncode != 0 or not part.exists():
        part.unlink(missing_ok=True)
        raise ActionError(f"Couldn't convert {target.name}: {res.stderr.strip()[-200:]}")
    part.replace(target)


def _image_to_png(src: Path, target: Path) -> None:
    """SVG through Quick Look (renders vectors), everything else through sips or ffmpeg."""
    if src.suffix.lower() == ".svg":
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["qlmanage", "-t", "-s", "2048", "-o", tmp, str(src)], capture_output=True, timeout=120)
            out = next(Path(tmp).glob("*.png"), None)
            if out is None:
                raise ActionError(f"Couldn't render {src.name}")
            shutil.move(str(out), target)
        return
    res = subprocess.run(["sips", "-s", "format", "png", str(src), "--out", str(target)], capture_output=True,
                         timeout=120)
    if res.returncode != 0 or not target.exists():
        _ffmpeg(["-i", str(src), "-frames:v", "1"], target)


# ------------------------------------------------------------------------------------------- import + sort

@register
class ImportMedia(Action):
    id, label = "import_media", "Import + sort in the Media Pool"
    about = "Every file into its bin, camera clips tagged with camera, profile and colour group."
    kinds = frozenset({"camera", "stock", "still", "graphic", "logo", "music", "voice", "sfx"})
    transactional = True
    after = ("make_importable",)
    batch = False
    seconds_per_unit = 0.3

    def check(self, ctx, asset):
        return Check(DONE if item_of(ctx, asset) is not None else TODO)

    def run_one(self, ctx, asset, values, progress):
        cfg = ctx.cfg
        mp = ctx.project.GetMediaPool()
        camera_bin = ""
        if asset.kind == "camera":
            folder = filesystem.media_folder_for(cfg, ctx.base, asset.camera_key)
            camera_bin = folder.name if folder else asset.camera_key
        mp.SetCurrentFolder(ensure_bin(mp, bin_for(cfg, asset, camera_bin)))
        items = mp.ImportMedia([asset.resolve_path]) or []
        item = next((i for i in items if i.GetClipProperty("File Path") == asset.resolve_path), None) or \
            (items[0] if items else None)
        if item is None:
            raise ActionError(f"Resolve couldn't import {asset.name}")
        tag(cfg, ctx, asset, item)
        asset.media_id = item.GetUniqueId() if hasattr(item, "GetUniqueId") else ""
        pool = pool_items(ctx)
        pool[f"path:{asset.resolve_path}"] = item
        if asset.media_id:
            pool[f"id:{asset.media_id}"] = item
        ctx.store.put(asset)
        return {"media_id": asset.media_id, "path": asset.resolve_path}

    def undo_one(self, ctx, asset, info):
        proj = ctx.project
        if proj is None or asset is None:
            return
        item = item_of(ctx, asset)
        if item is not None and not proj.GetMediaPool().DeleteClips([item]):
            raise ResolveError(f"Resolve didn't remove {asset.name} from the Media Pool")
        pool = pool_items(ctx)
        pool.pop(f"path:{asset.resolve_path}", None)
        pool.pop(f"id:{asset.media_id}", None)
        asset.media_id = ""

    def finish(self, ctx, assets, values):
        kinds = sorted({a.kind for a in assets})
        return f"{len(assets)} files into " + ", ".join(bin_for(ctx.cfg, a) for a in
                                                         {a.kind: a for a in assets if a.kind != "camera"}.values()) \
            if kinds != ["camera"] else f"{len(assets)} clips into their camera bins"


def tag(cfg: Config, ctx, asset: Asset, item) -> None:
    """What davigen writes on an imported clip: its asset id always, camera details on camera clips."""
    meta = {media_pool.META_ASSET: asset.id, media_pool.META_KIND: asset.kind}
    if asset.kind == "camera" and asset.profile in cfg.profiles:
        profile = cfg.profiles[asset.profile]
        keys = [a.camera_key for a in ctx.store] if ctx.store is not None else []
        cameras = list(dict.fromkeys(k for k in keys if k))
        colour = media_pool.CLIP_COLORS[(cameras.index(asset.camera_key) if asset.camera_key in cameras else 0)
                                        % len(media_pool.CLIP_COLORS)]
        item.SetMetadata({
            "Camera #": asset.camera_key, "Camera Type": asset.model or asset.camera_name,
            "Camera Manufacturer": asset.make,
            "Keywords": ",".join(filter(None, [asset.camera_key, profile.label.split(" /")[0].replace(" ", "_")])),
            "Comments": f"davigen: {asset.camera_name} · {profile.label}"})
        meta.update({media_pool.META_GROUP: asset.group, media_pool.META_PROFILE: asset.profile,
                     media_pool.META_CAMERA: asset.camera_key, media_pool.META_CAMERA_NAME: asset.camera_name})
        item.SetClipColor(colour)
    item.SetThirdPartyMetadata(meta)


__all__ = ["BringIn", "MakeImportable", "ImportMedia", "item_of", "pool_items", "ASSET"]
