"""Media Pool bins, clip import and tagging."""

from __future__ import annotations

from .config import Config
from .resolve_api import ensure_bin
from .scanner import CameraGroup, ClipInfo

CLIP_COLORS = ["Orange", "Blue", "Green", "Purple", "Yellow", "Teal", "Pink", "Tan"]


def build_bins(media_pool, cfg: Config, camera_bins: list[str]) -> None:
    bins = cfg.workflow["bins"]
    for path in bins["tree"]:
        ensure_bin(media_pool, path)
    for name in camera_bins:
        ensure_bin(media_pool, f"{bins['footage']}/{name}")


# ------------------------------------------------------------------------- import

META_GROUP, META_PROFILE, META_CAMERA = "davigen.group", "davigen.profile", "davigen.camera"
META_CAMERA_NAME = "davigen.camera_name"
META_ASSET, META_KIND = "davigen.asset", "davigen.kind"      # every asset davigen imported (pipeline)


def import_group(media_pool, bin_folder, group: CameraGroup, paths: list[str], color: str,
                 profile_label: str, group_name: str) -> list:
    """Import files into the camera bin and tag them. Returns MediaPoolItems in path order.

    The colour group is stored as hidden third-party metadata so clips can be (re)assigned to
    their group on any timeline later.
    """
    media_pool.SetCurrentFolder(bin_folder)
    items = media_pool.ImportMedia(paths) or []
    by_path = {}
    for item in items:
        by_path[item.GetClipProperty("File Path")] = item
    ordered = [by_path[p] for p in paths if p in by_path]
    keywords = ",".join(filter(None, [group.camera_key, profile_label.split(" /")[0].replace(" ", "_")]))
    for item in ordered:
        item.SetMetadata({
            "Camera #": group.camera_key,
            "Camera Type": group.model or group.camera_name,
            "Camera Manufacturer": group.make,
            "Keywords": keywords,
            "Comments": f"davigen: {group.camera_name} · {profile_label}",
        })
        item.SetThirdPartyMetadata({META_GROUP: group_name, META_PROFILE: group.profile,
                                    META_CAMERA: group.camera_key, META_CAMERA_NAME: group.camera_name})
        item.SetClipColor(color)
    return ordered


def all_clip_paths(folder) -> set[str]:
    """File paths of every clip below a Media Pool folder."""
    paths = {c.GetClipProperty("File Path") for c in folder.GetClipList() or []}
    for sub in folder.GetSubFolderList() or []:
        paths |= all_clip_paths(sub)
    return paths


def davigen_clips(folder) -> list:
    """All clips below a Media Pool folder that davigen tagged."""
    out = [c for c in folder.GetClipList() or [] if c.GetThirdPartyMetadata(META_GROUP)]
    for sub in folder.GetSubFolderList() or []:
        out += davigen_clips(sub)
    return out


def missing(paths: list[str], items: list) -> list[str]:
    imported = {i.GetClipProperty("File Path") for i in items}
    return [p for p in paths if p not in imported]


def sort_key(clip: ClipInfo) -> tuple:
    return (clip.created or "", clip.name)
