"""Changes within a clip – a tunnel exit, a cloud over the sun, a pan from shade into light – as keyframes
(concept §14).

The clip's correction (correct.py, scenes.py) is right for its median frame. When the key or the light moves
during the shot, davigen measures more frames around the change (`extra_frames`) and keyframes nodes 01 and 02 so
that each moment lands close to where the median frame does. Small moves around the median are composition, not
light, and stay alone (a dead zone); beyond it a share of the change is corrected, so a tunnel still reads darker
than the street after it. A moment whose exposure moves also gets its own black point (the street's contrast would
crush a lifted tunnel); saturation stays constant. Every number is in [basic_correction.dynamic].
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, field

import numpy as np

from . import correct as c
from . import measure as ms
from . import pipeline as p


@dataclass
class Keyframes:
    """Node values over time. frames are source frames of the clip, in its own frame rate."""
    frames: list[int]
    nodes: dict[str, list[p.Cdl]]               # label → one CDL per frame (nodes 01–04)
    stops: list[float] = field(default_factory=list)     # exposure change against the static correction
    kelvin: list[float] = field(default_factory=list)    # the light measured at each keyframe
    reason: str = ""

    def to_dict(self) -> dict:
        return {"frames": list(self.frames), "stops": list(self.stops), "kelvin": list(self.kelvin),
                "reason": self.reason, "nodes": {k: [v.to_dict() for v in vs] for k, vs in self.nodes.items()}}


# ------------------------------------------------------------------------------------------------ series

def _mired(cct: float) -> float:
    return 1e6 / max(float(cct), 1000.0)


def _median3(values: np.ndarray) -> np.ndarray:
    """A running median over three neighbours (ends kept): drops one-sample outliers, keeps steps."""
    v = np.asarray(values, dtype="float64")
    if len(v) < 3:
        return v.copy()
    out = v.copy()
    stacked = np.stack([v[:-2], v[1:-1], v[2:]], axis=0)
    out[1:-1] = np.median(stacked, axis=0)
    return out


def extra_frames(frames: list[int], samples: list[dict], settings: dict) -> list[int]:
    """Frames to measure between neighbouring samples that differ by more than the refine steps."""
    dy = settings["dynamic"]
    if not dy["enabled"] or len(frames) < 2:
        return []
    # only a clip that can get keyframes is worth more frames (a steady hand-held shot wobbles a stop anyway)
    use = [s for s in samples if s.get("usable", True)]
    keys = _median3(np.array([s["exposure_stops"] for s in use])) if use else np.zeros(1)
    mireds = _median3(np.array([_mired(s["cct"]) for s in use])) if use else np.zeros(1)
    if np.ptp(keys) < dy["min_change_stops"] and np.ptp(mireds) < dy["min_change_mired"]:
        return []
    have = set(frames)
    out: set[int] = set()
    for (fa, sa), (fb, sb) in zip(zip(frames, samples), zip(frames[1:], samples[1:])):
        jump = (abs(sa["exposure_stops"] - sb["exposure_stops"]) > dy["refine_step_stops"]
                or abs(_mired(sa["cct"]) - _mired(sb["cct"])) > dy["refine_step_mired"])
        if not jump or fb - fa < 3:
            continue
        n = min(dy["refine_frames"], fb - fa - 1)
        for i in range(1, n + 1):
            f = int(round(fa + i * (fb - fa) / (n + 1)))
            if f not in have:
                out.add(f)
    return sorted(out)


# -------------------------------------------------------------------------------------------------- plan

def _dead(delta: float, dead: float) -> float:
    return math.copysign(max(0.0, abs(delta) - dead), delta)


def plan(frames: list[int], samples: list[dict], m: ms.Measurement, corr: c.Correction, thumbs: list,
         output_lut, settings: dict) -> Keyframes | None:
    """Keyframes for a clip whose light changes, or None when one set of values fits the whole shot.

    frames / samples / thumbs: every measured frame of the clip in time order (samples are Measurement.per_sample
    dicts, thumbs the DaVinci Intermediate thumbnails). m and corr are the clip's static result.
    """
    dy, ex = settings["dynamic"], settings["exposure"]
    if not dy["enabled"] or len(frames) < 2:
        return None
    # frames that say nothing (lens covered, all sky) are left out: the curve bridges them
    order = [i for i in np.argsort(frames) if samples[i].get("usable", True)]
    if len(order) < 2:
        return None
    frames = [int(frames[i]) for i in order]
    samples = [samples[i] for i in order]
    thumbs = [thumbs[i] for i in order]
    keys = _median3(np.array([s["exposure_stops"] for s in samples]))
    lights = np.array([s["illuminant"] for s in samples], dtype="float64")
    lights = np.stack([_median3(lights[:, ch]) for ch in range(3)], axis=-1)
    lights /= np.maximum(p.dwg_luminance(lights), 1e-9)[:, None]
    mireds = np.array([_mired(p.cct_duv(p.dwg_to_xy(li))[0]) for li in lights])

    median_key = m.exposure_stops
    median_mired = _mired(m.cct)
    exposure_moves = float(np.ptp(keys)) >= dy["min_change_stops"]
    wb_static = corr.nodes[c.WHITE_BALANCE]
    wb_moves = (float(np.ptp(mireds)) >= dy["min_change_mired"] and not wb_static.is_identity
                and corr.confidence.get(c.WHITE_BALANCE, 0.0) >= dy["min_wb_confidence"])
    if not exposure_moves and not wb_moves:
        return None

    exp_static = corr.nodes[c.EXPOSURE]
    key_di = float(p.to_log(0.18 * 2.0 ** median_key))
    static_stops = c._offset_to_stops(key_di, exp_static.offset[0])
    anchor, _ = c.white_balance_cdl(m, exp_static, *c.wb_target(m.cct, m.duv, settings)[:2])

    con_static = corr.nodes[c.CONTRAST]
    stops_out, offsets, wbs, kelvins, own_contrast = [], [], [], [], []
    for f, k, light, mired, thumb in zip(frames, keys, lights, mireds, thumbs):
        # exposure: the part of the key's move beyond the dead zone, `follow` of it
        stops = static_stops
        if exposure_moves:
            stops = static_stops + dy["follow"] * _dead(median_key - k, dy["dead_stops"])
            stops = min(max(stops, -ex["max_stops_down"]), ex["max_stops_up"])
            if stops > static_stops:
                stops = _hold_highlights(thumb, output_lut, float(k), static_stops, stops, settings)
        mk = dataclasses.replace(m, exposure_stops=float(k))
        exp_cdl = _exposure_at(float(k), stops) if exposure_moves else exp_static
        # white balance: the static node plus how much this moment's light differs from the clip's
        if wb_moves and abs(mired - median_mired) > dy["dead_mired"]:
            share = dy["wb_follow"] * _dead(mired - median_mired, dy["dead_mired"]) / (mired - median_mired)
            use = _normalise(np.asarray(m.illuminant) ** (1 - share) * light ** share)
        else:
            use = np.asarray(m.illuminant, dtype="float64")
        ml = dataclasses.replace(mk, illuminant=[float(v) for v in use])
        cct, duv = p.cct_duv(p.dwg_to_xy(use))
        here, _ = c.white_balance_cdl(ml, exp_cdl, *c.wb_target(cct, duv, settings)[:2])
        wb = p.Cdl(offset=tuple(float(s + h - a) for s, h, a in zip(wb_static.offset, here.offset, anchor.offset)))
        if wb_static.is_identity:
            wb = wb_static
        # the frame's own black point: the street's contrast would crush a lifted tunnel
        own = con_static
        if exposure_moves and abs(stops - static_stops) > 1e-3:
            sim = c._Sim([np.asarray(thumb, dtype="float64")[..., :3]], output_lut, m.clip_level,
                         settings["measure"]["clip_tolerance"])
            own = c._contrast(m, sim, [exp_cdl, wb], settings, {})[0]
        own_contrast.append(own)
        stops_out.append(float(stops - static_stops))
        offsets.append(exp_cdl)
        wbs.append(wb)
        kelvins.append(float(1e6 / mired))

    # contrast: a moment whose exposure moves by contrast_follow_stops or more gets its own (smoothed over three
    # samples), one that stays with the clip keeps the clip's, and in between a blend
    slopes = _median3(np.array([n.slope[0] for n in own_contrast]))
    pivots = _median3(np.array([_pivot(n) for n in own_contrast]))
    s0, p0 = con_static.slope[0], _pivot(con_static)
    cons = []
    for st, sl, pv in zip(stops_out, slopes, pivots):
        w = min(1.0, abs(st) / dy["contrast_follow_stops"]) if exposure_moves else 0.0
        k, pivot = s0 + w * (sl - s0), p0 + w * (pv - p0)
        cons.append(con_static if w == 0.0 else p.Cdl(slope=(k,) * 3, offset=(pivot * (1.0 - k),) * 3))

    keep = _simplify(frames, [np.array([o.offset[0], *w.offset, n.offset[0], 0.3 * (n.slope[0] - 1.0)])
                              for o, w, n in zip(offsets, wbs, cons)],
                     dy["tolerance_stops"] * p.STOP, dy["max_keyframes"])
    if len(keep) < 2:
        return None
    pick = [frames.index(f) for f in keep]
    reason = " and ".join(x for x, on in (("exposure", exposure_moves), ("white balance", wb_moves)) if on)
    return Keyframes(
        frames=keep,
        nodes={c.EXPOSURE: [offsets[i] for i in pick], c.WHITE_BALANCE: [wbs[i] for i in pick],
               c.CONTRAST: [cons[i] for i in pick], c.SATURATION: [corr.nodes[c.SATURATION]] * len(pick)},
        stops=[stops_out[i] for i in pick], kelvin=[kelvins[i] for i in pick],
        reason=f"{reason} change within the shot")


def _pivot(cdl: p.Cdl) -> float:
    k = cdl.slope[0]
    return cdl.offset[0] / (1.0 - k) if abs(1.0 - k) > 1e-6 else p.GREY


def _exposure_at(key: float, stops: float) -> p.Cdl:
    """Node 01 moving a frame whose key is `key` by `stops`, exact at the key."""
    o = float(p.to_log(0.18 * 2.0 ** (key + stops)) - p.to_log(0.18 * 2.0 ** key))
    return p.Cdl(offset=(o, o, o))


def _normalise(rgb) -> np.ndarray:
    rgb = np.maximum(np.asarray(rgb, dtype="float64"), 1e-9)
    return rgb / max(float(p.dwg_luminance(rgb)), 1e-9)


def _hold_highlights(thumb, output_lut, key: float, static_stops: float, stops: float, settings: dict) -> float:
    """Brighten a frame only while its highlights have room (as correct.py does for the whole clip)."""
    frame = np.asarray(thumb, dtype="float64")[..., :3]

    def white(st):
        y = p.luminance(p.apply_lut(p.apply_cdl(frame, _exposure_at(key, st)), output_lut))
        return float(np.percentile(y, 99.5))
    cap = max(settings["contrast"]["white_max"], white(static_stops))
    if white(stops) <= cap:
        return stops
    return c._bisect(white, cap, static_stops, stops)


def _simplify(frames: list[int], values: list[np.ndarray], tolerance: float, max_keys: int) -> list[int]:
    """The fewest frames whose straight lines stay within `tolerance` of every value (Douglas–Peucker)."""
    t = np.asarray(frames, dtype="float64")
    v = np.stack(values)

    def split(a: int, b: int, tol: float, out: set):
        if b - a < 2:
            return
        w = (t[a + 1:b] - t[a]) / max(t[b] - t[a], 1e-9)
        line = v[a] + w[:, None] * (v[b] - v[a])
        err = np.abs(v[a + 1:b] - line).max(axis=1)
        i = int(np.argmax(err))
        if err[i] > tol:
            out.add(a + 1 + i)
            split(a, a + 1 + i, tol, out)
            split(a + 1 + i, b, tol, out)

    tol = tolerance
    while True:
        keep = {0, len(frames) - 1}
        split(0, len(frames) - 1, tol, keep)
        if len(keep) <= max_keys or tol > 1.0:
            break
        tol *= 1.5
    idx = sorted(keep)
    # a clip whose values end where they started, with nothing in between beyond tolerance, needs no keyframes
    if len(idx) == 2 and np.abs(v[idx[0]] - v[idx[1]]).max() <= tolerance:
        return []
    return [frames[i] for i in idx]
