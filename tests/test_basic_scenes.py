import copy

import pytest

np = pytest.importorskip("numpy")

from davigen import colormath  # noqa: E402
from davigen.basic import correct as c  # noqa: E402
from davigen.basic import measure as m  # noqa: E402
from davigen.basic import pipeline as p  # noqa: E402
from davigen.basic import scenes  # noqa: E402
from davigen.basic import settings  # noqa: E402

from test_basic_correct import grey_scene, light  # noqa: E402 - tests/ is on sys.path under pytest
from test_basic_measure import di, neutral_scene  # noqa: E402

pytestmark = pytest.mark.skipif(not colormath.available(), reason="colour-science not installed")
S = settings.load(None)


@pytest.fixture(scope="module")
def out_lut(tmp_path_factory):
    return colormath.output_lut(tmp_path_factory.mktemp("lut") / "out.cube", size=33)


def shot(frame, out_lut, order, created="2026-09-25T10:00:00Z", meta=None, seconds=5.0, cfg=S):
    meta = meta or m.ClipMeta(created=created)
    meta.created = created
    samples = [di(frame)]
    meas = m.measure(samples, meta, out_lut, cfg)
    return scenes.Shot(id=f"item{order}", order=order, meta=meta, measurement=meas,
                       correction=c.correct(meas, samples, out_lut, cfg), used_seconds=seconds, clip_seconds=seconds)


def wb_light_after(s: scenes.Shot, illum):
    """Where a grey surface at the shot's key, under `illum`, ends up after the shot's nodes 01 and 02."""
    key = 0.18 * 2 ** s.measurement.exposure_stops
    out = p.apply_nodes(p.to_log(key * np.asarray(illum)), [s.correction.nodes[c.EXPOSURE],
                                                            s.correction.nodes[c.WHITE_BALANCE]])
    return p.to_linear(out)


def test_green_closeup_inherits_scene_white_balance(out_lut):
    illum = light(4800)
    wide = shot(grey_scene(seed=1) * illum, out_lut, 0, "2026-09-25T10:00:00Z", seconds=8)
    medium = shot(grey_scene(seed=2) * illum, out_lut, 1, "2026-09-25T10:01:00Z")
    bush = grey_scene(seed=3) * illum
    bush[:, :80] *= np.array([0.35, 1.0, 0.3]) / p.dwg_luminance(np.array([0.35, 1.0, 0.3]))
    close = shot(bush, out_lut, 2, "2026-09-25T10:02:00Z")
    assert close.correction.confidence[c.WHITE_BALANCE] < S["confidence_flag_below"]
    scenes.match_scenes([wide, medium, close], S)
    assert wide.scene == medium.scene == close.scene and wide.hero
    assert close.notes["white_balance"] == "from the scene"
    assert scenes.MATCHED in close.correction.flags
    # the green close-up is now balanced for the light, not for its own green
    assert p.angle_deg(wb_light_after(close, illum), wb_light_after(wide, illum)) < 0.5


def test_pause_starts_new_scene(out_lut):
    a = shot(grey_scene(seed=1), out_lut, 0, "2026-09-25T10:00:00Z")
    b = shot(grey_scene(seed=2), out_lut, 1, "2026-09-25T10:30:00Z")
    scenes.match_scenes([a, b], S)
    assert a.scene != b.scene and a.hero and b.hero


def test_day_to_night_splits_scene(out_lut):
    day = shot(grey_scene(seed=1), out_lut, 0, "2026-09-25T20:00:00Z",
               m.ClipMeta(iso=100, fnumber=8, exposure_time=1 / 250))          # EV100 ≈ 14
    inside = shot(grey_scene(seed=2, key=0.05), out_lut, 1, "2026-09-25T20:03:00Z",
                  m.ClipMeta(iso=3200, fnumber=2, exposure_time=1 / 50))       # EV100 ≈ 2.6
    assert len(scenes.group([day, inside], S)) == 2


def test_single_shot_is_unchanged(out_lut):
    s = shot(grey_scene() * light(5600), out_lut, 0)
    before = copy.deepcopy(s.correction.nodes)
    scenes.match_scenes([s], S)
    assert s.correction.nodes == before and s.hero
    assert scenes.MATCHED not in s.correction.flags


def test_pull_strength(out_lut):
    def pair(pull):
        cfg = settings.load({"basic_correction": {"scenes": {"pull_to_scene": pull}}})
        hero = shot(grey_scene(seed=1), out_lut, 0, "2026-09-25T10:00:00Z", seconds=20, cfg=cfg)
        other = shot(grey_scene(seed=2, key=0.18 * 2 ** 0.6) * light(5400), out_lut, 1, "2026-09-25T10:01:00Z",
                     seconds=2, cfg=cfg)
        before = copy.deepcopy(other.correction)
        scenes.match_scenes([hero, other], cfg)
        return hero, other, before
    _, other, before = pair(0.0)
    assert other.correction.nodes == before.nodes
    hero, other, _ = pair(1.0)
    after = other.measurement.exposure_stops + other.correction.values["exposure_stops"]
    ref = hero.measurement.exposure_stops + hero.correction.values["exposure_stops"]
    assert after == pytest.approx(ref, abs=1e-9)
    assert other.correction.values["cct_after"] == pytest.approx(hero.correction.values["cct_after"])


def test_silhouette_is_not_pulled(out_lut):
    hero = shot(grey_scene(seed=1), out_lut, 0, "2026-09-25T18:00:00Z", seconds=20)
    frame = np.full((64, 96, 3), 0.18 * 8)
    frame[20:] = 0.18 * 2 ** -6
    sil = shot(frame, out_lut, 1, "2026-09-25T18:01:00Z")
    assert m.LOW_KEY in sil.measurement.flags
    stops = sil.correction.values["exposure_stops"]
    scenes.match_scenes([hero, sil], S)
    assert sil.correction.values["exposure_stops"] == stops


def test_fixed_kelvin_leans_on_scene(out_lut):
    illum = light(5200)
    fixed = dict(white_balance="Manual", kelvin=5600)
    hero = shot(grey_scene(seed=1) * illum, out_lut, 0, "2026-09-25T10:00:00Z", m.ClipMeta(**fixed), seconds=20)
    other_scene = neutral_scene(seed=4) * illum          # colour patches: its own estimate is a bit off
    other = shot(other_scene, out_lut, 1, "2026-09-25T10:01:00Z", m.ClipMeta(**fixed))
    err_before = p.angle_deg(wb_light_after(other, illum), wb_light_after(hero, illum))
    scenes.match_scenes([hero, other], S)
    err_after = p.angle_deg(wb_light_after(other, illum), wb_light_after(hero, illum))
    assert err_after <= err_before * (1 - S["scenes"]["pull_fixed_wb"]) + 0.05
