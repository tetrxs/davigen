"""What Resolve does to a clip in a davigen project, reproduced in numpy.

    camera log ──[input LUT]──▶ DWG / Intermediate ──[01–04, CDL]──▶ DWG / Intermediate ──[output LUT]──▶ Rec.709

This is the only place in davigen/basic/ that knows about LUTs and CDL. Images are float arrays (..., 3), 0–1.
The CDL model follows what the API spike measured in Resolve 21 (concept §12): slope and offset act exactly on
the values entering the node, saturation mixes with the luma weights in SAT_LUMA.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..colormath import _colour
from ..lut import Lut3D

# DaVinci Intermediate (Blackmagic's published formula)
_DI_A, _DI_B, _DI_C, _DI_M = 0.0075, 7.0, 0.07329248, 10.44426855
_DI_LIN_CUT, _DI_LOG_CUT = 0.00262409, 0.02740668

STOP = _DI_C                        # one stop in DaVinci Intermediate, exact above the toe
GREY = 0.336                        # 18 % grey in DaVinci Intermediate (0.33604)
SAT_LUMA = np.array([0.21, 0.70, 0.09])         # Resolve's saturation luma, measured in step 01
REC709_LUMA = np.array([0.2126, 0.7152, 0.0722])
DISPLAY_GAMMA = 2.4


def to_linear(y):
    """DaVinci Intermediate → linear DWG."""
    y = np.asarray(y, dtype="float64")
    return np.where(y <= _DI_LOG_CUT, y / _DI_M, 2.0 ** (y / _DI_C - _DI_B) - _DI_A)


def to_log(x):
    """Linear DWG → DaVinci Intermediate."""
    x = np.asarray(x, dtype="float64")
    return np.where(x <= _DI_LIN_CUT, x * _DI_M, (np.log2(np.maximum(x, _DI_LIN_CUT) + _DI_A) + _DI_B) * _DI_C)


def stops_to_offset(stops: float) -> float:
    return stops * STOP


def gain_to_offset(gain):
    """A linear gain as an offset in DaVinci Intermediate (per channel if gain is an array)."""
    return STOP * np.log2(gain)


# ---------------------------------------------------------------------------------------------------- CDL

@dataclass(frozen=True)
class Cdl:
    slope: tuple[float, float, float] = (1.0, 1.0, 1.0)
    offset: tuple[float, float, float] = (0.0, 0.0, 0.0)
    power: tuple[float, float, float] = (1.0, 1.0, 1.0)
    sat: float = 1.0

    @classmethod
    def exposure(cls, stops: float) -> "Cdl":
        o = stops_to_offset(stops)
        return cls(offset=(o, o, o))

    @classmethod
    def white_balance(cls, gains) -> "Cdl":
        """Per-channel linear gains (DWG primaries) as offsets."""
        r, g, b = (float(v) for v in gain_to_offset(np.asarray(gains, dtype="float64")))
        return cls(offset=(r, g, b))

    @classmethod
    def contrast(cls, c: float) -> "Cdl":
        """Contrast c around middle grey: grey stays where it is."""
        o = GREY * (1.0 - c)
        return cls(slope=(c, c, c), offset=(o, o, o))

    @classmethod
    def saturation(cls, sat: float) -> "Cdl":
        return cls(sat=sat)

    @property
    def is_identity(self) -> bool:
        return self == Cdl()

    def to_resolve(self, node: int) -> dict[str, str]:
        """The map TimelineItem.SetCDL expects: every value a string, channels space-separated."""
        def fmt(values):
            return " ".join(f"{v:.6f}".rstrip("0").rstrip(".") if v else "0" for v in values)
        return {"NodeIndex": str(node), "Slope": fmt(self.slope), "Offset": fmt(self.offset),
                "Power": fmt(self.power), "Saturation": fmt((self.sat,))}

    def to_dict(self) -> dict:
        return {"slope": list(self.slope), "offset": list(self.offset), "power": list(self.power), "sat": self.sat}


def apply_cdl(image, cdl: Cdl):
    """ASC CDL as Resolve applies it: slope, offset, power per channel, then saturation."""
    out = np.asarray(image, dtype="float64") * np.asarray(cdl.slope) + np.asarray(cdl.offset)
    if cdl.power != (1.0, 1.0, 1.0):
        # Resolve keeps the sign below 0 (−|v|^p, measured on a ramp, concept §12); ASC would clamp to 0
        out = np.sign(out) * np.abs(out) ** np.asarray(cdl.power)
    if cdl.sat != 1.0:
        luma = (out @ SAT_LUMA)[..., None]
        out = luma + cdl.sat * (out - luma)
    return out


def apply_nodes(image, nodes):
    for cdl in nodes:
        image = apply_cdl(image, cdl)
    return image


# --------------------------------------------------------------------------------------------------- LUTs

_luts: dict[tuple[str, float], np.ndarray] = {}


def _table(path: str | Path) -> np.ndarray:
    """A .cube LUT as an array [b, g, r, channel], read once per file version."""
    path = Path(path)
    key = (str(path), path.stat().st_mtime)
    if key not in _luts:
        lut = Lut3D.read(path)
        _luts[key] = np.asarray(lut.table, dtype="float64").reshape(lut.size, lut.size, lut.size, 3)
    return _luts[key]


def apply_lut(image, path: str | Path):
    """Trilinear 3D LUT lookup (Resolve's default interpolation). Inputs are clamped to 0–1 like Resolve does."""
    table = _table(path)
    n = table.shape[0] - 1
    v = np.clip(np.asarray(image, dtype="float64"), 0.0, 1.0) * n
    i0 = np.minimum(np.floor(v).astype(int), n - 1)
    f = v - i0
    r0, g0, b0 = i0[..., 0], i0[..., 1], i0[..., 2]
    fr, fg, fb = f[..., 0:1], f[..., 1:2], f[..., 2:3]

    def at(dr, dg, db):
        return table[b0 + db, g0 + dg, r0 + dr]

    c0 = (at(0, 0, 0) * (1 - fr) + at(1, 0, 0) * fr) * (1 - fg) + (at(0, 1, 0) * (1 - fr) + at(1, 1, 0) * fr) * fg
    c1 = (at(0, 0, 1) * (1 - fr) + at(1, 0, 1) * fr) * (1 - fg) + (at(0, 1, 1) * (1 - fr) + at(1, 1, 1) * fr) * fg
    return c0 * (1 - fb) + c1 * fb


def simulate(log_image, input_lut: str | Path | None, nodes, output_lut: str | Path):
    """The whole clip pipeline. Returns (DWG/Intermediate after the nodes, display image).

    input_lut None means the image is already in DWG/Intermediate. nodes: CDLs for 01–04, in order.
    """
    working = apply_lut(log_image, input_lut) if input_lut is not None else np.asarray(log_image, dtype="float64")
    graded = apply_nodes(working, nodes)
    return graded, apply_lut(graded, output_lut)


# ------------------------------------------------------------------------------------------------ display

def luminance(display):
    """Rec.709 luma of a display image (on the gamma-encoded values, like a waveform)."""
    return np.asarray(display, dtype="float64") @ REC709_LUMA


def ire(display_value):
    """Display value (data levels) on the 0–100 IRE scale."""
    return np.asarray(display_value, dtype="float64") * 100.0


def dwg_luminance(linear):
    """Relative luminance Y of linear DaVinci Wide Gamut values."""
    matrix = _colour().RGB_COLOURSPACES["DaVinci Wide Gamut"].matrix_RGB_to_XYZ
    return np.asarray(linear, dtype="float64") @ matrix[1]


def display_to_lab(display):
    """CIELAB (D65) of a Rec.709 Gamma 2.4 display image."""
    colour = _colour()
    space = colour.RGB_COLOURSPACES["ITU-R BT.709"]
    linear = np.clip(np.asarray(display, dtype="float64"), 0.0, 1.0) ** DISPLAY_GAMMA
    xyz = linear @ space.matrix_RGB_to_XYZ.T
    return colour.XYZ_to_Lab(xyz, space.whitepoint)


def chroma(lab):
    """CIELAB C* from Lab values."""
    lab = np.asarray(lab, dtype="float64")
    return np.hypot(lab[..., 1], lab[..., 2])


def ycbcr(display):
    """Rec.709 Y'CbCr of a display image: (Y' 0–1, Cb, Cr ±0.5), as a vectorscope sees it."""
    display = np.asarray(display, dtype="float64")
    y = display @ REC709_LUMA
    return y, (display[..., 2] - y) / 1.8556, (display[..., 0] - y) / 1.5748


def vectorscope_hue(cb, cr):
    """Angle on the vectorscope in degrees, counter-clockwise from +Cb. Resolve's skin line is at about 123°."""
    return np.degrees(np.arctan2(cr, cb)) % 360.0


# ------------------------------------------------------------------------------------------------ white

def dwg_to_xy(linear):
    """CIE xy chromaticity of linear DWG values."""
    colour = _colour()
    xyz = np.asarray(linear, dtype="float64") @ colour.RGB_COLOURSPACES["DaVinci Wide Gamut"].matrix_RGB_to_XYZ.T
    return xyz[..., :2] / np.maximum(xyz.sum(-1, keepdims=True), 1e-12)


def xy_to_dwg(xy, luminance: float = 1.0):
    """Linear DWG RGB of a chromaticity at the given luminance Y."""
    colour = _colour()
    x, y = float(xy[0]), float(xy[1])
    xyz = np.array([x / y, 1.0, (1 - x - y) / y]) * luminance
    return xyz @ colour.RGB_COLOURSPACES["DaVinci Wide Gamut"].matrix_XYZ_to_RGB.T


def cct_duv(xy) -> tuple[float, float]:
    """Correlated colour temperature and Duv (distance from the blackbody locus), Ohno 2013."""
    colour = _colour()
    cct, duv = colour.temperature.uv_to_CCT(colour.xy_to_UCS_uv(np.asarray(xy, dtype="float64")), method="Ohno 2013")
    return float(cct), float(duv)


def cct_duv_to_xy(cct: float, duv: float):
    colour = _colour()
    return colour.UCS_uv_to_xy(colour.temperature.CCT_to_uv(np.array([cct, duv]), method="Ohno 2013"))


def angle_deg(a, b) -> float:
    """Angle between two RGB vectors in degrees (the usual white balance error measure)."""
    a, b = np.asarray(a, dtype="float64"), np.asarray(b, dtype="float64")
    cos = float(a @ b / max(np.linalg.norm(a) * np.linalg.norm(b), 1e-12))
    return float(np.degrees(np.arccos(min(max(cos, -1.0), 1.0))))
