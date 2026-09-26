from pathlib import Path

import pytest

from davigen import colormath
from davigen.lut import Lut3D

pytestmark = pytest.mark.skipif(not colormath.available(), reason="colour-science not installed")
RESOLVE_VLOG = Path("/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT/davigen/"
                    "DAVIGEN_IN_VLOG_TO_DWG.cube")


def test_vlog_grey_lands_on_davinci_intermediate_grey():
    assert abs(colormath.grey_check("V-Log", 0.423) - 0.3357) < 5e-4


def test_gplog2_formula():
    # GoPro GP-Log2: log base 600; 18 % grey encodes to log600(0.18 * 599 + 1)
    import math
    code = math.log(0.18 * 599 + 1, 600)
    assert abs(colormath._decoder("logbase:600")(code) - 0.18) < 1e-9


@pytest.mark.skipif(not RESOLVE_VLOG.exists(), reason="Resolve-baked reference LUT not present")
def test_matches_resolve_cst(tmp_path):
    ours = Lut3D.read(colormath.input_lut("V-Log", "V-Gamut", tmp_path / "v.cube", size=33))
    ref = Lut3D.read(RESOLVE_VLOG)
    worst = 0.0
    for v in [0.2, 0.35, 0.423, 0.5, 0.6, 0.7, 0.8]:
        for rgb in [(v, v, v), (v, v * 0.9, v * 0.8), (v * 0.8, v, v * 0.95)]:
            a, b = ours.sample(*rgb), ref.sample(*rgb)
            worst = max(worst, *(abs(x - y) for x, y in zip(a, b)))
    assert worst < 2e-3   # 33-point interpolation vs 65-point reference
