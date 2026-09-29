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


def avoid_jump_cuts(shots: list[Candidate], window: int) -> list[Candidate]:
    """Two shots of the same clip in a row look like a jump cut. Pull the next shot of another clip forward, if one
    is within `window` places (the same part of the day, so the story's order holds)."""
    out = list(shots)
    for i in range(1, len(out)):
        if out[i].clip.id != out[i - 1].clip.id:
            continue
        for j in range(i + 1, min(len(out), i + 1 + window)):
            if out[j].clip.id != out[i - 1].clip.id:
                out.insert(i, out.pop(j))
                break
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
    pool = sorted(candidates(clips, cfg, piece + beat), key=lambda c: -c.rating)
    # variety first: a short cut from a long shoot takes one stretch per clip, a long one up to max_per_clip
    sources = len({c.clip.id for c in pool}) or 1
    per_clip_max = max(1, min(cfg["max_per_clip"], -(-6 * len(cuts) // (5 * sources))))
    # stretches shorter than their slot end early and leave beats for more shots: take more until the music is full
    count, shots = len(cuts), []
    while True:
        chosen = _choose(pool, count, per_clip_max, cfg)
        shots, filled = _place(chosen, music, beats, cuts)
        if filled or count >= len(pool):
            break
        if len(chosen) < count and per_clip_max < cfg["max_per_clip"]:
            per_clip_max += 1                       # every clip used as often as allowed so far: allow one more
        count += max(2, len(cuts) // 4)
    return shots


def _choose(pool: list[Candidate], count: int, per_clip_max: int, cfg: dict) -> list[Candidate]:
    """The best `count` stretches, at most per_clip_max per clip, back in recording order."""
    per_clip: dict[str, int] = {}
    chosen = []
    for c in pool:
        if per_clip.get(c.clip.id, 0) < per_clip_max:
            chosen.append(c)
            per_clip[c.clip.id] = per_clip.get(c.clip.id, 0) + 1
        if len(chosen) >= count:
            break
    chosen.sort(key=lambda c: c.key)
    return avoid_jump_cuts(chosen, cfg["reorder_window"])


def _place(chosen: list[Candidate], music: Music, beats: list[float], cuts: list[int]) -> tuple[list[Shot], bool]:
    """Lay the stretches on the beat grid. Returns the shots and whether they reach the end of the music."""
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
            if fit - pos < 2:                       # under two beats: not worth a cut
                continue
            end = fit
            if target - fit < 2:                    # the slot's last beat or so goes to the next shot
                k += 1
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
    return shots, pos >= len(beats) - 1


# ------------------------------------------------------------------------------------------ length and pace

PACES = {
    # bars per shot where the music is calm / energetic, and cutting every half bar above this tempo
    "calm": {"bars_calm": 4, "bars_energetic": 2, "half_bar_above_bpm": 0},
    "auto": {},                                     # as [edit_assist.rough_cut] says
    "fast": {"bars_calm": 1, "bars_energetic": 1, "half_bar_above_bpm": 100},
}


def with_pace(cfg: dict, pace: str) -> dict:
    return {**cfg, **PACES.get(pace, {})}


def music_window(music: Music, seconds: float) -> tuple[float, float]:
    """The part of a song a cut of about `seconds` uses: whole bars, starting where a section starts if one fits,
    the most energetic such stretch (a 30 s reel wants the chorus, not the intro). The whole song when it's
    shorter or seconds is 0."""
    if not seconds or seconds >= music.duration - 1 or len(music.downbeats) < 4:
        return 0.0, music.duration
    downs = list(music.downbeats)
    starts = [0.0] + [s["start"] for s in music.sections[1:]]

    def energy(a: float, b: float) -> float:
        total = 0.0
        for s in music.sections:
            overlap = max(0.0, min(b, s["end"]) - max(a, s["start"]))
            total += overlap * s["energy"]
        return total / max(b - a, 1e-6)

    best, best_score = (0.0, music.duration), -1.0
    for a in downs:
        ends = [d for d in downs if d > a] + [music.duration]
        b = min(ends, key=lambda d: abs((d - a) - seconds))
        if abs((b - a) - seconds) > max(4.0, 0.15 * seconds):
            continue
        at_section = any(abs(a - s) < 0.6 for s in starts)
        score = energy(a, b) + (0.15 if at_section else 0.0)
        if score > best_score:
            best, best_score = (a, b), score
    return best


def cut_music(music: Music, start: float, end: float) -> Music:
    """The song between start and end, with its beats, bars and sections counted from start."""
    def inside(ts):
        return [round(t - start, 3) for t in ts if start - 1e-6 <= t < end - 1e-6]
    secs = [{"start": max(0.0, s["start"] - start), "end": min(end, s["end"]) - start, "energy": s["energy"]}
            for s in music.sections if s["end"] > start and s["start"] < end]
    return Music(duration=end - start, tempo=music.tempo, beats=inside(music.beats),
                 downbeats=inside(music.downbeats), sections=secs)
