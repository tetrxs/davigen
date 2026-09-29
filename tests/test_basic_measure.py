import tomllib
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

from davigen import colormath  # noqa: E402
from davigen.basic import measure as m  # noqa: E402
from davigen.basic import pipeline as p  # noqa: E402
from davigen.basic import settings  # noqa: E402

pytestmark = pytest.mark.skipif(not colormath.available(), reason="colour-science not installed")
ROOT = Path(__file__).resolve().parent.parent
S = settings.load(None)
S["measure"]["white_balance"]["learned"] = False     # these tests check the maths on synthetic scenes, where the
#                                                      classic estimators are exact; the learned model is tested
#                                                      on real images (scripts/train_wb.py, test_basic_wb_model.py)
H, W = 64, 96


@pytest.fixture(scope="module")
def out_lut(tmp_path_factory):
    return colormath.output_lut(tmp_path_factory.mktemp("lut") / "out.cube", size=33)


def blocks(values: np.ndarray) -> np.ndarray:
    """(8, 12, 3) patch values → a 64 × 96 thumbnail of 8-pixel patches."""
    return np.kron(values, np.ones((8, 8, 1)))


def neutral_scene(key=0.18, seed=0):
    """Mostly grey patches of varying brightness with some colour, geometric mean = key, neutral on average."""
    rng = np.random.default_rng(seed)
    level = np.exp(rng.normal(0, 0.7, (8, 12, 1)))
    tint = np.ones((8, 12, 3))
    colour_patches = rng.random((8, 12)) < 0.4
    hues = rng.uniform(0.75, 1.3, (8, 12, 3))
    tint[colour_patches] = hues[colour_patches]
    tint /= np.exp(np.log(tint).mean(axis=(0, 1)))          # colours cancel out on average
    lin = level * tint
    lin *= key / np.exp(np.log(p.dwg_luminance(lin)).mean())
    return blocks(lin)


def di(lin):
    return p.to_log(np.maximum(lin, 0.0))


def test_settings_defaults_match_workflow_toml():
    with open(ROOT / "config" / "workflow.toml", "rb") as f:
        section = tomllib.load(f)["basic_correction"]

    def keys(d, prefix=""):
        out = set()
        for k, v in d.items():
            out.add(prefix + k)
            if isinstance(v, dict):
                out |= keys(v, prefix + k + ".")
        return out
    assert keys(section) == keys(settings.DEFAULTS)
    assert settings.load({})["measure"]["skin"]["line"] == 123
    assert settings.load({"basic_correction": {"measure": {"black_stops": 4}}})["measure"]["black_stops"] == 4
    assert settings.load({"basic_correction": {"measure": {"black_stops": 4}}})["measure"]["midtone_stops"] == 2.5


def test_ev100():
    assert m.ClipMeta(iso=100, fnumber=2.8, exposure_time=1 / 50).ev100() == pytest.approx(8.61, abs=0.01)
    assert m.ClipMeta(iso=640, fnumber=2.8).ev100() is None


def test_grey_card_one_stop_under(out_lut):
    card = np.full((H, W, 3), 0.09)
    r = m.measure([di(card)], m.ClipMeta(), out_lut, S)
    assert r.exposure_stops == pytest.approx(-1.0, abs=0.05)
    textured = m.measure([di(neutral_scene(key=0.09))], m.ClipMeta(), out_lut, S)
    assert textured.exposure_stops == pytest.approx(-1.0, abs=0.15)      # centre weighting moves it a little


def test_white_balance_finds_the_tint(out_lut):
    gains = np.array([1.25, 1.0, 0.75])
    scene = neutral_scene() * gains
    r = m.measure([di(scene)], m.ClipMeta(), out_lut, S)
    truth = m._norm(gains)
    assert p.angle_deg(r.illuminant, truth) < 1.0
    assert r.cct < 5000                         # more red, less blue: warm light
    neutral = m.measure([di(neutral_scene())], m.ClipMeta(), out_lut, S)
    assert p.angle_deg(neutral.illuminant, [1, 1, 1]) < 1.0
    assert abs(neutral.cct - 6504) < 350 and m.NO_NEUTRAL not in neutral.flags


def test_green_frame_is_dominant(out_lut):
    scene = neutral_scene()
    green = np.array([0.35, 1.0, 0.3])
    scene[:, : int(W * 0.7)] = scene[:, : int(W * 0.7)] * green / p.dwg_luminance(green)
    r = m.measure([di(scene)], m.ClipMeta(), out_lut, S)
    assert r.dominant_fraction > 0.6
    assert m.DOMINANT in r.flags


def test_snow_is_high_key(out_lut):
    rng = np.random.default_rng(3)
    snow = np.clip(0.18 * 2 ** rng.normal(1.3, 0.3, (H, W, 1)), 0, None) * np.ones(3)
    r = m.measure([di(snow)], m.ClipMeta(), out_lut, S)
    assert r.high_key and m.HIGH_KEY in r.flags and not r.low_key


def test_silhouette_is_low_key(out_lut):
    frame = np.full((H, W, 3), 0.18 * 2 ** 3)              # bright sky
    frame[H // 3:, :] = 0.18 * 2 ** -6                     # dark foreground
    r = m.measure([di(frame)], m.ClipMeta(), out_lut, S)
    assert r.low_key and m.LOW_KEY in r.flags and not r.high_key


def test_flat_frame_is_haze(out_lut):
    rng = np.random.default_rng(4)
    fog = 0.18 * 2 ** rng.normal(0, 0.05, (8, 12, 1)) * np.ones(3)
    r = m.measure([di(blocks(fog))], m.ClipMeta(), out_lut, S)
    assert r.haze and m.HAZE in r.flags
    busy = m.measure([di(neutral_scene())], m.ClipMeta(), out_lut, S)
    assert not busy.haze


def test_exposure_change_within_clip(out_lut):
    a, b = neutral_scene(key=0.045), neutral_scene(key=0.045) * 16.0       # four stops apart
    r = m.measure([di(a), di(b)], m.ClipMeta(), out_lut, S)
    assert r.exposure_spread == pytest.approx(4.0, abs=0.05)
    assert m.CHANGES in r.flags
    same = m.measure([di(a), di(a)], m.ClipMeta(), out_lut, S)
    assert m.CHANGES not in same.flags


def test_clipped_highlights(out_lut):
    frame = di(neutral_scene())
    frame[:10, :] = 0.8                                    # a flat plateau in all channels: blown sky
    r = m.measure([frame], m.ClipMeta(), out_lut, S)
    assert r.clipped_fraction == pytest.approx(10 / H, abs=0.01)
    assert m.CLIPPED in r.flags
    assert m.CLIPPED not in m.measure([di(neutral_scene())], m.ClipMeta(), out_lut, S).flags


def test_night_flag_from_metadata(out_lut):
    night = m.ClipMeta(iso=6400, fnumber=1.8, exposure_time=1 / 50)       # EV100 ≈ 1.3
    r = m.measure([di(neutral_scene(key=0.05))], night, out_lut, S)
    assert r.ev100 == pytest.approx(1.34, abs=0.02) and m.NIGHT in r.flags
    nd = m.measure([di(neutral_scene(key=0.4))], night, out_lut, S)           # bright picture: an ND filter
    assert m.NIGHT not in nd.flags


def test_skin_hue_and_record(out_lut):
    frame = neutral_scene()
    skin = np.array([0.42, 0.26, 0.19])                    # a warm skin tone in linear DWG
    frame[16:48, 32:64] = skin
    r = m.measure([di(frame)], m.ClipMeta(), out_lut, S)
    assert r.skin_fraction > 0.1
    assert 98 <= r.skin_hue <= 148
    warm_grey = neutral_scene() * np.array([1.5, 0.95, 0.55])            # grey walls under tungsten are not skin
    assert m.measure([di(warm_grey)], m.ClipMeta(), out_lut, S).skin_fraction < 0.02
    import json
    json.dumps(r.to_dict())                                # the record must be JSON


def test_scanner_reads_exposure():
    from davigen import scanner
    info = scanner.exposure(scanner.ClipInfo(path="x.mov"), {
        "ExifIFD:FNumber": 2.8, "ExifIFD:ExposureTime": "1/50", "ExifIFD:WhiteBalance": "Auto",
        "Panasonic:ColorTempKelvin": 5800})
    assert (info.fnumber, info.exposure_time, info.white_balance, info.kelvin) == (2.8, 0.02, "Auto", 5800)
    empty = scanner.exposure(scanner.ClipInfo(path="x.mp4"), {})
    assert (empty.fnumber, empty.exposure_time, empty.kelvin) == (0.0, 0.0, 0)
