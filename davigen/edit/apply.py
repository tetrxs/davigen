"""Edit Assist in Resolve: markers on Media Pool clips, the selects timeline and the rough cut (concept §6).

Frames: a clip's markers and ranges count in the clip's own frame rate; the timeline counts in its own. A 59.94 fps
clip on a 25 fps timeline plays at 100 % speed, so seconds are the common unit.
"""

from __future__ import annotations

from ..resolve_api import ResolveError, ensure_bin, find_timeline
from . import selects as sel
from .music import Music
from .roughcut import Shot

MARKER_DATA = "davigen-edit"
COLOURS = {sel.GOOD: "Green", sel.UNUSABLE: "Red", sel.SPEECH: "Blue"}


def clip_fps(mpi, fallback: float) -> float:
    try:
        return float(str(mpi.GetClipProperty("FPS")).split()[0]) or fallback
    except (TypeError, ValueError, IndexError):
        return fallback


def mark_clip(mpi, segments: list[sel.Segment], fps: float) -> int:
    """Replace davigen's markers on a Media Pool clip. Returns how many were set."""
    try:
        mpi.DeleteMarkerByCustomData(MARKER_DATA)
    except (AttributeError, TypeError):
        pass
    count = 0
    for s in segments:
        frame = int(round(s.start * fps))
        duration = max(1, int(round(s.length * fps)))
        if s.kind == sel.GOOD:
            name, note = f"davigen: good {s.rating:.2f}", f"{s.length:.1f} s usable"
        elif s.kind == sel.UNUSABLE:
            name, note = f"davigen: {s.reason or 'unusable'}", f"{s.length:.1f} s"
        else:
            name = "davigen: speech"
            note = s.text[:1000] if s.text else f"{s.length:.1f} s with voice"
        try:
            ok = mpi.AddMarker(frame, COLOURS[s.kind], name, note, duration, MARKER_DATA)
        except TypeError:
            ok = mpi.AddMarker(frame, COLOURS[s.kind], name, note, duration)
        count += bool(ok)
    return count


def tag_clip(mpi, segments: list[sel.Segment], duration: float, cfg: dict) -> str:
    """Clip-level result in the Media Pool: a flag (green: worth a look, red: nothing usable) and keywords
    davigen can replace on the next run. Returns the flag colour set ('' for none)."""
    good = sum(s.length for s in segments if s.kind == sel.GOOD)
    bad = sum(s.length for s in segments if s.kind == sel.UNUSABLE)
    speech = any(s.kind == sel.SPEECH for s in segments)
    for colour in ("Green", "Red"):
        try:
            mpi.ClearFlags(colour)
        except (AttributeError, TypeError):
            pass
    flag = ""
    if good >= cfg["flag_good_seconds"]:
        flag = "Green"
    elif good < 1.0 and duration and bad / duration >= cfg["flag_bad_share"]:
        flag = "Red"
    if flag:
        mpi.AddFlag(flag)
    try:
        current = [k.strip() for k in (mpi.GetMetadata("Keywords") or "").split(",") if k.strip()]
    except (AttributeError, TypeError):
        current = []
    keep = [k for k in current if not k.startswith("davigen ")]
    ours = (["davigen good"] if good >= cfg["flag_good_seconds"] else []) + (["davigen speech"] if speech else []) \
        + (["davigen unusable"] if flag == "Red" else [])
    mpi.SetMetadata({"Keywords": ",".join(keep + ours)})
    return flag


def mark_music(mpi, music: Music, fps: float) -> int:
    """Bars (and section starts) as markers on the music clip. Beats would be too many to read."""
    try:
        mpi.DeleteMarkerByCustomData(MARKER_DATA)
    except (AttributeError, TypeError):
        pass
    starts = {round(s["start"], 1) for s in music.sections[1:]}
    count = 0
    for n, t in enumerate(music.downbeats, 1):
        section = round(t, 1) in starts or any(abs(t - s) < 0.3 for s in starts)
        colour, name = ("Purple", f"davigen: section · bar {n}") if section else ("Cyan", f"bar {n}")
        count += bool(mpi.AddMarker(int(round(t * fps)), colour, name, f"{music.tempo:.1f} BPM", 1, MARKER_DATA))
    return count


def next_name(project, stem: str) -> str:
    """stem_v001, or the next free version: davigen never replaces a timeline."""
    n = 1
    while find_timeline(project, f"{stem}_v{n:03d}"):
        n += 1
    return f"{stem}_v{n:03d}"


def _new_timeline(project, name: str, bin_path: str):
    mp = project.GetMediaPool()
    mp.SetCurrentFolder(ensure_bin(mp, bin_path))
    tl = mp.CreateEmptyTimeline(name)
    if tl is None:
        raise ResolveError(f"Resolve couldn't create the timeline {name}")
    project.SetCurrentTimeline(tl)
    return tl


def selects_timeline(project, name: str, bin_path: str, picks: list[tuple], timeline_fps: float) -> object:
    """picks: [(mpi, start_s, end_s)] in order. Video and sound, back to back."""
    tl = _new_timeline(project, name, bin_path)
    infos = []
    for mpi, a, b in picks:
        fps = clip_fps(mpi, timeline_fps)
        start = int(round(a * fps))
        infos.append({"mediaPoolItem": mpi, "startFrame": start, "endFrame": max(start + 1, int(round(b * fps)))})
    if infos and not project.GetMediaPool().AppendToTimeline(infos):
        raise ResolveError("Resolve didn't put the selects on the timeline")
    return tl


def rough_cut_timeline(project, name: str, bin_path: str, shots: list[Shot], clips: dict, music_mpi,
                       timeline_fps: float) -> tuple[object, list[str]]:
    """Video of every shot on V1, back to back and frame-exact to the beat grid; the music on A1 from the start."""
    warnings = []
    tl = _new_timeline(project, name, bin_path)
    mp = project.GetMediaPool()
    infos = []
    for shot in shots:
        mpi = clips[shot.clip_id]
        fps = clip_fps(mpi, timeline_fps)
        # timeline frames from the beat grid, so cuts don't drift; source frames at the clip's own rate
        tl_frames = int(round(shot.record_end * timeline_fps)) - int(round(shot.record_start * timeline_fps))
        start = int(round(shot.source_start * fps))
        length = max(1, int(round(tl_frames / timeline_fps * fps)))
        infos.append({"mediaPoolItem": mpi, "startFrame": start, "endFrame": start + length, "mediaType": 1})
    if infos and not mp.AppendToTimeline(infos):
        raise ResolveError("Resolve didn't put the shots on the timeline")
    if music_mpi is not None and shots:
        music_fps = clip_fps(music_mpi, timeline_fps)
        end = int(round(shots[-1].record_end * music_fps)) if music_fps else 0
        start_frame = int(tl.GetStartFrame()) if hasattr(tl, "GetStartFrame") else 0
        placed = mp.AppendToTimeline([{"mediaPoolItem": music_mpi, "startFrame": 0, "endFrame": max(1, end),
                                       "mediaType": 2, "trackIndex": 1, "recordFrame": start_frame}])
        if not placed:
            warnings.append("Resolve didn't place the music – drag it from 04_AUDIO/MUSIC onto A1")
        else:
            item = placed[0]
            if hasattr(item, "GetStart") and item.GetStart() != start_frame:
                warnings.append("The music landed after the pictures (this Resolve ignores recordFrame) – "
                                "move it to the start of A1")
    return tl, warnings
