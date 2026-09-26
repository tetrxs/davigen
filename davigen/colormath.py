"""Exact input LUTs computed from published log-curve formulas (colour-science, BSD-3).

Used for every profile the installed Resolve has no Color Space Transform for. Verified against
Resolve's own CST: V-Log → DWG/Intermediate differs by at most 3e-5 over 140k grid points.
"""

from __future__ import annotations

import math
import warnings
from pathlib import Path

WORKING_GAMUT = "DaVinci Wide Gamut"


def available() -> bool:
    try:
        _colour()
        return True
    except ImportError:
        return False


def _colour():
    warnings.filterwarnings("ignore", module="colour")
    import colour  # noqa: PLC0415 - optional dependency, only in davigen's own Python
    return colour


def known_curves() -> list[str]:
    return sorted(_colour().LOG_DECODINGS) if available() else []


def known_gamuts() -> list[str]:
    return sorted(_colour().RGB_COLOURSPACES) if available() else []


def _decoder(curve: str):
    """colour-science curve name, or a formula 'logbase:<N>' (GoPro GP-Log2 style: (N^v - 1)/(N - 1))."""
    colour = _colour()
    if curve.startswith("logbase:"):
        base = float(curve.split(":", 1)[1])
        return lambda v: (base ** v - 1.0) / (base - 1.0)
    if curve not in colour.LOG_DECODINGS:
        raise KeyError(f"colour-science doesn't know the log curve '{curve}'")
    return lambda v: colour.log_decoding(v, function=curve)


def input_lut(curve: str, gamut: str, target: Path, size: int = 65, title: str = "") -> Path:
    """Write a .cube LUT: camera log (curve, gamut) → DaVinci Wide Gamut / DaVinci Intermediate."""
    colour = _colour()
    import numpy as np  # noqa: PLC0415

    if gamut not in colour.RGB_COLOURSPACES:
        raise KeyError(f"colour-science doesn't know the gamut '{gamut}'")
    grid = np.linspace(0.0, 1.0, size)
    b, g, r = np.meshgrid(grid, grid, grid, indexing="ij")   # .cube order: red varies fastest
    rgb = np.stack([r, g, b], axis=-1).reshape(-1, 3)
    linear = _decoder(curve)(rgb)
    dwg = colour.RGB_to_RGB(linear, colour.RGB_COLOURSPACES[gamut], colour.RGB_COLOURSPACES[WORKING_GAMUT],
                            chromatic_adaptation_transform="CAT02")
    out = np.clip(colour.models.oetf_DaVinciIntermediate(dwg), 0.0, None)   # Resolve clips below 0 as well
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'TITLE "{title or target.stem}"', f"LUT_3D_SIZE {size}", ""]
    lines += [f"{x:.6f} {y:.6f} {z:.6f}" for x, y, z in out]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def grey_check(curve: str, code_value: float) -> float:
    """Where a given log code value lands in DaVinci Intermediate (for tests / sanity checks)."""
    lin = float(_decoder(curve)(code_value))
    return float(_colour().models.oetf_DaVinciIntermediate(lin)) if lin > 0 else -math.inf


def output_lut(target: Path, size: int = 33, title: str = "") -> Path:
    """Fallback output LUT (DWG/Intermediate → Rec.709 Gamma 2.4) for Resolve versions that won't bake
    davigen's CST template. Uses a smooth highlight roll-off instead of Resolve's DaVinci tone mapping."""
    colour = _colour()
    import numpy as np  # noqa: PLC0415

    grid = np.linspace(0.0, 1.0, size)
    b, g, r = np.meshgrid(grid, grid, grid, indexing="ij")
    rgb = np.stack([r, g, b], axis=-1).reshape(-1, 3)
    linear = colour.models.oetf_inverse_DaVinciIntermediate(rgb)
    rec709 = colour.RGB_to_RGB(linear, colour.RGB_COLOURSPACES[WORKING_GAMUT], colour.RGB_COLOURSPACES["ITU-R BT.709"],
                               chromatic_adaptation_transform="CAT02")
    rec709 = np.clip(rec709, 0.0, None)
    # extended Reinhard roll-off, white point at 8× diffuse white, keeps 18 % grey where it was
    white = 8.0
    toned = rec709 * (1 + rec709 / white ** 2) / (1 + rec709)
    toned *= 0.18 / (0.18 * (1 + 0.18 / white ** 2) / 1.18)
    out = np.clip(toned, 0.0, 1.0) ** (1 / 2.4)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'TITLE "{title or target.stem}"', f"LUT_3D_SIZE {size}", ""]
    lines += [f"{x:.6f} {y:.6f} {z:.6f}" for x, y, z in out]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target
