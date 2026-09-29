"""Keyframes for changes within a clip (concept §14)."""

import pytest

np = pytest.importorskip("numpy")

from davigen import colormath  # noqa: E402
from davigen.basic import correct as c  # noqa: E402
from davigen.basic import dynamic as dyn  # noqa: E402
from davigen.basic import measure as m  # noqa: E402
from davigen.basic import pipeline as p  # noqa: E402
from davigen.basic import settings  # noqa: E402

from test_basic_correct import S, grey_scene, light  # noqa: E402
from test_basic_measure import di  # noqa: E402

pytestmark = pytest.mark.skipif(not colormath.available(), reason="colour-science not installed")


@pytest.fixture(scope="module")
def out_lut(tmp_path_factory):
    return colormath.output_lut(tmp_path_factory.mktemp("lut") / "out.cube", size=33)


def clip(keys, lights=None):
    """Frames 0, 25, 50 … with the given scene keys (stops) and lights."""
    lights = lights or [light(5500)] * len(keys)
    frames = [n * 25 for n in range(len(keys))]
    thumbs = [di(grey_scene(0.18 * 2.0 ** k, seed=n) * li) for n, (k, li) in enumerate(zip(keys, lights))]
    return frames, thumbs


def static(frames, thumbs, out_lut):
    meas = m.measure(thumbs, m.ClipMeta(), out_lut, S)
    return meas, c.correct(meas, thumbs, out_lut, S)


def key_after(thumb, kf, i):
    """The key of a frame after keyframe i's nodes 01 and 02."""
    img = p.apply_nodes(thumb, [kf.nodes[c.EXPOSURE][i], kf.nodes[c.WHITE_BALANCE][i]])
    return float(np.log2(np.exp(np.log(np.maximum(p.dwg_luminance(p.to_linear(img)), 1e-6)).mean()) / 0.18))


def test_steady_clip_needs_no_keyframes(out_lut):
    frames, thumbs = clip([0.3, 0.1, 0.4, 0.2, 0.3, 0.2])
    meas, corr = static(frames, thumbs, out_lut)
    assert dyn.extra_frames(frames, meas.per_sample, S) == []
    assert dyn.plan(frames, meas.per_sample, meas, corr, thumbs, out_lut, S) is None


def test_tunnel_exit_gets_a_ramp(out_lut):
    keys = [-3.5, -3.5, -3.4, 0.0, 0.1, 0.0, 0.0]
    frames, thumbs = clip(keys)
    meas, corr = static(frames, thumbs, out_lut)
    assert dyn.extra_frames(frames, meas.per_sample, S) == [54, 57, 61, 64, 68, 71]   # between 50 and 75
    kf = dyn.plan(frames, meas.per_sample, meas, corr, thumbs, out_lut, S)
    assert kf is not None and "exposure" in kf.reason
    assert kf.frames[0] == 0 and kf.frames[-1] == 150 and 50 in kf.frames and 75 in kf.frames
    assert len(kf.frames) <= 4                                          # flat stretches need no keyframes
    i_in, i_out = kf.frames.index(50), kf.frames.index(75)
    # the street after the tunnel keeps the static correction; the tunnel is lifted by 75 % beyond the dead zone,
    # up to the exposure limit (lifting further only lifts noise)
    assert kf.stops[i_out] == pytest.approx(0.0, abs=0.05)
    static_stops = corr.values["exposure_stops"]
    # beyond the dead zone, 75 % of the move (more with the clip's contrast), up to the moment limit of +3 stops
    assert 0.75 * (3.4 - S["dynamic"]["dead_stops"]) - 0.1 <= kf.stops[i_in]
    assert static_stops + kf.stops[i_in] <= S["dynamic"]["max_stops_up"] + 1e-6
    assert key_after(thumbs[2], kf, i_in) > keys[2] + 1.0
    # contrast and saturation are one value each (Resolve can't keyframe contrast exactly)
    assert len({repr(x) for x in kf.nodes[c.CONTRAST]}) == 1
    assert len({repr(x) for x in kf.nodes[c.SATURATION]}) == 1


def test_small_moves_are_composition(out_lut):
    frames, thumbs = clip([0.0, 0.6, -0.4, 0.5, 0.0, -0.3])            # within the dead zone
    meas, corr = static(frames, thumbs, out_lut)
    assert dyn.plan(frames, meas.per_sample, meas, corr, thumbs, out_lut, S) is None


def test_one_odd_sample_is_ignored(out_lut):
    frames, thumbs = clip([0.0, 0.0, 2.5, 0.0, 0.0])                    # someone walks past the lens
    meas, corr = static(frames, thumbs, out_lut)
    assert dyn.plan(frames, meas.per_sample, meas, corr, thumbs, out_lut, S) is None


def test_light_change_keyframes_white_balance(out_lut):
    lights = [light(3200)] * 3 + [light(6500)] * 3                     # from tungsten inside to daylight
    frames, thumbs = clip([0.0] * 6, lights)
    meas, corr = static(frames, thumbs, out_lut)
    cfg = settings.load(None)
    cfg["measure"]["white_balance"]["learned"] = False
    cfg["dynamic"]["min_wb_confidence"] = 0.0                           # synthetic mixed clip: the test is the maths
    kf = dyn.plan(frames, meas.per_sample, meas, corr, thumbs, out_lut, cfg)
    assert kf is not None and "white balance" in kf.reason
    first, last = kf.nodes[c.WHITE_BALANCE][0].offset, kf.nodes[c.WHITE_BALANCE][-1].offset
    assert first[2] - first[0] > last[2] - last[0]                      # more blue under tungsten


def test_simplify_keeps_corners():
    frames = list(range(0, 110, 10))
    values = [np.array([0.0]) if f < 50 else np.array([min(1.0, (f - 50) / 20)]) for f in frames]
    assert dyn._simplify(frames, values, 0.01, 16) == [0, 50, 70, 100]
    assert dyn._simplify(frames, [np.array([0.0])] * len(frames), 0.01, 16) == []


def test_covered_frames_are_bridged(out_lut):
    frames, thumbs = clip([0.0, 0.0, 0.0, 0.0, 0.0])
    thumbs[2] = np.zeros_like(thumbs[2])                                # the lens covered for a moment
    meas, corr = static(frames, thumbs, out_lut)
    assert meas.per_sample[2]["usable"] is False and meas.samples == 4 and len(meas.per_sample) == 5
    assert abs(meas.exposure_stops) < 0.3                               # the black frame doesn't pull the key
    assert dyn.plan(frames, meas.per_sample, meas, corr, thumbs, out_lut, S) is None
