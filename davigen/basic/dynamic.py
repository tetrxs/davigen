"""Changes within a clip – a tunnel exit, a cloud over the sun, a pan from shade into light – as keyframes
(concept §14).

The clip's correction (correct.py, scenes.py) is right for its median frame. When the key or the light moves
during the shot, davigen measures more frames around the change (`extra_frames`) and keyframes nodes 01 and 02 so
that each moment lands close to where the median frame does. Small moves around the median are composition, not
light, and stay alone (a dead zone); beyond it a share of the change is corrected, so a tunnel still reads darker
than the street after it. Only nodes 01 and 02 get keyframes (Offset, exact in Resolve); contrast and saturation
stay one value each, the contrast solved on every moment as its own exposure leaves it. Every number is in
[basic_correction.dynamic].
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
        node03, node04 = resolve_nodes(self)
        return {"frames": list(self.frames), "stops": list(self.stops), "kelvin": list(self.kelvin),
                "reason": self.reason, "nodes": {k: [v.to_dict() for v in vs] for k, vs in self.nodes.items()},
                "resolve": {"03": node03.to_dict(), "04": node04}}


HEADROOM = 1.05         # node 03 is set this much above the highest contrast, so node 04 always takes some back


def resolve_nodes(kf: Keyframes) -> tuple[p.Cdl, list[dict]]:
    """How the keyframes are written into Resolve (concept §12): Resolve's Contrast is exact only up to 1, so node
    03 holds the clip's highest contrast as a constant CDL (SetCDL), and node 04 takes the rest back per keyframe
    with a Contrast ≤ 1 around a pivot, plus the saturation. Returns (node 03, [{contrast, pivot, sat}] per
    keyframe); composed they give exactly each keyframe's contrast node."""
    cons, sats = kf.nodes[c.CONTRAST], kf.nodes[c.SATURATION]
    top = max(n.slope[0] for n in cons)
    if top <= 1.0 + 1e-6:
        node03 = p.Cdl()
    else:
        k3 = top * HEADROOM
        best = max(cons, key=lambda n: n.slope[0])
        node03 = p.Cdl(slope=(k3,) * 3, offset=(_pivot(best) * (1.0 - k3),) * 3)
    k3, o3 = node03.slope[0], node03.offset[0]
    rows = []
    for n, sat in zip(cons, sats):
        k = n.slope[0] / k3
        pivot = (n.offset[0] - k * o3) / (1.0 - k) if abs(1.0 - k) > 1e-6 else p.GREY
        rows.append({"contrast": float(k), "pivot": float(pivot), "sat": float(sat.sat)})
    return node03, rows


def node04_cdl(row: dict) -> p.Cdl:
    k = row["contrast"]
    return p.Cdl(slope=(k,) * 3, offset=(row["pivot"] * (1.0 - k),) * 3, sat=row["sat"])


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
         output_lut, settings: dict, fps: float = 25.0) -> Keyframes | None:
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

    # node 03 multiplies what is left of a moment's distance from the clip's key by its contrast; the lift is
    # scaled so that after contrast (1 − follow) of the move remains, as it would without contrast
    slope = corr.nodes[c.CONTRAST].slope[0]
    gain = max(1.0, (1.0 - (1.0 - dy["follow"]) / max(slope, 1.0)) / max(dy["follow"], 1e-6))
    # what each moment asks for: exposure against the clip's correction, and the share of its own light
    times = np.asarray(frames, dtype="float64") / max(fps, 1.0)
    wanted, ceiling, shares = [], [], []
    for k, mired, thumb in zip(keys, mireds, thumbs):
        stops = static_stops
        if exposure_moves:
            stops = static_stops + gain * dy["follow"] * _dead(median_key - k, dy["dead_stops"])
            # a moment may be lifted further than a whole clip: a tunnel is seconds, not the shot
            stops = min(max(stops, -ex["max_stops_down"]), max(ex["max_stops_up"], dy["max_stops_up"]))
        top = stops
        if stops > static_stops:
            top = _hold_highlights(thumb, output_lut, float(k), static_stops, stops, settings, dy["hold_percentile"])
        wanted.append(min(stops, top))
        ceiling.append(top)
        share = 0.0
        if wb_moves and abs(mired - median_mired) > dy["dead_mired"]:
            share = dy["wb_follow"] * _dead(mired - median_mired, dy["dead_mired"]) / (mired - median_mired)
        shares.append(share)
    # as a colorist rides it: smoothed over smooth_seconds, never faster than max_stops_per_second, and never
    # above what a moment's highlights allow
    ride = _rate_limit(times, _smooth_time(times, np.array(wanted), dy["smooth_seconds"]), dy["max_stops_per_second"])
    ride = np.minimum(ride, np.array(ceiling))
    shares = np.clip(_smooth_time(times, np.array(shares), dy["smooth_seconds"]), 0.0, 1.0)

    stops_out, offsets, wbs, kelvins = [], [], [], []
    for k, light, mired, stops, share in zip(keys, lights, mireds, ride, shares):
        mk = dataclasses.replace(m, exposure_stops=float(k))
        exp_cdl = _exposure_at(float(k), float(stops)) if exposure_moves else exp_static
        # white balance: the static node plus how much this moment's light differs from the clip's
        if share > 0:
            use = _normalise(np.asarray(m.illuminant) ** (1 - share) * light ** share)
        else:
            use = np.asarray(m.illuminant, dtype="float64")
        ml = dataclasses.replace(mk, illuminant=[float(v) for v in use])
        cct, duv = p.cct_duv(p.dwg_to_xy(use))
        here, _ = c.white_balance_cdl(ml, exp_cdl, *c.wb_target(cct, duv, settings)[:2])
        wb = p.Cdl(offset=tuple(float(s + h - a) for s, h, a in zip(wb_static.offset, here.offset, anchor.offset)))
        if wb_static.is_identity:
            wb = wb_static
        stops_out.append(float(stops - static_stops))
        offsets.append(exp_cdl)
        wbs.append(wb)
        kelvins.append(float(1e6 / mired))

    keep = _simplify(frames, [np.array([o.offset[0], *w.offset]) for o, w in zip(offsets, wbs)],
                     dy["tolerance_stops"] * p.STOP, dy["max_keyframes"])
    if len(keep) < 2:
        return None
    # contrast can't be keyframed exactly in Resolve (its Contrast is an S-curve, concept §12), so it stays one
    # value, solved on every moment as nodes 01 and 02 leave it: the street's contrast alone crushed a lifted tunnel
    contrast = corr.nodes[c.CONTRAST]
    if exposure_moves:
        sim = c._Sim([np.asarray(t, dtype="float64")[..., :3] for t in thumbs], output_lut, m.clip_level,
                     settings["measure"]["clip_tolerance"])
        sim.frames = [p.apply_nodes(f, [o, w]) for f, o, w in zip(sim.frames, offsets, wbs)]
        contrast = c._contrast(m, sim, [], settings, {})[0]
        contrast = _dont_crush(contrast, sim, stops_out, settings)
    pick = [frames.index(f) for f in keep]
    reason = " and ".join(x for x, on in (("exposure", exposure_moves), ("white balance", wb_moves)) if on)
    return Keyframes(
        frames=keep,
        nodes={c.EXPOSURE: [offsets[i] for i in pick], c.WHITE_BALANCE: [wbs[i] for i in pick],
               c.CONTRAST: [contrast] * len(pick), c.SATURATION: [corr.nodes[c.SATURATION]] * len(pick)},
        stops=[stops_out[i] for i in pick], kelvin=[kelvins[i] for i in pick],
        reason=f"{reason} change within the shot")


def _smooth_time(times: np.ndarray, values: np.ndarray, seconds: float) -> np.ndarray:
    """A Gaussian average over time (σ = seconds / 2), for unevenly spaced samples."""
    if seconds <= 0 or len(values) < 3:
        return np.asarray(values, dtype="float64")
    sigma = seconds / 2.0
    w = np.exp(-0.5 * ((times[:, None] - times[None, :]) / sigma) ** 2)
    return (w @ values) / w.sum(axis=1)


def _rate_limit(times: np.ndarray, values: np.ndarray, per_second: float) -> np.ndarray:
    """No faster change than per_second (forwards, then backwards, so a ramp is centred on the change)."""
    if per_second <= 0 or len(values) < 2:
        return values
    out = np.asarray(values, dtype="float64").copy()
    for order in (range(1, len(out)), range(len(out) - 2, -1, -1)):
        for i in order:
            j = i - 1 if order.start < order.stop else i + 1
            step = per_second * abs(times[i] - times[j])
            out[i] = min(max(out[i], out[j] - step), out[j] + step)
    return out


def _dont_crush(contrast: p.Cdl, sim, stops: list[float], settings: dict) -> p.Cdl:
    """Less contrast when it would push the lifted moments' black point (the tunnel) under the band's lower end."""
    lifted = [i for i, st in enumerate(stops) if st >= settings["dynamic"]["dead_stops"]]
    k0 = contrast.slope[0]
    if not lifted or k0 <= 1.0:
        return contrast
    pivot = _pivot(contrast)
    floor = settings["contrast"]["black"][0]

    def black(k):
        node = p.Cdl(slope=(k,) * 3, offset=(pivot * (1.0 - k),) * 3)
        ys = []
        for i in lifted:
            d = p.luminance(p.apply_lut(p.apply_cdl(sim.frames[i], node), sim.output_lut))
            ys.append(np.percentile(d, 0.5))
        return float(np.median(ys))
    if black(k0) >= floor:
        return contrast
    k = c._bisect(black, floor, 1.0, k0) if black(1.0) >= floor else 1.0
    return p.Cdl(slope=(k,) * 3, offset=(pivot * (1.0 - k),) * 3)


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


def _hold_highlights(thumb, output_lut, key: float, static_stops: float, stops: float, settings: dict,
                     percentile: float = 99.5) -> float:
    """Brighten a frame only while its highlights have room (as correct.py does for the whole clip). A lifted
    moment may let a small part of the frame go to white – the end of a tunnel, its lamps – so it checks a lower
    percentile."""
    frame = np.asarray(thumb, dtype="float64")[..., :3]

    def white(st):
        y = p.luminance(p.apply_lut(p.apply_cdl(frame, _exposure_at(key, st)), output_lut))
        return float(np.percentile(y, percentile))
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
