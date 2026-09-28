"""A first rough cut: the shoot's best stretches, in recording order, cut on the music's beats (concept §5)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from . import selects as sel
from .music import Music


@dataclass
class ClipPlan:
    id: str                                  # whatever identifies the clip for the caller (Media Pool id, path)
    name: str
    created: str                             # ISO time, sorts the shoot
    order: int                               # tie-break: position in the project
    segments: list[sel.Segment]
    watch: object | None = None              # selects.calmest_window needs it; without, a stretch's middle
    camera: str = ""


@dataclass
class Shot:
    clip_id: str
    clip_name: str
    source_start: float                      # seconds in the clip
    source_end: float
    record_start: float                      # seconds on the timeline
    record_end: float
    rating: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Candidate:
    clip: ClipPlan
    start: float
    end: float
    rating: float
    key: tuple = field(default_factory=tuple)

    @property
    def length(self) -> float:
        return self.end - self.start


def candidates(clips: list[ClipPlan], cfg: dict, piece: float) -> list[Candidate]:
    """Good stretches, speech cut out, long ones split into pieces of about `piece` seconds with gaps between."""
    out = []
    for clip in clips:
        speech = [(s.start, s.end) for s in clip.segments if s.kind == sel.SPEECH] if cfg["skip_speech"] else []
        for seg in (s for s in clip.segments if s.kind == sel.GOOD):
            parts = [(seg.start, seg.end)]
            for a, b in speech:                                  # remove speech from the stretch
                parts = [p for x, y in parts for p in ((x, min(y, a)), (max(x, b), y)) if p[1] - p[0] > 0.5]
            for x, y in parts:
                t = x
                while y - t >= min(piece, 2.0):
                    end = min(y, t + piece)
                    out.append(Candidate(clip, t, end, seg.rating, (clip.created, clip.order, t)))
                    t = end + cfg["min_gap"]
    return out


def grid(music: Music, cfg: dict) -> list[int]:
    """Beat indices to cut on: one or two bars per shot depending on the section's energy."""
    beats = music.beats
    if len(beats) < 2:
        return []
    downs = set(music.downbeats)
    first = next((i for i, b in enumerate(beats) if b in downs), 0)
    cuts = [0] if first else []
    i = first
    while i < len(beats):
        cuts.append(i)
        t = beats[i]
        section = next((s for s in music.sections if s["start"] <= t < s["end"]), music.sections[-1]
                       if music.sections else {"energy": 0.5})
        energetic = section["energy"] >= cfg["energy_split"]
        bars = cfg["bars_energetic"] if energetic else cfg["bars_calm"]
        step = 4 * bars
        if energetic and cfg["half_bar_above_bpm"] and music.tempo > cfg["half_bar_above_bpm"]:
            step = 2
        i += step
    return cuts


def plan(clips: list[ClipPlan], music: Music, cfg: dict) -> list[Shot]:
    cuts = grid(music, cfg)
    if not cuts:
        return []
    beats = list(music.beats) + [music.duration]
    if beats[0] > 0.05:                             # the cut starts with the music, not on its first beat
        beats = [0.0] + beats
        cuts = [0] + [c + 1 for c in cuts if c + 1 > 0]
    if len(cuts) > 2 and cuts[1] - cuts[0] < 2:      # a lead-in shorter than two beats joins the first shot
        cuts.pop(1)
    beat = 60.0 / max(music.tempo, 1.0)
    piece = beat * 4 * max(cfg["bars_calm"], cfg["bars_energetic"])
    pool = candidates(clips, cfg, piece + beat)
    # choose: the best, at most max_per_clip per clip, then back into recording order
    per_clip: dict[str, int] = {}
    chosen = []
    for c in sorted(pool, key=lambda c: -c.rating):
        if per_clip.get(c.clip.id, 0) < cfg["max_per_clip"]:
            chosen.append(c)
            per_clip[c.clip.id] = per_clip.get(c.clip.id, 0) + 1
        if len(chosen) >= len(cuts):
            break
    chosen.sort(key=lambda c: c.key)

    shots: list[Shot] = []
    queue = list(chosen)
    k = 0
    pos = cuts[0]
    targets = cuts[1:] + [len(beats) - 1]
    while queue and k < len(targets):
        target = targets[k]
        if target <= pos:
            k += 1
            continue
        c = queue.pop(0)
        want = beats[target] - beats[pos]
        if c.length + 1e-6 < want:                  # too short: end it on the last whole beat it fills
            fit = pos
            while fit + 1 <= target and beats[fit + 1] - beats[pos] <= c.length + 1e-6:
                fit += 1
            if fit - pos < 2 or target - fit < 2:   # a shot or a leftover under two beats: not worth a cut
                continue
            end = fit
        else:
            end = target
            k += 1
        length = beats[end] - beats[pos]
        seg = sel.Segment(sel.GOOD, c.start, c.end, c.rating)
        if c.clip.watch is not None:
            a, b = sel.calmest_window(c.clip.watch, seg, length)
        else:
            mid = (c.start + c.end) / 2
            a, b = mid - length / 2, mid + length / 2
        shots.append(Shot(c.clip.id, c.clip.name, round(a, 3), round(b, 3), round(beats[pos], 3),
                          round(beats[end], 3), round(c.rating, 3)))
        pos = end
    return shots
