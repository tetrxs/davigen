import pytest

np = pytest.importorskip("numpy")

from davigen.edit import music, selects  # noqa: E402
from davigen.edit.watch import Watch, measure_audio, measure_frames, phase_shift, sharpness  # noqa: E402
from davigen.edit import settings as edit_settings  # noqa: E402

CFG = edit_settings.load(None)["selects"]


def click_track(bpm=128.0, seconds=60.0, accent=4, loud_from=None, rate=music.RATE, seed=0):
    """Kick-like clicks on every beat, louder on the bar's first beat, plus quiet noise; optionally louder later."""
    rng = np.random.default_rng(seed)
    n = int(seconds * rate)
    out = rng.normal(0, 0.01, n).astype("float32")
    t = np.arange(int(0.08 * rate)) / rate
    kick = np.sin(2 * np.pi * 60 * t) * np.exp(-t * 40)
    hat = rng.normal(0, 1, len(t)) * np.exp(-t * 200) * 0.3
    period = 60.0 / bpm
    k = 0
    while (start := int((0.5 + k * period) * rate)) + len(t) < n:
        gain = 1.0 if k % accent == 0 else 0.45
        if loud_from is not None and start / rate >= loud_from:
            gain *= 2.5
            out[start:start + len(t)] += hat * gain
        out[start:start + len(t)] += (kick * gain).astype("float32")
        k += 1
    return out


def test_tempo_beats_and_bars():
    m = music.analyse(click_track(128.0))
    assert m.tempo == pytest.approx(128.0, abs=1.5)
    period = 60.0 / 128.0
    truth = 0.5 + period * np.arange(200)
    errors = [np.min(np.abs(truth - b)) for b in m.beats[2:-2]]
    assert np.median(errors) < 0.03                               # within ~one onset frame
    first_downs = np.array(m.downbeats[:5])
    bar = 4 * period
    phase = ((first_downs - 0.5) / bar) % 1
    assert np.all((phase < 0.05) | (phase > 0.95))                 # downbeats on the accented clicks


@pytest.mark.parametrize("bpm", [92.0, 150.0])
def test_other_tempos(bpm):
    assert music.analyse(click_track(bpm, 40)).tempo == pytest.approx(bpm, rel=0.03)


def test_sections_find_the_louder_part():
    m = music.analyse(click_track(120.0, 64.0, loud_from=32.5))
    boundaries = [s["start"] for s in m.sections[1:]]
    assert any(abs(b - 32.5) < 2.5 for b in boundaries), m.sections
    assert m.sections[-1]["energy"] > m.sections[0]["energy"]


def test_phase_shift_and_sharpness():
    rng = np.random.default_rng(1)
    base = rng.random((96, 128)).astype("float32")
    moved = np.roll(base, (3, -5), axis=(0, 1))
    win = np.outer(np.hanning(96), np.hanning(128)).astype("float32")
    assert phase_shift(moved, base, win) == (-5.0, 3.0)
    blurred = (base + np.roll(base, 1, 0) + np.roll(base, 1, 1) + np.roll(base, (1, 1), (0, 1))) / 4
    assert sharpness(base) > 2 * sharpness(blurred)


def synthetic_watch(n=150, fps=5.0, voice_from=None):
    """30 s: steady and sharp, then 6 s of shake, then 4 s of a covered lens, then steady again."""
    rng = np.random.default_rng(2)
    motion = np.zeros((n, 2))
    shake = np.full(n, 0.02)
    sharp = np.full(n, 0.2)
    bright = np.full(n, 0.5)
    detail = np.full(n, 0.02)
    shake[40:70] = 0.6                                  # 8–14 s shaking
    bright[90:110] = 0.02                                # 18–22 s pocket
    detail[90:110] = 0.001
    m = int(n / fps / 0.2)
    voice = np.zeros(m)
    if voice_from is not None:
        voice[int(voice_from / 0.2):int((voice_from + 3) / 0.2)] = 0.9
    return Watch(fps=fps, duration=n / fps, motion=motion + rng.normal(0, 0.01, (n, 2)), shake=shake,
                 sharpness=sharp, brightness=bright, detail=detail, change=np.zeros(n),
                 loudness=np.full(m, -30.0), voice=voice)


def test_segments_good_unusable_speech():
    segs = selects.segments(synthetic_watch(voice_from=24.0), CFG)
    kinds = [(s.kind, round(s.start), round(s.end)) for s in segs]
    good = [s for s in segs if s.kind == selects.GOOD]
    bad = [s for s in segs if s.kind == selects.UNUSABLE]
    assert any(s.start < 1 and 6 < s.end < 9 for s in good), kinds
    assert any(abs(s.start - 18) < 1.5 and abs(s.end - 22) < 1.5 for s in bad), kinds
    assert any(abs(s.start - 8) < 1.5 for s in bad), kinds             # the shake
    speech = [s for s in segs if s.kind == selects.SPEECH]
    assert len(speech) == 1 and abs(speech[0].start - 24) < 0.6
    assert all(0 < s.rating <= 1 for s in good)


def test_calmest_window():
    w = synthetic_watch()
    w.shake[0:10] = 0.3                                  # the first 2 s are shakier
    seg = selects.Segment(selects.GOOD, 0.0, 7.0)
    start, end = selects.calmest_window(w, seg, 3.0)
    assert start >= 2.0 and end - start == pytest.approx(3.0)


def test_measure_frames_and_audio_shapes():
    rng = np.random.default_rng(3)
    frames = rng.random((12, 60, 80)).astype("float32")
    frames[6:] = np.roll(frames[5], 4, axis=1)          # a pan from frame 6 on
    out = measure_frames(frames, 5.0)
    assert out["motion"].shape == (12, 2) and out["sharpness"].shape == (12,)
    rate = 16000
    t = np.arange(rate * 3) / rate
    speechy = np.sin(2 * np.pi * 800 * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 4 * t)) * 0.3   # 4 Hz syllables
    steady = np.sin(2 * np.pi * 800 * t) * 0.3
    v_speech = measure_audio(speechy.astype("float32"))["voice"]
    v_steady = measure_audio(steady.astype("float32"))["voice"]
    assert v_speech[3:-3].mean() > 0.5 > v_steady[3:-3].mean()
