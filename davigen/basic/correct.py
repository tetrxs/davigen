"""Turn a Measurement into CDL values for the nodes 01–04 (concept §5), with limits and a confidence per node.

Each node is computed on the output of the ones before it, in the simulated pipeline. Offsets are solved in
DaVinci Intermediate so that the reference lands exactly where it should (a log offset is only approximately a
stop near black, see pipeline.py). Every number comes from the settings ([basic_correction] in workflow.toml).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import measure as ms
from . import pipeline as p

EXPOSURE, WHITE_BALANCE, CONTRAST, SATURATION = "01_EXPOSURE", "02_WHITE_BALANCE", "03_CONTRAST", "04_SATURATION"
NODES = (EXPOSURE, WHITE_BALANCE, CONTRAST, SATURATION)
LIMIT = "limit reached"


@dataclass
class Correction:
    nodes: dict[str, p.Cdl]                 # node label → CDL
    confidence: dict[str, float]            # node label → 0–1
    flags: list[str] = field(default_factory=list)
    values: dict = field(default_factory=dict)      # the intermediate numbers, for the record

    @property
    def overall(self) -> float:
        return min(self.confidence.values()) if self.confidence else 0.0

    def chain(self) -> list[p.Cdl]:
        return [self.nodes[n] for n in NODES]

    def to_dict(self) -> dict:
        return {"nodes": {k: v.to_dict() for k, v in self.nodes.items()}, "confidence": dict(self.confidence),
                "overall": self.overall, "flags": list(self.flags), "values": ms._plain(self.values)}


def correct(m: ms.Measurement, samples: list, output_lut: str | Path, settings: dict) -> Correction:
    """Measurement + the same samples it came from → CDL per node. Deterministic."""
    frames = [np.asarray(s, dtype="float64")[..., :3] for s in samples]
    sim = _Sim(frames, output_lut, m.clip_level, settings["measure"]["clip_tolerance"])
    flags = list(m.flags)
    values: dict = {}
    conf = settings["confidence"]

    exp_cdl, exp_conf, exp_limited = _exposure(m, sim, settings, values)
    wb_cdl, wb_conf, wb_limited = _white_balance(m, settings, values, exp_cdl)
    con_cdl, con_conf, con_limited = _contrast(m, sim, [exp_cdl, wb_cdl], settings, values)
    sat_cdl, sat_conf, sat_limited = _saturation(m, sim, [exp_cdl, wb_cdl, con_cdl], settings, values)
    if exp_limited or wb_limited:           # contrast and saturation at their range are normal, not a doubt
        flags.append(LIMIT)
    values.update(contrast_limited=bool(con_limited), saturation_limited=bool(sat_limited))
    confidence = {
        EXPOSURE: _clamp(exp_conf - conf["limit"] * exp_limited),
        WHITE_BALANCE: _clamp(wb_conf - conf["limit"] * wb_limited),
        CONTRAST: _clamp(con_conf),
        SATURATION: _clamp(sat_conf),
    }
    nodes = {EXPOSURE: exp_cdl, WHITE_BALANCE: wb_cdl, CONTRAST: con_cdl, SATURATION: sat_cdl}
    return Correction(nodes=nodes, confidence=confidence, flags=flags, values=values)


# ------------------------------------------------------------------------------------------- simulator

class _Sim:
    """The clip's samples through a node chain and the output LUT."""

    def __init__(self, frames, output_lut, clip_level=None, tolerance=0.0):
        self.frames, self.output_lut = frames, output_lut
        # pixels the camera clipped are white whatever the grade does; they don't count for the white point
        self.unclipped = [np.ones(f.shape[:2], bool) if clip_level is None
                          else ~(f >= clip_level - tolerance).any(-1) for f in frames]

    def display(self, nodes):
        return [p.apply_lut(p.apply_nodes(f, nodes), self.output_lut) for f in self.frames]

    def luma_percentiles(self, nodes, q=(0.5, 99.5)):
        values = []
        for d, keep in zip(self.display(nodes), self.unclipped):
            y = p.luminance(d)
            values.append(np.percentile(y[keep] if keep.sum() > 16 else y, q))
        return np.median(np.array(values), axis=0)

    def mean_chroma(self, nodes):
        out = []
        for d in self.display(nodes):
            y = p.luminance(d)
            usable = (y > 0.1) & (y < 0.9)
            if usable.any():
                out.append(float(p.chroma(p.display_to_lab(d))[usable].mean()))
        return float(np.median(out)) if out else 0.0

    def grey_luma(self, di_value: float) -> float:
        """Display luma of a neutral DaVinci Intermediate value."""
        return float(p.luminance(p.apply_lut(np.full(3, di_value), self.output_lut)))


def _bisect(fn, target: float, lo: float, hi: float, steps: int = 40) -> float:
    """x in [lo, hi] with fn(x) ≈ target, for a monotonic fn."""
    f_lo, f_hi = fn(lo), fn(hi)
    if (f_lo - target) * (f_hi - target) > 0:
        return lo if abs(f_lo - target) < abs(f_hi - target) else hi
    rising = f_hi > f_lo
    for _ in range(steps):
        mid = (lo + hi) / 2
        if (fn(mid) < target) == rising:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# -------------------------------------------------------------------------------------------- 01 exposure

def _exposure(m: ms.Measurement, sim: _Sim, settings: dict, values: dict):
    ex, conf, fl = settings["exposure"], settings["confidence"], settings["flags"]
    key = m.exposure_stops
    confidence = 1.0

    # where the frame's key should end up: grey, moved by how far the highlights are above the key. Experts place
    # a bright, low-contrast scene (beach, haze) above grey and a scene with bright highlights over a darker
    # subject (backlight, a window) below it: on MIT-Adobe FiveK (expert C, 1,352 raws) the final key follows
    # −0.64 × the headroom (R² 0.54; the other four experts −0.56 … −0.65), scripts/fivek_targets.py
    target, reason = 0.0, "grey"
    headroom = _headroom(m, sim)
    if ex["headroom_weight"] and headroom is not None:
        t = ex["headroom_weight"] * (ex["headroom_typical"] - headroom)
        target = max(-ex["headroom_max"], min(ex["headroom_max"], t))
        if abs(target) >= 0.05:
            reason = "by the highlights"
    values["exposure_headroom"] = headroom
    ev = m.ev100
    if ms.is_night(m, fl):
        target, reason = ex["night_target"], "night"
    elif ev is not None and ev < ex["ev_full"] and key < 0:
        t = (ev - ex["ev_low"]) / (ex["ev_full"] - ex["ev_low"])
        target, reason = ex["ev_low_target"] * (1 - min(max(t, 0.0), 1.0)), "dusk / interior"
    stops = target - key

    if reason == "night":                       # only brighten night that is clearly too dark even for night
        stops = stops if key < ex["night_target"] - ex["night_margin"] else 0.0
        stops = max(stops, 0.0)
        confidence -= conf["night"]
    if m.high_key:
        stops = min(0.0, ex["high_key_max"] - key)
        reason = "high key"
        confidence -= conf["key"]
    elif m.low_key:
        stops = max(0.0, ex["low_key_min"] - key)
        reason = "low key"
        confidence -= conf["key"]

    # skin keeps the frame's key honest: after the key correction it should sit at skin_ire; the key decides,
    # skin may nudge by up to skin_max_nudge (a beige wall found as "skin" can't run the exposure)
    if m.skin_stops is not None and reason in ("grey", "by the highlights", "dusk / interior"):
        lo, hi = (v / 100.0 for v in ex["skin_ire"])
        skin_di = float(p.to_log(0.18 * 2.0 ** m.skin_stops)) + exposure_cdl(m, stops).offset[0]
        now = sim.grey_luma(skin_di)
        if now < lo or now > hi:
            extra = _bisect(lambda o: sim.grey_luma(skin_di + o), lo if now < lo else hi, -1.0, 1.0)
            nudge = _offset_to_stops(float(p.to_log(0.18 * 2.0 ** (key + stops))), extra)
            if abs(nudge) > ex["skin_disagree"]:
                confidence -= conf["skin_disagree"]
            nudge = max(-ex["skin_max_nudge"], min(ex["skin_max_nudge"], nudge))
            values["exposure_skin_nudge"] = nudge
            stops, reason = stops + nudge, reason + ", nudged by skin"

    if m.exposure_spread > fl["exposure_spread"]:
        confidence -= conf["changes"]
    stops += settings.get("learned", {}).get("exposure", 0.0)      # the user's taste (concept §13)
    # log footage exposed to the right can come down a long way; lifting underexposure lifts noise
    limited = not (-ex["max_stops_down"] <= stops <= ex["max_stops_up"])
    stops = max(-ex["max_stops_down"], min(ex["max_stops_up"], stops))

    # brighten only while the highlights have room: a dark sea under bright rocks stays a dark sea
    ceiling = ex["max_stops_up"]
    if stops > 0:
        white_cap = max(settings["contrast"]["white_max"], sim.luma_percentiles([])[1])

        def white(st):
            return sim.luma_percentiles([exposure_cdl(m, st)])[1]
        if white(stops) > white_cap:
            ceiling = max(0.0, _bisect(white, white_cap, 0.0, stops))
            stops, reason = ceiling, reason + ", held by highlights"
    key_di = float(p.to_log(0.18 * 2.0 ** key))
    offset = exposure_cdl(m, stops).offset[0]

    # don't turn clipped whites grey: the clip level must stay at or above display white
    if m.clipped_fraction > fl["clipped"]:
        confidence -= conf["clipped"]
        if m.clip_level is not None and offset < 0:
            white_di = _bisect(sim.grey_luma, ex["clipped_white"], 0.0, 1.2)
            allowed = min(white_di - m.clip_level, 0.0)
            if offset < allowed:
                offset, reason = allowed, reason + ", held by clipped highlights"
                stops = _offset_to_stops(key_di, offset)
    # the range scene matching may move this shot in (scenes.py), with the same limits
    floor = -ex["max_stops_down"]
    if "held by clipped" in reason:
        floor = stops
    values.update(exposure_stops=stops, exposure_target=target, exposure_reason=reason, exposure_key=key,
                  exposure_range=[floor, ceiling])
    return p.Cdl(offset=(offset, offset, offset)), _clamp(confidence), limited


def _headroom(m: ms.Measurement, sim: _Sim) -> float | None:
    """Stops from the key up to the 99.5th percentile (as it comes out of the camera), None if unknown."""
    if not (0.0 < m.white_point < 0.999):
        return None
    white_di = _bisect(sim.grey_luma, m.white_point, -0.1, 1.2)
    return math.log2(max(float(p.to_linear(white_di)), 1e-6) / 0.18) - m.exposure_stops


def exposure_cdl(m: ms.Measurement, stops: float) -> p.Cdl:
    """Node 01 moving the frame's key by `stops`, exact at the key (a log offset is a stop only above the toe)."""
    key = m.exposure_stops
    offset = float(p.to_log(0.18 * 2.0 ** (key + stops)) - p.to_log(0.18 * 2.0 ** key))
    return p.Cdl(offset=(offset, offset, offset))


def _offset_to_stops(di_value: float, offset: float) -> float:
    """How many stops an offset is at a given DaVinci Intermediate level."""
    before = float(p.to_linear(di_value))
    after = float(p.to_linear(di_value + offset))
    return math.log2(max(after, 1e-9) / max(before, 1e-9))


# ---------------------------------------------------------------------------------------- 02 white balance

def _strength(cct: float, table) -> float:
    for kelvin, strength in table:
        if cct >= kelvin:
            return float(strength)
    return float(table[-1][1])


def _white_balance(m: ms.Measurement, settings: dict, values: dict, exposure: p.Cdl):
    """Offsets that are exact for a neutral surface at the frame's key, as node 01 leaves it."""
    conf, fl = settings["confidence"], settings["flags"]
    cct, duv = m.cct, m.duv
    new_cct, new_duv, limited = wb_target(cct, duv, settings)
    cdl, gains = white_balance_cdl(m, exposure, new_cct, new_duv)

    # estimators always disagree a little on real scenes; only what goes beyond the free part costs confidence
    if m.wb_agreement is not None:
        confidence = 1.0 - conf["wb_disagree_per_degree"] * max(0.0, m.wb_agreement - conf["wb_disagree_free"])
    else:
        confidence = 1.0 - conf["wb_spread_per_degree"] * max(0.0, m.wb_spread - conf["wb_spread_free"])
    confidence -= conf["achromatic"] * (m.achromatic_fraction < fl["achromatic_min"])
    confidence -= conf["dominant"] * (m.dominant_fraction > fl["dominant"])
    confidence -= conf["mixed_light"] * (m.mixed_light > fl["mixed_light"])
    confidence -= conf["changes"] * (m.mired_spread > fl["mired_spread"])
    values.update(cct_before=cct, duv_before=duv, cct_after=float(new_cct), duv_after=float(new_duv),
                  wb_gains=[float(g) for g in gains])
    return cdl, _clamp(confidence), limited


def wb_target(cct: float, duv: float, settings: dict) -> tuple[float, float, bool]:
    """Where a light of (cct, duv) should end up: CCT partly towards neutral, Duv fully up to max_duv.
    Returns (cct, duv, limited)."""
    wb = settings["white_balance"]
    neutral_duv = p.cct_duv(p.dwg_to_xy(np.ones(3)))[1]
    new_cct = cct + _strength(cct, wb["cct_strength"]) * (wb["neutral_cct"] - cct)
    new_cct = min(max(new_cct + settings.get("learned", {}).get("kelvin", 0.0), 1800.0), 20000.0)
    duv_error = duv - neutral_duv
    limited = abs(duv_error) > wb["max_duv"]
    new_duv = duv - math.copysign(min(abs(duv_error), wb["max_duv"]), duv_error)
    return float(new_cct), float(new_duv), limited


def white_balance_cdl(m: ms.Measurement, exposure: p.Cdl, cct: float, duv: float, illuminant=None):
    """Node 02 turning the light (default: the measured illuminant) into the white (cct, duv).

    Exact for a neutral surface at the frame's key, as node 01 leaves it. Returns (Cdl, linear gains).
    """
    neutral_xy = p.dwg_to_xy(np.ones(3))
    neutral_cct, neutral_duv = p.cct_duv(neutral_xy)
    if abs(cct - neutral_cct) < 1.0 and abs(duv - neutral_duv) < 1e-6:
        target_xy = neutral_xy
    else:
        target_xy = p.cct_duv_to_xy(cct, duv)
    white = p.xy_to_dwg(target_xy)
    white = white / float(p.dwg_luminance(white))
    light = np.asarray(m.illuminant if illuminant is None else illuminant, dtype="float64")
    light = light / float(p.dwg_luminance(light))
    key = 0.18 * 2.0 ** m.exposure_stops
    after_01 = p.apply_cdl(p.to_log(key * light), exposure)
    level = float(p.dwg_luminance(p.to_linear(after_01)))
    offsets = p.to_log(level * white) - after_01
    return p.Cdl(offset=tuple(float(o) for o in offsets)), white / light


# -------------------------------------------------------------------------------------------- 03 contrast

def _contrast(m: ms.Measurement, sim: _Sim, before: list, settings: dict, values: dict):
    """Black to the target band, around grey; when the whites won't allow that, the pivot moves up towards them
    (a two-point levels fit) and the mids come down, at most max_grey_shift stops."""
    co, conf = settings["contrast"], settings["confidence"]
    lo_c, hi_c = co["range"]
    shift = settings.get("learned", {}).get("black", 0.0)
    black_lo, black_hi = (max(0.0, v + shift) for v in co["black"])

    def node(c, pivot=p.GREY):
        return p.Cdl(slope=(c, c, c), offset=(pivot * (1.0 - c),) * 3)

    def pct(c, pivot=p.GREY):
        return sim.luma_percentiles(before + [node(c, pivot)])

    black, white = pct(1.0)
    values.update(black_before=float(black), white_before=float(white))
    c, pivot = 1.0, p.GREY
    goal = None
    if not (black_lo <= black <= black_hi):
        goal = black_hi if black > black_hi else black_lo
        c = _bisect(lambda x: pct(x)[0], goal, 0.5, 2.5)          # black falls as contrast rises
    # whites may rise into the output LUT's soft shoulder, up to white_ceiling (or stay where the camera put them)
    white_cap = max(white, co["white_ceiling"])
    if c > 1.0 and pct(c)[1] > white_cap:
        # levels on the neutral axis: the display black and white as DaVinci Intermediate values, mapped linearly
        def di(y):
            return _bisect(sim.grey_luma, y, -0.1, 1.2)
        b0, w0, bt, wt = di(black), di(white), di(goal), di(white_cap)
        c2 = (wt - bt) / max(w0 - b0, 1e-6)
        pivot2 = (bt - c2 * b0) / (1.0 - c2) if abs(1.0 - c2) > 1e-6 else p.GREY
        # the mids may come down only so far: then the black stays higher than its goal
        max_drop = co["max_grey_shift"] * p.STOP
        if (1.0 - c2) * (pivot2 - p.GREY) < -max_drop:
            grey_to = p.GREY - max_drop
            c2 = (wt - grey_to) / max(w0 - p.GREY, 1e-6)
            pivot2 = (grey_to - c2 * p.GREY) / (1.0 - c2) if abs(1.0 - c2) > 1e-6 else p.GREY
        if c2 > 1.0:
            c, pivot = c2, pivot2
        else:
            c = min(c, _bisect(lambda x: pct(x)[1], white_cap, 1.0, c))
    confidence = 1.0
    if m.haze:
        c = 1.0 + (c - 1.0) * co["haze_factor"]
        confidence -= conf["haze"]
    if m.high_key or m.low_key:
        confidence -= conf["key"]
    limited = not (lo_c <= c <= hi_c)
    c = min(max(c, lo_c), hi_c)
    black_after, white_after = pct(c, pivot)
    values.update(contrast=float(c), contrast_pivot=float(pivot), black_after=float(black_after),
                  white_after=float(white_after),
                  grey_shift_stops=float((1.0 - c) * (pivot - p.GREY) / p.STOP))
    return node(c, pivot), _clamp(confidence), limited


# ------------------------------------------------------------------------------------------ 04 saturation

def _saturation(m: ms.Measurement, sim: _Sim, before: list, settings: dict, values: dict):
    sa, conf, fl = settings["saturation"], settings["confidence"], settings["flags"]
    lo_s, hi_s = sa["range"]
    scale = settings.get("learned", {}).get("chroma", 1.0)
    lo_c, hi_c = (v * scale for v in sa["chroma"])

    def chroma(s):
        return sim.mean_chroma(before + [p.Cdl.saturation(s)])

    now = chroma(1.0)
    sat = 1.0
    if now < lo_c and now > 0:
        sat = _bisect(chroma, lo_c, 1.0, 2.0)
    elif now > hi_c:
        sat = min(1.0, _bisect(chroma, hi_c, 0.5, 1.0))           # never boost an already colourful frame
    limited = not (lo_s <= sat <= hi_s)
    sat = min(max(sat, lo_s), hi_s)
    confidence = 1.0 - conf["dominant"] * (m.dominant_fraction > fl["dominant"])
    values.update(chroma_before=now, saturation=float(sat), chroma_after=chroma(sat))
    return p.Cdl.saturation(sat), _clamp(confidence), limited


def _clamp(v: float) -> float:
    return float(min(max(v, 0.0), 1.0))
