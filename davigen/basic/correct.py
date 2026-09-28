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
    if exp_limited or wb_limited or con_limited or sat_limited:
        flags.append(LIMIT)
    confidence = {
        EXPOSURE: _clamp(exp_conf - conf["limit"] * exp_limited),
        WHITE_BALANCE: _clamp(wb_conf - conf["limit"] * wb_limited),
        CONTRAST: _clamp(con_conf - conf["limit"] * con_limited),
        SATURATION: _clamp(sat_conf - conf["limit"] * sat_limited),
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

    # where the frame's key should end up
    target, reason = 0.0, "grey"
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

    # skin takes priority when there is enough of it (not for high/low key or night)
    if m.skin_stops is not None and m.skin_ire is not None and reason in ("grey", "dusk / interior"):
        lo, hi = (v / 100.0 for v in ex["skin_ire"])
        skin_di = float(p.to_log(0.18 * 2.0 ** m.skin_stops))
        now = sim.grey_luma(skin_di)
        if now < lo or now > hi:
            goal = lo if now < lo else hi
            offset = _bisect(lambda o: sim.grey_luma(skin_di + o), goal, -0.5, 0.5)
            skin_stops = _offset_to_stops(skin_di, offset)
        else:
            skin_stops = 0.0
        values["exposure_from_skin"] = skin_stops
        values["exposure_from_key"] = stops
        if abs(skin_stops - stops) > ex["skin_disagree"]:
            confidence -= conf["skin_disagree"]
        stops, reason = skin_stops, "skin"

    if m.exposure_spread > fl["exposure_spread"]:
        confidence -= conf["changes"]
    limited = abs(stops) > ex["max_stops"]
    stops = max(-ex["max_stops"], min(ex["max_stops"], stops))
    key_di = float(p.to_log(0.18 * 2.0 ** key))
    offset = float(p.to_log(0.18 * 2.0 ** (key + stops)) - key_di)      # exact at the key, like a real stop

    # don't turn clipped whites grey: the clip level must stay at or above display white
    if m.clipped_fraction > fl["clipped"]:
        confidence -= conf["clipped"]
        if m.clip_level is not None and offset < 0:
            white_di = _bisect(sim.grey_luma, settings["contrast"]["white_max"], 0.0, 1.2)
            allowed = min(white_di - m.clip_level, 0.0)
            if offset < allowed:
                offset, reason = allowed, reason + ", held by clipped highlights"
                stops = _offset_to_stops(key_di, offset)
    values.update(exposure_stops=stops, exposure_target=target, exposure_reason=reason, exposure_key=key)
    return p.Cdl(offset=(offset, offset, offset)), _clamp(confidence), limited


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
    wb, conf, fl = settings["white_balance"], settings["confidence"], settings["flags"]
    neutral_xy = p.dwg_to_xy(np.ones(3))
    neutral_cct, neutral_duv = p.cct_duv(neutral_xy)
    cct, duv = m.cct, m.duv
    new_cct = cct + _strength(cct, wb["cct_strength"]) * (wb["neutral_cct"] - cct)
    duv_error = duv - neutral_duv
    limited = abs(duv_error) > wb["max_duv"]
    new_duv = duv - math.copysign(min(abs(duv_error), wb["max_duv"]), duv_error)   # Duv fully, up to max_duv
    if abs(new_cct - neutral_cct) < 1.0 and abs(new_duv - neutral_duv) < 1e-6:
        target_xy = neutral_xy
    else:
        target_xy = p.cct_duv_to_xy(new_cct, new_duv)
    white = p.xy_to_dwg(target_xy)
    white = white / float(p.dwg_luminance(white))
    illuminant = np.asarray(m.illuminant, dtype="float64")
    illuminant = illuminant / float(p.dwg_luminance(illuminant))
    # a neutral surface at the key, after node 01, lands on the target white at the same luminance
    key = 0.18 * 2.0 ** m.exposure_stops
    after_01 = p.apply_cdl(p.to_log(key * illuminant), exposure)
    level = float(p.dwg_luminance(p.to_linear(after_01)))
    offsets = p.to_log(level * white) - after_01

    confidence = 1.0 - conf["wb_spread_per_degree"] * m.wb_spread
    confidence -= conf["achromatic"] * (m.achromatic_fraction < fl["achromatic_min"])
    confidence -= conf["dominant"] * (m.dominant_fraction > fl["dominant"])
    confidence -= conf["mixed_light"] * (m.mixed_light > fl["mixed_light"])
    confidence -= conf["changes"] * (m.cct_spread > fl["cct_spread"])
    gains = white / illuminant
    values.update(cct_before=cct, duv_before=duv, cct_after=float(new_cct), duv_after=float(new_duv),
                  wb_gains=[float(g) for g in gains])
    return p.Cdl(offset=tuple(float(o) for o in offsets)), _clamp(confidence), limited


# -------------------------------------------------------------------------------------------- 03 contrast

def _contrast(m: ms.Measurement, sim: _Sim, before: list, settings: dict, values: dict):
    co, conf = settings["contrast"], settings["confidence"]
    lo_c, hi_c = co["range"]
    black_lo, black_hi = co["black"]

    def pct(c):
        return sim.luma_percentiles(before + [p.Cdl.contrast(c)])

    black, white = pct(1.0)
    values.update(black_before=float(black), white_before=float(white))
    c = 1.0
    if not (black_lo <= black <= black_hi):
        goal = black_hi if black > black_hi else black_lo
        c = _bisect(lambda x: pct(x)[0], goal, 0.5, 2.0)          # black falls as contrast rises
    # whites stay under white_max; if the camera already put them above it, they may go up to white_ceiling
    white_cap = co["white_max"] if white <= co["white_max"] else max(white, co["white_ceiling"])
    if c > 1.0 and pct(c)[1] > white_cap:
        c = min(c, _bisect(lambda x: pct(x)[1], white_cap, 1.0, c))
    confidence = 1.0
    if m.haze:
        c = 1.0 + (c - 1.0) * co["haze_factor"]
        confidence -= conf["haze"]
    if m.high_key or m.low_key:
        confidence -= conf["key"]
    limited = not (lo_c <= c <= hi_c)
    c = min(max(c, lo_c), hi_c)
    black_after, white_after = pct(c)
    values.update(contrast=float(c), black_after=float(black_after), white_after=float(white_after))
    return p.Cdl.contrast(c), _clamp(confidence), limited


# ------------------------------------------------------------------------------------------ 04 saturation

def _saturation(m: ms.Measurement, sim: _Sim, before: list, settings: dict, values: dict):
    sa, conf, fl = settings["saturation"], settings["confidence"], settings["flags"]
    lo_s, hi_s = sa["range"]
    lo_c, hi_c = sa["chroma"]

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
