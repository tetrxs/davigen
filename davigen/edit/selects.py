"""From a watched clip to stretches: good, unusable, speech (concept §3). All thresholds from [edit_assist]."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from .watch import AUDIO_HOP, Watch

GOOD, UNUSABLE, SPEECH = "good", "unusable", "speech"


@dataclass
class Segment:
    kind: str
    start: float                # seconds from the start of the clip
    end: float
    rating: float = 0.0         # good: 0–1, better is higher
    reason: str = ""

    @property
    def length(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict:
        return asdict(self)


def frame_scores(w: Watch, cfg: dict) -> tuple[np.ndarray, list[str]]:
    """Usability 0–1 per frame, and why a frame is unusable (empty string if it isn't)."""
    n = len(w.sharpness)
    if n == 0:
        return np.zeros(0), []
    # sharpness relative to the clip's own best (scenes differ a lot), with an absolute floor for real blur
    best = max(float(np.percentile(w.sharpness, 90)), 1e-6)
    rel = w.sharpness / best
    s_sharp = np.clip((rel - cfg["sharp_rel"][0]) / (cfg["sharp_rel"][1] - cfg["sharp_rel"][0]), 0, 1)
    s_shake = np.clip(1 - (w.shake - cfg["shake"][0]) / (cfg["shake"][1] - cfg["shake"][0]), 0, 1)
    speed = np.hypot(*w.motion.T)
    s_pan = np.clip(1 - (speed - cfg["pan_speed"][0]) / (cfg["pan_speed"][1] - cfg["pan_speed"][0]), 0, 1)
    score = s_sharp * s_shake * s_pan
    why = [""] * n
    for i in range(n):
        if w.brightness[i] < cfg["dark"]:
            why[i] = "dark / covered"
        elif w.detail[i] < cfg["no_detail"]:
            why[i] = "no detail (lens covered, pocket)"
        elif w.sharpness[i] < cfg["blur_abs"]:
            why[i] = "blurred"
        elif w.shake[i] > cfg["shake"][1] * 1.5:
            why[i] = "shaking"
        elif speed[i] > cfg["pan_speed"][1] * 1.5:
            why[i] = "whip pan"
        if why[i]:
            score[i] = 0.0
    return score, why


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """[start, end) index ranges where mask is True."""
    out, start = [], None
    for i, m in enumerate(mask):
        if m and start is None:
            start = i
        elif not m and start is not None:
            out.append((start, i))
            start = None
    if start is not None:
        out.append((start, len(mask)))
    return out


def smooth(values: np.ndarray, width: int) -> np.ndarray:
    if width <= 1 or len(values) == 0:
        return values.astype(float)
    kernel = np.ones(width) / width
    padded = np.pad(values.astype(float), (width // 2, width - 1 - width // 2), mode="edge")
    return np.convolve(padded, kernel, mode="valid")


def segments(w: Watch, cfg: dict) -> list[Segment]:
    """Good, unusable and speech stretches of one clip, in time order."""
    out: list[Segment] = []
    score, why = frame_scores(w, cfg)
    if len(score):
        sm = smooth(score, int(round(w.fps)))
        speed = np.hypot(*w.motion.T)
        for a, b in _runs(sm < cfg["unusable_below"]):
            if (b - a) / w.fps >= cfg["unusable_min"]:
                reasons = [r for r in why[a:b] if r]
                reason = max(set(reasons), key=reasons.count) if reasons else "shaky / soft"
                out.append(Segment(UNUSABLE, a / w.fps, b / w.fps, 0.0, reason))
        for a, b in _runs(sm > cfg["good_above"]):
            length = (b - a) / w.fps
            if length >= cfg["good_min"]:
                calm = 1.0 - min(1.0, float(speed[a:b].mean()) / cfg["pan_speed"][1])
                rating = float(np.clip(sm[a:b].mean() * (0.7 + 0.3 * calm) * (0.85 + 0.15 * min(length / 8, 1)),
                                       0, 1))
                out.append(Segment(GOOD, a / w.fps, b / w.fps, rating))
    if len(w.voice):
        v = smooth(w.voice, max(1, int(round(0.6 / AUDIO_HOP))))
        for a, b in _runs(v > cfg["voice_above"]):
            if (b - a) * AUDIO_HOP >= cfg["speech_min"]:
                out.append(Segment(SPEECH, a * AUDIO_HOP, b * AUDIO_HOP, float(v[a:b].mean())))
    return sorted(out, key=lambda s: (s.start, s.kind))


def calmest_window(w: Watch, seg: Segment, length: float) -> tuple[float, float]:
    """The `length` seconds inside a stretch with the least shake and motion."""
    if seg.length <= length:
        return seg.start, seg.end
    a, b = int(seg.start * w.fps), int(seg.end * w.fps)
    k = max(1, int(round(length * w.fps)))
    cost = w.shake[a:b] + 0.5 * np.hypot(*w.motion[a:b].T)
    if len(cost) <= k:
        return seg.start, seg.start + length
    sums = np.convolve(cost, np.ones(k), mode="valid")
    i = int(np.argmin(sums))
    start = (a + i) / w.fps
    return start, start + length
