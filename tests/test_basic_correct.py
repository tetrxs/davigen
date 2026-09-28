import pytest

np = pytest.importorskip("numpy")

from davigen import colormath  # noqa: E402
from davigen.basic import correct as c  # noqa: E402
from davigen.basic import measure as m  # noqa: E402
from davigen.basic import pipeline as p  # noqa: E402
from davigen.basic import settings  # noqa: E402

from test_basic_measure import di, neutral_scene  # noqa: E402 - tests/ is on sys.path under pytest

pytestmark = pytest.mark.skipif(not colormath.available(), reason="colour-science not installed")
S = settings.load(None)
S["measure"]["white_balance"]["learned"] = False     # these tests check the maths on synthetic scenes, where the
#                                                      classic estimators are exact; the learned model is tested
#                                                      on real images (scripts/train_wb.py, test_basic_wb_model.py)


@pytest.fixture(scope="module")
def out_lut(tmp_path_factory):
    return colormath.output_lut(tmp_path_factory.mktemp("lut") / "out.cube", size=33)


def run(frames, out_lut, meta=None, cfg=S):
    samples = [di(f) for f in frames]
    meas = m.measure(samples, meta or m.ClipMeta(), out_lut, cfg)
    return meas, c.correct(meas, samples, out_lut, cfg)


def light(cct, duv_offset=0.0):
    """Linear DWG colour of a light source, luminance 1."""
    neutral_duv = p.cct_duv(p.dwg_to_xy(np.ones(3)))[1]
    rgb = p.xy_to_dwg(p.cct_duv_to_xy(cct, neutral_duv + duv_offset if cct > 4000 else duv_offset))
    return rgb / p.dwg_luminance(rgb)


def grey_scene(key=0.18, seed=0):
    """Neutral patches of varying brightness only: every estimator sees the light exactly, so these tests check
    the correction math, not the estimators (test_basic_measure does that)."""
    rng = np.random.default_rng(seed)
    level = np.exp(rng.normal(0, 0.7, (8, 12, 1)))
    level *= key / np.exp(np.log(level).mean())
    return np.kron(level * np.ones(3), np.ones((8, 8, 1)))


def after_01_02(frame_lin, corr):
    return p.apply_nodes(di(frame_lin), [corr.nodes[c.EXPOSURE], corr.nodes[c.WHITE_BALANCE]])


def test_grey_card_one_stop_under(out_lut):
    card = np.full((64, 96, 3), 0.09)
    _, corr = run([card], out_lut)
    off = corr.nodes[c.EXPOSURE].offset
    assert off[0] == off[1] == off[2]
    assert off[0] == pytest.approx(p.STOP, abs=0.005)
    assert np.allclose(p.apply_cdl(di(card), corr.nodes[c.EXPOSURE]), p.GREY, atol=1e-3)
    assert corr.values["exposure_stops"] == pytest.approx(1.0, abs=0.05)


def test_warm_light_is_only_partly_neutralised(out_lut):
    scene = grey_scene() * light(3000)
    meas, corr = run([scene], out_lut)
    assert meas.cct == pytest.approx(3000, abs=100)
    key = 0.18 * 2 ** meas.exposure_stops                    # log offsets are exact at the frame's key
    grey = np.full((1, 1, 3), key) * light(3000)
    out = p.to_linear(after_01_02(grey, corr))[0, 0]
    after = p.cct_duv(p.dwg_to_xy(out))[0]
    expected = meas.cct + 0.35 * (6504 - meas.cct)
    assert after == pytest.approx(expected, abs=50)
    assert corr.values["cct_after"] == pytest.approx(expected, abs=1)


def test_green_cast_is_removed(out_lut):
    green = light(6504, duv_offset=0.01)
    scene = grey_scene() * green
    _, corr = run([scene], out_lut)
    out = p.to_linear(after_01_02(np.full((1, 1, 3), 0.18) * green, corr))[0, 0]
    assert p.angle_deg(out, [1, 1, 1]) < 0.5


def test_wb_keeps_grey_luminance(out_lut):
    for cct in (3000, 4500, 9000):
        illum = light(cct)
        _, corr = run([grey_scene() * illum], out_lut)
        grey = np.full(3, p.GREY) + (p.to_log(0.18 * illum) - p.GREY)     # a grey card at the key, under the light
        out = p.to_linear(p.apply_cdl(grey, corr.nodes[c.WHITE_BALANCE]))
        assert float(p.dwg_luminance(out)) == pytest.approx(0.18, abs=0.002)


def test_contrast_keeps_grey_and_stays_in_range(out_lut):
    rng = np.random.default_rng(5)
    flat = 0.18 * 2 ** rng.normal(0, 0.3, (8, 12, 1)) * np.ones(3)
    punchy = 0.18 * 2 ** rng.normal(0, 3.0, (8, 12, 1)) * np.ones(3)
    for scene in (np.kron(flat, np.ones((8, 8, 1))), np.kron(punchy, np.ones((8, 8, 1)))):
        _, corr = run([scene], out_lut)
        cdl = corr.nodes[c.CONTRAST]
        assert np.allclose(p.apply_cdl(np.full(3, p.GREY), cdl), p.GREY)
        assert S["contrast"]["range"][0] <= cdl.slope[0] <= S["contrast"]["range"][1]


def test_night_is_barely_touched(out_lut):
    night = m.ClipMeta(iso=3200, fnumber=2.0, exposure_time=1 / 25)      # EV100 ≈ 2.6
    meas, corr = run([neutral_scene(key=0.09)], out_lut, night)
    assert meas.ev100 < 5 and m.NIGHT in meas.flags
    assert corr.values["exposure_stops"] < 1.0
    assert corr.values["exposure_stops"] >= 0.0                         # never darkened further


def test_limit_reached(out_lut):
    meas, corr = run([neutral_scene(key=0.18 / 16)], out_lut)             # four stops under
    assert corr.values["exposure_stops"] == pytest.approx(S["exposure"]["max_stops_up"])
    assert c.LIMIT in corr.flags and corr.confidence[c.EXPOSURE] < 1.0


def test_chain_puts_grey_card_on_grey_and_neutral(out_lut):
    illum = light(5200)
    frame = grey_scene(key=0.07) * illum
    frame[24:40, 40:56] = 0.07 * illum                                    # a grey card in the middle
    _, corr = run([frame], out_lut)
    out = p.apply_nodes(di(frame), corr.chain())[32, 48]
    assert np.allclose(out.mean(), p.GREY, atol=0.02)
    assert p.angle_deg(p.to_linear(out), [1, 1, 1]) < 1.0


def test_saturation_and_record(out_lut):
    rng = np.random.default_rng(6)
    grey_ish = neutral_scene() * 0.3 + 0.7 * neutral_scene().mean(-1, keepdims=True)   # washed out
    _, corr = run([grey_ish], out_lut)
    assert corr.nodes[c.SATURATION].sat >= 1.0
    loud = neutral_scene() ** 1.0 * rng.uniform(0.3, 1.7, (1, 1, 3))
    import json
    json.dumps(corr.to_dict())
    assert set(corr.nodes) == set(c.NODES)
    assert 0.0 <= corr.overall <= 1.0
    del loud


def test_saturation_never_boosts_colourful(out_lut):
    rng = np.random.default_rng(7)
    vivid = np.kron(rng.uniform(0.02, 0.6, (8, 12, 3)), np.ones((8, 8, 1)))
    _, corr = run([vivid], out_lut)
    if corr.values["chroma_before"] > S["saturation"]["chroma"][1]:
        assert corr.nodes[c.SATURATION].sat <= 1.0


def test_clipped_sky_is_not_turned_grey(out_lut):
    frame = neutral_scene(key=0.5)
    sky = di(frame)
    sky[:16] = 0.75                                                       # blown sky on a bright frame
    meas = m.measure([sky], m.ClipMeta(), out_lut, S)
    corr = c.correct(meas, [sky], out_lut, S)
    assert m.CLIPPED in meas.flags
    top = p.apply_nodes(np.full(3, meas.clip_level), [corr.nodes[c.EXPOSURE]])
    white = float(p.luminance(p.apply_lut(top, out_lut)))
    assert white >= S["exposure"]["clipped_white"] - 0.01


def test_log_exposed_to_the_right_comes_down(out_lut):
    """Real Lumix V-Log clips sat 2–3 stops over (step 04 on 57 clips): they must come down more than 1.5 stops."""
    rng = np.random.default_rng(8)
    colours = np.kron(rng.uniform(0.25, 1.75, (8, 12, 3)), np.ones((8, 8, 1)))     # a colourful street, not snow
    scene = neutral_scene(key=0.18 * 2 ** 2.5) * colours
    meas, corr = run([scene], out_lut)
    assert not meas.high_key
    assert corr.values["exposure_stops"] == pytest.approx(-meas.exposure_stops, abs=S["exposure"]["skin_max_nudge"] + 0.05)
    assert corr.values["exposure_stops"] < -1.5


def test_lut_ceiling_does_not_make_a_sunny_frame_dark(out_lut):
    """A vendor LUT (DJI) squeezes highlights onto a ceiling: that plateau still counts as bright."""
    frame = di(neutral_scene(key=0.18))
    frame[:28] = 0.62                                          # 44 % of the frame on the LUT's ceiling
    meas = m.measure([frame], m.ClipMeta(), out_lut, S)
    corr = c.correct(meas, [frame], out_lut, S)
    assert meas.clipped_fraction > 0.4 and meas.exposure_stops > 0.5
    assert corr.values["exposure_stops"] <= 0.0                # never brightened


def test_brightening_stops_where_highlights_run_out(out_lut):
    """A dark sea under bright rocks (DJI, step 05 calibration): the key says brighten, the highlights say no."""
    rng = np.random.default_rng(9)
    sea = np.kron(0.18 * 2 ** -1.5 * np.exp(rng.normal(0, 0.3, (8, 12, 1))) * [0.4, 0.8, 1.3], np.ones((8, 8, 1)))
    rock = np.exp(rng.normal(0, 0.15, (12, 96, 1)))                     # texture: rock isn't a clipped plateau
    sea[:12] = 0.18 * 2 ** 3.2 * rock * np.array([1.0, 0.97, 0.9])     # sunlit rock, already near display white
    meas, corr = run([sea], out_lut)
    assert meas.exposure_stops < -0.3
    assert corr.values["exposure_stops"] < 0.5 and "held by highlights" in corr.values["exposure_reason"]
