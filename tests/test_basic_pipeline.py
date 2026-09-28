from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

from davigen import colormath  # noqa: E402
from davigen.basic import pipeline as p  # noqa: E402
from davigen.lut import Lut3D  # noqa: E402

pytestmark = pytest.mark.skipif(not colormath.available(), reason="colour-science not installed")
RESOLVE_VLOG = Path("/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT/davigen/"
                    "DAVIGEN_IN_VLOG_TO_DWG.cube")


@pytest.fixture(scope="module")
def luts(tmp_path_factory):
    d = tmp_path_factory.mktemp("luts")
    return (colormath.input_lut("V-Log", "V-Gamut", d / "in.cube", size=17),
            colormath.output_lut(d / "out.cube", size=17))


def test_grey_lands_on_0336():
    assert abs(float(p.to_log(0.18)) - p.GREY) < 1e-3


def test_davinci_intermediate_matches_colour_science():
    colour = colormath._colour()
    x = np.concatenate([np.linspace(0, 0.01, 50), np.geomspace(0.01, 100, 200)])
    assert np.allclose(p.to_log(x), colour.models.oetf_DaVinciIntermediate(x), atol=1e-7)
    y = np.linspace(0, 1, 300)
    assert np.allclose(p.to_linear(y), colour.models.oetf_inverse_DaVinciIntermediate(y), rtol=1e-6, atol=1e-9)


def test_one_stop_offset_doubles_linear():
    lin = np.repeat(np.array([0.01, 0.05, 0.18, 0.5, 2.0, 8.0])[:, None], 3, axis=1)
    out = p.to_linear(p.apply_cdl(p.to_log(lin), p.Cdl.exposure(1.0)))
    assert np.allclose(out + 0.0075, 2 * (lin + 0.0075))         # exact: DI's log2(x + 0.0075)
    assert np.allclose(out[3:] / lin[3:], 2.0, rtol=0.01)          # "doubles" well above the toe
    assert abs(p.gain_to_offset(2.0) - p.STOP) < 1e-12


@pytest.mark.parametrize("c", [0.5, 0.85, 1.0, 1.35, 2.0])
def test_contrast_keeps_grey(c):
    grey = np.full(3, p.GREY)
    assert np.allclose(p.apply_cdl(grey, p.Cdl.contrast(c)), p.GREY, atol=1e-12)


def test_cdl_identity():
    img = np.random.default_rng(0).uniform(-0.1, 1.1, (8, 8, 3))
    assert np.array_equal(p.apply_cdl(img, p.Cdl()), img)
    assert p.Cdl().is_identity


def test_saturation_mixes_with_resolve_luma():
    img = np.array([[0.6, 0.4, 0.2], [0.3, 0.3, 0.3]])
    out = p.apply_cdl(img, p.Cdl.saturation(0.0))
    assert np.allclose(out[0], img[0] @ p.SAT_LUMA)
    assert np.allclose(out[1], 0.3)                        # neutral stays neutral
    half = p.apply_cdl(img, p.Cdl.saturation(0.5))
    assert np.allclose(2 * half - img, (img @ p.SAT_LUMA)[:, None])   # the relation the spike measured


def test_power_clamps_below_zero():
    out = p.apply_cdl(np.array([-0.1, 0.25, 0.5]), p.Cdl(power=(2.0, 2.0, 2.0)))
    assert np.allclose(out, [0.0, 0.0625, 0.25])


def test_white_balance_offsets():
    cdl = p.Cdl.white_balance([2.0, 1.0, 0.5])
    assert np.allclose(cdl.offset, [p.STOP, 0.0, -p.STOP])


def test_to_resolve_format():
    assert p.Cdl.exposure(1.0).to_resolve(1) == {
        "NodeIndex": "1", "Slope": "1 1 1", "Offset": "0.073292 0.073292 0.073292", "Power": "1 1 1",
        "Saturation": "1"}
    assert p.Cdl(offset=(-0.05, 0.0, 0.0), sat=0.5).to_resolve(4)["Offset"] == "-0.05 0 0"
    assert p.Cdl.contrast(1.2).to_resolve(3)["Slope"] == "1.2 1.2 1.2"


def test_apply_lut_matches_reference_sampler(luts):
    in_lut, _ = luts
    ref = Lut3D.read(in_lut)
    pts = np.random.default_rng(1).uniform(0, 1, (200, 3))
    ours = p.apply_lut(pts, in_lut)
    theirs = np.array([ref.sample(*pt) for pt in pts])
    assert np.allclose(ours, theirs, atol=1e-9)
    # corners, and values outside 0–1 clamp like Resolve
    assert np.allclose(p.apply_lut(np.array([[1.0, 1.0, 1.0], [1.5, 1.5, 1.5]]), in_lut), ref.sample(1, 1, 1))


def test_simulate_identity_equals_two_luts(luts):
    in_lut, out_lut = luts
    img = np.random.default_rng(2).uniform(0.1, 0.8, (6, 9, 3))
    graded, display = p.simulate(img, in_lut, [p.Cdl()] * 4, out_lut)
    assert np.allclose(graded, p.apply_lut(img, in_lut))
    assert np.allclose(display, p.apply_lut(p.apply_lut(img, in_lut), out_lut))
    assert display.shape == img.shape


def test_simulate_without_input_lut(luts):
    _, out_lut = luts
    grey = np.full((2, 2, 3), p.GREY)
    graded, display = p.simulate(grey, None, [p.Cdl.exposure(0.0)], out_lut)
    assert np.allclose(graded, p.GREY)
    assert np.allclose(display, p.apply_lut(grey, out_lut))


def test_display_helpers():
    lab = p.display_to_lab(np.array([[1.0, 1.0, 1.0], [0.5, 0.5, 0.5], [1.0, 0.0, 0.0]]))
    assert abs(lab[0, 0] - 100) < 0.01 and p.chroma(lab)[0] < 0.01 and p.chroma(lab)[1] < 0.01
    assert p.chroma(lab)[2] > 50
    assert p.ire(0.65) == pytest.approx(65)
    assert p.luminance([1.0, 1.0, 1.0]) == pytest.approx(1.0)
    assert float(p.dwg_luminance([1.0, 1.0, 1.0])) == pytest.approx(1.0, abs=1e-6)


@pytest.mark.skipif(not RESOLVE_VLOG.exists(), reason="Resolve-baked V-Log LUT not present")
def test_resolve_vlog_grey():
    out = p.apply_lut(np.array([0.423, 0.423, 0.423]), RESOLVE_VLOG)
    assert np.allclose(out, 0.336, atol=2e-3)
