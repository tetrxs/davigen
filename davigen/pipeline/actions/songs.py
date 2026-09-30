"""Song markers (concept §7.8): markers on the music clip itself, so they show wherever the song is used.

Each kind has its own colour and its own custom data, so davigen can replace its markers of one kind without
touching the user's markers or the other kinds. Resolve allows one marker per frame: where two kinds meet, the more
important one keeps the frame and the other moves by a frame or two (else it is left out).
"""

from __future__ import annotations

from ... import decode, music
from ..core import DONE, TODO, Action, ActionError, Check, Input, Option, register
from .intake import item_of

PREFIX = "davigen-song"
LEGACY = "davigen-edit"                   # the bar markers of Edit assist (until 2026-09-30), replaced by these
# kind: colour, name, what it is, priority (lower wins a frame)
KINDS = {
    "phrases": ("Purple", "Phrases and parts", "every 4 bars, and where the song changes character", 0),
    "vocals": ("Pink", "Vocal entries", "where a sung line starts after a pause (estimated)", 1),
    "accents": ("Yellow", "Accents", "hits that stand out from the beat around them", 2),
    "bars": ("Cyan", "Bars", "every downbeat", 3),
}


def clip_fps(mpi, fallback: float = 25.0) -> float:
    try:
        return float(str(mpi.GetClipProperty("FPS")).split()[0]) or fallback
    except (TypeError, ValueError, IndexError):
        return fallback


def markers(track: music.Music, kinds: list[str], fps: float) -> list[dict]:
    """The markers to set: [{"frame", "kind", "color", "name", "note", "duration"}], at most one per frame."""
    wanted: list[dict] = []
    if "phrases" in kinds:
        for n, sec in enumerate(track.sections):
            label = "energetic" if sec["energy"] >= 0.6 else "calm" if sec["energy"] < 0.35 else "medium"
            wanted.append({"t": sec["start"], "kind": "phrases", "name": f"part {n + 1} · {label}",
                           "note": f"{sec['end'] - sec['start']:.0f} s", "seconds": sec["end"] - sec["start"]})
        starts = [s["start"] for s in track.sections]
        for n, t in enumerate(track.phrases):
            if all(abs(t - s) > 0.3 for s in starts):
                wanted.append({"t": t, "kind": "phrases", "name": f"phrase {n + 1}", "note": "4 bars"})
    if "vocals" in kinds:
        wanted += [{"t": t, "kind": "vocals", "name": "vocal entry (estimated)", "note": ""} for t in track.vocals]
    if "accents" in kinds:
        wanted += [{"t": a["time"], "kind": "accents", "name": "accent", "note": f"{a['strength']:.1f}× the bars around"}
                   for a in track.accents]
    if "bars" in kinds:
        wanted += [{"t": t, "kind": "bars", "name": f"bar {n}", "note": f"{track.tempo:.1f} BPM"}
                   for n, t in enumerate(track.downbeats, 1)]
    taken: set[int] = set()
    out = []
    for w in sorted(wanted, key=lambda w: (KINDS[w["kind"]][3], w["t"])):
        frame = int(round(w["t"] * fps))
        for shift in (0, 1, 2, -1):
            if frame + shift >= 0 and frame + shift not in taken:
                frame += shift
                break
        else:
            continue
        taken.add(frame)
        out.append({"frame": frame, "kind": w["kind"], "color": KINDS[w["kind"]][0], "name": w["name"],
                    "note": w["note"], "duration": max(1, int(round(w.get("seconds", 0) * fps)))})
    return sorted(out, key=lambda m: m["frame"])


def davigen_kinds(mpi) -> set[str]:
    """Which kinds of davigen's song markers the clip has now."""
    try:
        found = mpi.GetMarkers() or {}
    except (AttributeError, TypeError):
        return set()
    return {str(m.get("customData", ""))[len(PREFIX) + 1:] for m in found.values()
            if str(m.get("customData", "")).startswith(PREFIX + ":")}


def clear(mpi, kinds) -> None:
    for kind in kinds:
        try:
            mpi.DeleteMarkerByCustomData(f"{PREFIX}:{kind}")
        except (AttributeError, TypeError):
            pass


@register
class SongMarkers(Action):
    id, label, kinds, mandatory = "song_markers", "Song markers", frozenset({"music"}), False
    about = "Markers to cut on, on the song itself: each kind in its own colour."
    after = ("import_media",)
    on_error = "skip"
    seconds_per_unit = 4.0
    inputs = (Input("kinds", "Markers", "multi",
                    [Option(k, v[1], f"{v[0]}: {v[2]}") for k, v in KINDS.items()],
                    default=["bars", "phrases", "accents", "vocals"]),)

    def defaults(self, ctx):
        chosen = ctx.settings.get("song_markers")
        return {"kinds": [k for k in chosen if k in KINDS] if isinstance(chosen, list) else
                list(self.inputs[0].default)}

    def check(self, ctx, asset):
        mpi = item_of(ctx, asset)
        if mpi is None:
            return Check(TODO, "not imported yet")
        return Check(DONE if davigen_kinds(mpi) else TODO)          # other kinds on a marked song: 'redo'

    def prepare(self, ctx, assets, values):
        if not decode.available():
            raise ActionError("Song markers need ffmpeg: install it with 'brew install ffmpeg' (https://brew.sh)")

    def run_one(self, ctx, asset, values, progress):
        kinds = [k for k in values.get("kinds") or [] if k in KINDS]
        if not kinds:
            raise ActionError("no marker kinds chosen")
        mpi = item_of(ctx, asset)
        if mpi is None:
            raise ActionError("not in the Media Pool")
        progress(detail=f"{asset.name}: listening")
        stereo = decode.audio_stereo(asset.resolve_path, music.RATE)
        if len(stereo) < music.RATE * 5:
            raise ActionError("no usable audio")
        track = music.analyse(stereo.mean(axis=1), stereo=stereo)
        progress(detail=f"{asset.name}: {track.tempo:.0f} BPM")
        clear(mpi, KINDS)                                # davigen's own song markers only, every kind
        try:
            mpi.DeleteMarkerByCustomData(LEGACY)
        except (AttributeError, TypeError):
            pass
        count = 0
        for m in markers(track, kinds, clip_fps(mpi)):
            count += bool(mpi.AddMarker(m["frame"], m["color"], m["name"], m["note"], m["duration"],
                                        f"{PREFIX}:{m['kind']}"))
        asset.info["music"] = {"tempo": track.tempo, "bars": len(track.downbeats), "sections": len(track.sections),
                               "accents": len(track.accents), "vocals": len(track.vocals), "markers": count}
        return None

    def finish(self, ctx, assets, values):
        tempos = [a.info.get("music", {}).get("tempo") for a in assets if a.info.get("music")]
        total = sum(a.info.get("music", {}).get("markers", 0) for a in assets)
        return f"{total} markers on {len(assets)} songs" + (f" · {', '.join(f'{t:.0f} BPM' for t in tempos[:4])}"
                                                            if tempos else "")
