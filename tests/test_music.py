"""Song analysis: tempo, bars, parts, phrases, accents and (estimated) vocal entries on synthetic music."""

import pytest

np = pytest.importorskip("numpy")

from davigen import music  # noqa: E402


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


def test_phrases_every_four_bars():
    m = music.analyse(click_track(120.0, 40))
    assert m.phrases == m.downbeats[::4] and len(m.phrases) >= 4


def with_crashes(track, times, rate=music.RATE, seed=3):
    """Loud noise bursts (a crash cymbal and a kick) at the given seconds."""
    rng = np.random.default_rng(seed)
    out = track.copy()
    t = np.arange(int(0.3 * rate)) / rate
    burst = rng.normal(0, 1, len(t)) * np.exp(-t * 12) * 0.9 + np.sin(2 * np.pi * 55 * t) * np.exp(-t * 20)
    for s in times:
        a = int(s * rate)
        out[a:a + len(t)] += burst[: len(out) - a].astype("float32")
    return out


def test_accents_are_the_hits_that_stand_out():
    track = with_crashes(click_track(120.0, 48.0), [16.5, 32.5, 40.75])
    m = music.analyse(track)
    times = [a["time"] for a in m.accents]
    for crash in (16.5, 32.5, 40.75):
        assert any(abs(t - crash) < 0.08 for t in times), (crash, times)
    # the regular accented downbeat (every bar louder) is a pattern, not an accent
    assert len(m.accents) <= 6


def test_no_accents_in_a_steady_track():
    assert len(music.analyse(click_track(120.0, 40.0)).accents) <= 1


def voice(seconds, starts, length=3.0, rate=music.RATE, f0=220.0):
    """A sung-like tone (harmonics, vibrato) at the given starts, in the middle of the stereo image."""
    n = int(seconds * rate)
    out = np.zeros(n, dtype="float32")
    t = np.arange(int(length * rate)) / rate
    phase = 2 * np.pi * np.cumsum(f0 * (1 + 0.01 * np.sin(2 * np.pi * 5.5 * t))) / rate
    tone = sum(np.sin(k * phase) / k for k in range(1, 9)) * 0.25
    env = np.minimum(1, t / 0.05) * np.minimum(1, (length - t) / 0.1)
    for s in starts:
        a = int(s * rate)
        seg = (tone * env)[: n - a]
        out[a:a + len(seg)] += seg.astype("float32")
    return out


def test_vocal_entries_in_the_centre():
    seconds = 40.0
    drums = click_track(120.0, seconds)
    rng = np.random.default_rng(5)
    wide = rng.normal(0, 0.02, len(drums)).astype("float32")         # instruments differ between the channels
    v = voice(seconds, [6.0, 14.0, 26.0])
    stereo = np.stack([drums + wide + v, drums * 0.8 - wide + v], axis=1)
    entries = music.vocal_entries(stereo)
    for s in (6.0, 14.0, 26.0):
        assert any(abs(e - s) < 0.4 for e in entries), (s, entries)
    assert len(entries) <= 5


def test_no_vocals_without_a_voice():
    stereo = np.stack([click_track(120.0, 30.0), click_track(120.0, 30.0, seed=1)], axis=1)
    assert len(music.vocal_entries(stereo)) <= 1
