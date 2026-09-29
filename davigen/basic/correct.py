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

    def tone(self, nodes):
        """A fast stand-in for the display luma under one more contrast node: each unclipped pixel as its
        luminance in DaVinci Intermediate, and the output LUT's neutral curve. Returns (values, curve x, curve y)."""
        if getattr(self, "_tone", None) is None or self._tone[0] != repr(nodes):
            grid = np.linspace(-0.2, 1.3, 1501)
            curve = p.luminance(p.apply_lut(np.repeat(grid[:, None], 3, 1), self.output_lut))
            values = []
            for f, keep in zip(self.frames, self.unclipped):
                di = p.apply_nodes(f, nodes)
                lum = np.maximum(p.dwg_luminance(p.to_linear(di)), 1e-6)
                v = p.to_log(lum)
                v = v[keep] if keep.sum() > 16 else v.ravel()
                values.append(v[:: max(1, len(v) // 1500)])       # every n-th pixel: percentiles barely move
            self._tone = (repr(nodes), values, grid, curve)
        return self._tone[1:]


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
    stops += ex.get("look_offset", 0.0)                            # the project's look (concept §15)
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
    new_cct += settings.get("learned", {}).get("kelvin", 0.0) + wb.get("look_kelvin", 0.0)
    new_cct = min(max(new_cct, 1800.0), 20000.0)
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

def _curve(v: np.ndarray, grid: np.ndarray, curve: np.ndarray) -> np.ndarray:
    """np.interp for the Sim.tone grid, which is uniform: direct indexing, several times faster."""
    pos = np.clip((v - grid[0]) / (grid[1] - grid[0]), 0.0, len(grid) - 1.000001)
    i = pos.astype(np.int64)
    f = pos - i
    return curve[i] * (1.0 - f) + curve[i + 1] * f


def tone_stats(y: np.ndarray) -> dict:
    """What the contrast is judged by, on display luma 0–1: the black and white points, and the spread of the
    tones in between (5th to 95th percentile)."""
    q = np.percentile(y, [0.5, 5, 50, 95, 99.5])
    return {"black": float(q[0]), "p5": float(q[1]), "median": float(q[2]), "p95": float(q[3]),
            "white": float(q[4]), "spread": float(q[3] - q[1]), "crushed": float((y < 0.012).mean())}


def _by_range(table, value: float) -> float:
    xs, ys = zip(*table)
    return float(np.interp(value, xs, ys))


def contrast_targets(before: dict, m: ms.Measurement, settings: dict) -> dict:
    """Where a shot's tones should end up, as experts put them (MIT-Adobe FiveK, scripts/fivek_targets.py): the
    black point and the spread of the tones (5th to 95th percentile) depend on the scene's dynamic range. A flat,
    hazy scene keeps some air in its blacks, a hard one gets deep blacks; neither is stretched to one number.
    The black band is ± black_tolerance around it, shifted by what was learned from the user's grades."""
    co = settings["contrast"]
    shift = settings.get("learned", {}).get("black", 0.0)
    black = max(0.0, _by_range(co["black_by_range"], m.range_stops) + shift)
    tol = co["black_tolerance"]
    return {"black_lo": max(0.0, black - tol), "black_hi": black + tol, "black": black,
            "spread": _by_range(co["spread_by_range"], m.range_stops) if co.get("spread_by_range") else None}


def contrast_node(k: float, grey_shift: float, power: float = 1.0) -> p.Cdl:
    """Node 03's tone curve in DaVinci Intermediate: contrast k (slope) and a toe (power: above 1 the shadows go
    down further than the mids), with grey landing grey_shift stops from where it was. SetCDL writes all three
    exactly (verified, concept §12)."""
    o = max(p.GREY + grey_shift * p.STOP, 1e-6) ** (1.0 / power) - k * p.GREY
    return p.Cdl(slope=(k,) * 3, offset=(o,) * 3, power=(power,) * 3)


def _contrast(m: ms.Measurement, sim: _Sim, before: list, settings: dict, values: dict):
    """A tone curve for node 03 – contrast, toe and a small move of the mids – so that the black point sits in
    its band and the tones between the 5th and 95th percentile span the target, without crushing the shadows or
    pushing the whites past the targets. The toe does the blacks where a steeper curve would harden the mids (a
    drone's vendor LUT already brings contrast): experts pull the blacks down, not the whole curve. A search on
    a fast tone model (Sim.tone), checked on the full simulation."""
    co, conf = settings["contrast"], settings["confidence"]
    lo_c, hi_c = co["range"]
    lo_p, hi_p = co["power_range"]
    ls, grid, curve = sim.tone(before)
    y0 = np.concatenate([_curve(v, grid, curve) for v in ls])
    start = tone_stats(y0)
    t = contrast_targets(start, m, settings)
    white_cap = max(start["white"], co["white_ceiling"])
    white_soft = max(start["white"], co["white_target"])
    crush_ok = max(start["crushed"], co["crushed_share"])

    def stats(k, g, pw):
        node = contrast_node(k, g, pw)
        o = node.offset[0]
        ys = [_curve(np.sign(k * v + o) * np.abs(k * v + o) ** pw, grid, curve) for v in ls]
        each = [tone_stats(y) for y in ys if len(y)]
        return {key: float(np.median([e[key] for e in each])) for key in each[0]} if each else start

    def cost(k, g, pw):
        st = stats(k, g, pw)
        err = 0.0
        if st["black"] > t["black_hi"]:
            err += ((st["black"] - t["black_hi"]) / 0.01) ** 2
        elif st["black"] < t["black_lo"]:
            err += ((t["black_lo"] - st["black"]) / 0.01) ** 2
        if t["spread"]:
            err += ((st["spread"] - t["spread"]) / 0.03) ** 2
        if st["crushed"] > crush_ok:                    # shadows that turn to solid black
            err += ((st["crushed"] - crush_ok) / 0.01) ** 2
        if st["white"] > white_cap:
            err += ((st["white"] - white_cap) / 0.005) ** 2
        if st["white"] > white_soft:                    # experts keep the whites under ~0.9
            err += ((st["white"] - white_soft) / 0.02) ** 2
        err += 1.0 * (k - 1.0) ** 2 + 0.3 * (pw - 1.0) ** 2 + 0.2 * g ** 2     # the least change that does it
        return err, st

    ks = np.arange(lo_c, hi_c + 1e-9, 0.08)             # coarse, then refined around the best
    pws = np.arange(lo_p, hi_p + 1e-9, 0.2)
    # around grey first: the exposure node decided the mids. Only when the whites hold the curve back and the
    # black stays above its band may the mids come down, at most max_grey_shift
    _, k, g, pw = min(((cost(k, 0.0, q)[0], k, 0.0, q) for k in ks for q in pws), key=lambda x: x[0])
    st = cost(k, 0.0, pw)[1]
    shift_allowed = co["max_grey_shift"] > 0 and st["white"] >= white_soft - 0.005 and st["black"] > t["black_hi"]
    if shift_allowed:
        gs = np.arange(-co["max_grey_shift"], 1e-9, 0.125)
        _, k, g, pw = min(((cost(kk, gg, q)[0], kk, gg, q) for kk in ks for gg in gs for q in pws), key=lambda x: x[0])
    lim_g = -co["max_grey_shift"] - 1e-9 if shift_allowed else -1e-9
    for sk, sg, sp in ((0.04, 0.06, 0.1), (0.02, 0.03, 0.05), (0.01, 0.015, 0.025), (0.005, 0.01, 0.01)):
        cand = [(cost(kk, gg, qq)[0], kk, gg, qq) for kk in (k - sk, k, k + sk) for gg in (g - sg, g, g + sg)
                for qq in (pw - sp, pw, pw + sp)
                if lo_c <= kk <= hi_c and lim_g <= gg <= 1e-9 and lo_p - 1e-9 <= qq <= hi_p + 1e-9]
        _, k, g, pw = min(cand, key=lambda x: x[0])
    confidence = 1.0
    if m.haze:
        confidence -= conf["haze"]
    if m.high_key or m.low_key:
        confidence -= conf["key"]
    limited = bool(k <= lo_c + 1e-6 or k >= hi_c - 1e-6)
    node = contrast_node(k, g, pw)
    black_after, white_after = sim.luma_percentiles(before + [node])
    end = stats(k, g, pw)
    values.update(contrast=float(k), contrast_power=float(pw), grey_shift_stops=float(g),
                  black_before=float(start["black"]), white_before=float(start["white"]),
                  black_after=float(black_after), white_after=float(white_after),
                  spread_before=float(start["spread"]), spread_after=float(end["spread"]), spread_target=t["spread"],
                  black_target=t["black"], crushed_after=float(end["crushed"]), frames_measured=len(ls))
    return node, _clamp(confidence), limited


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
