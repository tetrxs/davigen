"""Turn a clip's sample frames into numbers and flags (concept §5 and §7), independent of Resolve.

Input: small thumbnails in DWG / DaVinci Intermediate, exactly what Group Pre-Clip hands to node 01, plus the
clip's metadata. Every threshold comes from the [basic_correction.measure] and [basic_correction.flags] settings.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from . import pipeline as p

GREY_LINEAR = 0.18

# flag names, shared with correct.py and the report
CLIPPED = "clipped highlights"
NO_NEUTRAL = "no neutral surfaces"
DOMINANT = "dominant colour"
MIXED_LIGHT = "mixed light"
CHANGES = "changes within clip"
HIGH_KEY = "high key"
LOW_KEY = "low key"
NIGHT = "night"
HAZE = "haze"
SKIN_OFF = "skin off the skin line"


@dataclass
class ClipMeta:
    """What the camera recorded. Zero / empty means unknown."""
    iso: float = 0.0
    fnumber: float = 0.0
    exposure_time: float = 0.0
    white_balance: str = ""
    kelvin: int = 0
    created: str = ""

    @classmethod
    def from_clip_info(cls, info) -> "ClipMeta":
        """From a scanner.ClipInfo."""
        try:
            iso = float(str(info.iso).split()[0]) if info.iso else 0.0
        except ValueError:
            iso = 0.0
        return cls(iso=iso, fnumber=info.fnumber, exposure_time=info.exposure_time,
                   white_balance=info.white_balance, kelvin=info.kelvin, created=info.created)

    def ev100(self) -> float | None:
        """Scene brightness: EV100 = log2(N² / t) − log2(ISO / 100). None if anything is missing."""
        if self.fnumber <= 0 or self.exposure_time <= 0 or self.iso <= 0:
            return None
        return math.log2(self.fnumber ** 2 / self.exposure_time) - math.log2(self.iso / 100.0)


@dataclass
class Sample:
    """Measurements of one frame."""
    exposure_stops: float = 0.0         # log-average luminance relative to 18 % grey, linear DWG
    clipped_fraction: float = 0.0
    black_fraction: float = 0.0
    skin_fraction: float = 0.0
    skin_ire: float | None = None       # median skin luma after the output LUT
    skin_stops: float | None = None     # median skin luminance relative to 18 % grey, linear DWG
    skin_hue: float | None = None       # degrees on the vectorscope
    high_key: bool = False
    low_key: bool = False
    illuminant: list = field(default_factory=lambda: [1.0, 1.0, 1.0])   # linear DWG, luminance 1
    wb_spread: float = 0.0              # largest angle between the four estimators
    achromatic_fraction: float = 0.0
    dominant_fraction: float = 0.0
    mixed_light: float = 0.0            # largest angle between left/right or top/bottom estimates
    cct: float = 6500.0
    duv: float = 0.0
    black_point: float = 0.0            # 0.5th percentile of display luma
    white_point: float = 1.0            # 99.5th percentile of display luma
    range_stops: float = 0.0
    local_contrast: float = 0.0
    chroma: float = 0.0                 # mean CIELAB C* of the display image


@dataclass
class Measurement:
    """A clip: the median over its samples, the spread across them, and the flags."""
    samples: int
    ev100: float | None
    exposure_stops: float
    exposure_spread: float
    clipped_fraction: float
    black_fraction: float
    skin_fraction: float
    skin_ire: float | None
    skin_stops: float | None
    skin_hue: float | None
    high_key: bool
    low_key: bool
    illuminant: list
    wb_spread: float
    achromatic_fraction: float
    dominant_fraction: float
    mixed_light: float
    cct: float
    duv: float
    cct_spread: float
    black_point: float
    white_point: float
    range_stops: float
    local_contrast: float
    chroma: float
    haze: bool
    flags: list[str] = field(default_factory=list)
    per_sample: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return _plain(asdict(self))


# ------------------------------------------------------------------------------------------ entry point

def measure(samples: list, meta: ClipMeta, output_lut: str | Path, settings: dict) -> Measurement:
    """Measure one clip. samples: DWG/Intermediate thumbnails (h, w, 3); settings: settings.load(...)."""
    if not samples:
        raise ValueError("measure() needs at least one sample")
    ms, fl = settings["measure"], settings["flags"]
    frames = [np.asarray(s, dtype="float64")[..., :3] for s in samples]
    clip_level = _clip_level(frames, ms)
    per = [_sample(f, p.apply_lut(f, output_lut), clip_level, ms) for f in frames]

    def med(name):
        return float(np.median([getattr(s, name) for s in per]))

    def med_opt(name):
        values = [getattr(s, name) for s in per if getattr(s, name) is not None]
        return float(np.median(values)) if values else None

    illuminant = np.median([s.illuminant for s in per], axis=0)
    illuminant = illuminant / max(float(p.dwg_luminance(illuminant)), 1e-9)
    cct, duv = p.cct_duv(p.dwg_to_xy(illuminant))
    haze_cfg = ms["haze"]
    m = Measurement(
        samples=len(per), ev100=meta.ev100(),
        exposure_stops=med("exposure_stops"),
        exposure_spread=float(np.ptp([s.exposure_stops for s in per])),
        clipped_fraction=med("clipped_fraction"), black_fraction=med("black_fraction"),
        skin_fraction=med("skin_fraction"), skin_ire=med_opt("skin_ire"), skin_stops=med_opt("skin_stops"),
        skin_hue=med_opt("skin_hue"),
        high_key=sum(s.high_key for s in per) * 2 > len(per), low_key=sum(s.low_key for s in per) * 2 > len(per),
        illuminant=[float(v) for v in illuminant], wb_spread=med("wb_spread"),
        achromatic_fraction=med("achromatic_fraction"), dominant_fraction=med("dominant_fraction"),
        mixed_light=med("mixed_light"), cct=cct, duv=duv, cct_spread=float(np.ptp([s.cct for s in per])),
        black_point=med("black_point"), white_point=med("white_point"), range_stops=med("range_stops"),
        local_contrast=med("local_contrast"), chroma=med("chroma"), haze=False,
        per_sample=[_plain(asdict(s)) for s in per],
    )
    m.haze = m.range_stops < haze_cfg["max_range_stops"] and m.local_contrast < haze_cfg["max_local_contrast"]
    m.flags = flags(m, ms, fl)
    return m


def is_night(m: Measurement, fl: dict) -> bool:
    """Night needs both: a low EV100 from the metadata and a dark picture. Video is often shot through an ND
    filter, which the metadata doesn't know, so a low EV100 on a bright picture means ND, not night."""
    return m.ev100 is not None and m.ev100 < fl["night_ev"] and m.exposure_stops < 0.0


def flags(m: Measurement, ms: dict, fl: dict) -> list[str]:
    """The concept §7 flags a measurement alone can decide (correct.py adds 'limit reached')."""
    out = []
    if m.clipped_fraction > fl["clipped"]:
        out.append(CLIPPED)
    if m.wb_spread > fl["wb_spread"] or m.achromatic_fraction < fl["achromatic_min"]:
        out.append(NO_NEUTRAL)
    if m.dominant_fraction > fl["dominant"]:
        out.append(DOMINANT)
    if m.mixed_light > fl["mixed_light"]:
        out.append(MIXED_LIGHT)
    if m.exposure_spread > fl["exposure_spread"] or m.cct_spread > fl["cct_spread"]:
        out.append(CHANGES)
    if m.high_key:
        out.append(HIGH_KEY)
    if m.low_key:
        out.append(LOW_KEY)
    if is_night(m, fl):
        out.append(NIGHT)
    if m.haze:
        out.append(HAZE)
    if m.skin_hue is not None and abs(_hue_diff(m.skin_hue, ms["skin"]["line"])) > ms["skin"]["max_off"]:
        out.append(SKIN_OFF)
    return out


# ------------------------------------------------------------------------------------------- per sample

def _clip_level(frames: list, ms: dict) -> np.ndarray | None:
    """Per channel, the value the camera clipped at, if enough of the clip sits on it (a flat plateau)."""
    stack = np.concatenate([f.reshape(-1, 3) for f in frames])
    top = stack.max(axis=0)
    on_top = np.abs(stack - top) <= ms["clip_tolerance"]
    plateau = on_top.mean(axis=0) >= ms["clip_min_fraction"]
    bright = top > p.GREY + 2 * p.STOP          # a plateau in the shadows is black, not a clip
    level = np.where(plateau & bright, top, np.inf)
    return None if np.isinf(level).all() else level


def _sample(di: np.ndarray, display: np.ndarray, clip_level, ms: dict) -> Sample:
    h, w = di.shape[:2]
    lin = p.to_linear(di)
    lum = np.maximum(p.dwg_luminance(lin), 1e-6)
    clipped = (np.zeros((h, w), bool) if clip_level is None
               else (di >= clip_level - ms["clip_tolerance"]).any(-1))
    black = lum < GREY_LINEAR * 2.0 ** -ms["black_stops"]
    valid = ~clipped & ~black
    s = Sample(clipped_fraction=float(clipped.mean()), black_fraction=float(black.mean()))
    lk = ms["low_key"]                          # silhouette: much of the frame near black against bright light
    dark = lum < GREY_LINEAR * 2.0 ** -4
    bright = clipped | (lum > GREY_LINEAR * 4.0)
    s.low_key = bool(dark.mean() > lk["min_black_fraction"] and bright.mean() > lk["min_bright_fraction"])
    if valid.sum() < 16:                        # nothing usable: a black frame or all sky
        s.illuminant = [1.0, 1.0, 1.0]
        s.cct, s.duv = p.cct_duv(p.dwg_to_xy(np.ones(3)))
        _contrast(s, di, display, lum, valid)
        return s

    # exposure: centre-weighted log average
    yy, xx = np.mgrid[0:h, 0:w]
    sigma = ms["centre_sigma"]
    weight = np.exp(-(((xx + 0.5) / w - 0.5) ** 2 + ((yy + 0.5) / h - 0.5) ** 2) / (2 * sigma ** 2))
    key = math.exp(float((weight * np.log(lum))[valid].sum() / weight[valid].sum()))
    s.exposure_stops = math.log2(key / GREY_LINEAR)

    # skin, on the display image like a vectorscope
    y, cb, cr = p.ycbcr(display)
    hue, chroma = p.vectorscope_hue(cb, cr), np.hypot(cb, cr)
    sk = ms["skin"]
    skin = (valid & _hue_in(hue, *sk["hue"]) & (chroma >= sk["chroma"][0]) & (chroma <= sk["chroma"][1])
            & (y >= sk["luma"][0]) & (y <= sk["luma"][1]))
    s.skin_fraction = float(skin.mean())
    if s.skin_fraction >= sk["min_fraction"]:
        s.skin_ire = float(p.ire(np.median(y[skin])))
        s.skin_stops = math.log2(float(np.median(lum[skin])) / GREY_LINEAR)
        s.skin_hue = _circular_mean(hue[skin])

    # high key / low key
    lab = p.display_to_lab(display)
    c_star = p.chroma(lab)
    shadows = lum < GREY_LINEAR * 2.0 ** -2
    hk = ms["high_key"]
    s.high_key = bool(s.exposure_stops > hk["min_stops"] and shadows.mean() < hk["max_shadow_fraction"]
                      and float(c_star[valid].mean()) < hk["max_chroma"])

    # white balance on the midtones around the frame's key
    mid = valid & (np.abs(np.log2(lum / key)) < ms["midtone_stops"])
    if mid.sum() < 16:
        mid = valid
    _white_balance(s, lin, mid, valid, ms["white_balance"])
    s.dominant_fraction = _dominant(hue, chroma, valid, ms["dominant_hue"])
    _contrast(s, di, display, lum, valid)
    usable = valid & (y > 0.1) & (y < 0.9)
    s.chroma = float(c_star[usable].mean()) if usable.any() else 0.0
    return s


def _contrast(s: Sample, di, display, lum, valid) -> None:
    y = p.luminance(display)
    s.black_point, s.white_point = (float(v) for v in np.percentile(y, [0.5, 99.5]))
    if valid.any():
        lo, hi = np.percentile(lum[valid], [0.5, 99.5])
        s.range_stops = float(math.log2(max(hi, 1e-6) / max(lo, 1e-6)))
    gy, gx = np.gradient(di.mean(-1))
    s.local_contrast = float(np.hypot(gx, gy).mean() * di.shape[1] / 96.0)   # per 1/96 of the width


# ------------------------------------------------------------------------------------------ white balance

def _norm(rgb) -> np.ndarray:
    rgb = np.maximum(np.asarray(rgb, dtype="float64"), 1e-9)
    return rgb / max(float(p.dwg_luminance(rgb)), 1e-9)


def _estimators(lin: np.ndarray, mask: np.ndarray, wb: dict) -> list:
    px = np.maximum(lin[mask], 0.0)
    grey_world = px.mean(0)
    shades = (px ** wb["minkowski_p"]).mean(0) ** (1.0 / wb["minkowski_p"])
    grads = np.stack([np.hypot(*np.gradient(lin[..., c])) for c in range(3)], -1)[mask]
    edge = (grads ** wb["edge_p"]).mean(0) ** (1.0 / wb["edge_p"])
    estimate = _norm(np.median([_norm(grey_world), _norm(shades)], axis=0))
    unit = px / np.maximum(np.linalg.norm(px, axis=1, keepdims=True), 1e-9)
    keep = max(int(len(px) * wb["achromatic_top"]), 1)
    for _ in range(wb["iterations"]):           # pixels closest to neutral under the current estimate
        e = estimate / np.linalg.norm(estimate)
        closest = np.argsort(-(unit @ e))[:keep]
        estimate = _norm(px[closest].mean(0))
    return [_norm(grey_world), _norm(shades), _norm(edge), estimate]


def _white_balance(s: Sample, lin, mid, valid, wb: dict) -> None:
    ests = _estimators(lin, mid, wb)
    illuminant = _norm(np.median(ests, axis=0))
    s.illuminant = [float(v) for v in illuminant]
    s.wb_spread = max(p.angle_deg(a, b) for i, a in enumerate(ests) for b in ests[i + 1:])
    px = np.maximum(lin[valid], 1e-9)
    e = illuminant / np.linalg.norm(illuminant)
    cos = np.clip((px @ e) / np.linalg.norm(px, axis=1), -1.0, 1.0)
    s.achromatic_fraction = float((np.degrees(np.arccos(cos)) < wb["achromatic_max_angle"]).sum() / valid.size)
    s.cct, s.duv = p.cct_duv(p.dwg_to_xy(illuminant))

    h, w = mid.shape
    halves = [(np.s_[:, : w // 2], np.s_[:, w // 2:]), (np.s_[: h // 2, :], np.s_[h // 2:, :])]
    angles = []
    for a, b in halves:
        ma, mb = np.zeros_like(mid), np.zeros_like(mid)
        ma[a], mb[b] = mid[a], mid[b]
        if ma.sum() >= 16 and mb.sum() >= 16:
            ea = _norm(np.median(_estimators(lin, ma, wb)[:2], axis=0))
            eb = _norm(np.median(_estimators(lin, mb, wb)[:2], axis=0))
            angles.append(p.angle_deg(ea, eb))
    s.mixed_light = max(angles) if angles else 0.0


def _dominant(hue, chroma, valid, dh: dict) -> float:
    """Largest share of the frame whose hue falls into one window of `bin` degrees (slid in 10° steps)."""
    coloured = valid & (chroma >= dh["min_chroma"])
    if not valid.any():
        return 0.0
    hues = hue[coloured]
    best = 0
    for start in range(0, 360, 10):
        best = max(best, int(_hue_in(hues, start, start + dh["bin"]).sum()))
    return best / int(valid.sum())


# ------------------------------------------------------------------------------------------------ helpers

def _hue_in(hue, lo: float, hi: float):
    """Hue within [lo, hi) degrees, wrapping around 360."""
    return ((hue - lo) % 360.0) < ((hi - lo) % 360.0 or 360.0)


def _hue_diff(a: float, b: float) -> float:
    return (a - b + 180.0) % 360.0 - 180.0


def _circular_mean(deg) -> float:
    rad = np.radians(deg)
    return float(np.degrees(np.arctan2(np.sin(rad).mean(), np.cos(rad).mean())) % 360.0)


def _plain(value):
    """numpy scalars and arrays → plain Python, for the JSON record."""
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    return value
