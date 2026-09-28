import pytest

np = pytest.importorskip("numpy")

from davigen import colormath  # noqa: E402
from davigen.basic import correct as c  # noqa: E402
from davigen.basic import evaluate  # noqa: E402
from davigen.basic import measure as m  # noqa: E402
from davigen.basic import settings  # noqa: E402
from test_basic_measure import di, neutral_scene  # noqa: E402

pytestmark = pytest.mark.skipif(not colormath.available(), reason="colour-science not installed")


def test_learn_moves_towards_the_user_and_clamps(tmp_path):
    s = settings.load(None, learned=False)
    target = tmp_path / "learned.json"
    # DAVIGEN_AUTO was 0.6 stops brighter, 400 K warmer-looking (lower CCT), blacks 0.01 higher, 10 % more colourful
    diffs = [{"exposure": 0.6, "kelvin": -400, "black": 0.01, "chroma": 1.1}] * 5
    first = settings.learn(diffs, s, target)
    assert first["exposure"] == pytest.approx(-0.3) and first["kelvin"] == pytest.approx(200)
    assert first["black"] == pytest.approx(-0.005) and first["chroma"] == pytest.approx(1.1 ** -0.5)
    assert first["clips"] == 5
    for _ in range(20):
        last = settings.learn([{"exposure": 5.0, "kelvin": -9000, "black": 0.5, "chroma": 3.0}], s, target)
    assert last["exposure"] == -1.0 and last["kelvin"] == 1500 and last["black"] == -0.03
    assert last["chroma"] == pytest.approx(0.7)
    assert settings.learn([], s, target) == settings.load_learned()        # nothing to learn: unchanged


def test_learned_offsets_change_the_correction(tmp_path):
    lut = colormath.output_lut(tmp_path / "out.cube", size=33)
    samples = [di(neutral_scene(key=0.09))]
    base = settings.load(None, learned=False)
    base["exposure"]["headroom_weight"] = 0.0
    meas = m.measure(samples, m.ClipMeta(), lut, base)
    plain = c.correct(meas, samples, lut, base)
    tuned = dict(base, learned={**settings.NEUTRAL_LEARNED, "exposure": -0.5, "kelvin": 300})
    shifted = c.correct(meas, samples, lut, tuned)
    assert shifted.values["exposure_stops"] == pytest.approx(plain.values["exposure_stops"] - 0.5, abs=1e-6)
    assert shifted.values["cct_after"] == pytest.approx(plain.values["cct_after"] + 300, abs=1e-6)


def test_score_signs():
    rng = np.random.default_rng(1)
    lin = rng.uniform(0.02, 0.4, (32, 48, 3))
    disp = np.clip(lin, 0, 1) ** (1 / 2.4)
    warmer = np.clip(lin * [1.15, 1.0, 0.85], 0, 1) ** (1 / 2.4)
    sc = evaluate.score(disp, warmer)
    assert sc.kelvin < -200 and sc.chroma > 1.0
    lifted = evaluate.score(disp, np.clip(disp + 0.03, 0, 1))
    assert lifted.black == pytest.approx(0.03, abs=0.005)
