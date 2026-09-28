import pytest

np = pytest.importorskip("numpy")

from davigen.edit import roughcut as rc  # noqa: E402
from davigen.edit import selects as sel  # noqa: E402
from davigen.edit import settings as edit_settings  # noqa: E402
from davigen.edit.music import Music  # noqa: E402

CFG = edit_settings.load(None)["rough_cut"]


def song(bpm=120.0, seconds=64.0, calm_until=32.0):
    period = 60 / bpm
    beats = [round(0.5 + i * period, 3) for i in range(int((seconds - 0.5) / period))]
    return Music(duration=seconds, tempo=bpm, beats=beats, downbeats=beats[::4],
                 sections=[{"start": 0.0, "end": calm_until, "energy": 0.2},
                           {"start": calm_until, "end": seconds, "energy": 0.9}])


def clip(i, created, goods, speech=()):
    segs = [sel.Segment(sel.GOOD, a, b, r) for a, b, r in goods] + [sel.Segment(sel.SPEECH, a, b) for a, b in speech]
    return rc.ClipPlan(id=f"c{i}", name=f"C{i}.MOV", created=created, order=i, segments=segs)


def test_grid_follows_section_energy():
    cuts = rc.grid(song(), CFG)
    m = song()
    gaps = np.diff([m.beats[c] for c in cuts])
    calm = [g for c, g in zip(cuts, gaps) if m.beats[c] < 31]
    loud = [g for c, g in zip(cuts, gaps) if m.beats[c] > 33]
    assert all(g == pytest.approx(4.0, abs=0.01) for g in calm)          # two bars at 120 BPM
    assert all(g == pytest.approx(2.0, abs=0.01) for g in loud)          # one bar


def test_plan_cuts_on_beats_in_recording_order():
    clips = [clip(i, f"2026-09-25T10:{i:02d}:00Z", [(0.0, 30.0, 0.5 + i / 40)]) for i in range(12)]
    shots = rc.plan(clips, song(), CFG)
    m = song()
    beat_times = set(m.beats) | {0.0, m.duration}
    assert shots and shots[0].record_start == 0.0
    for a, b in zip(shots, shots[1:]):
        assert a.record_end == b.record_start                            # no gaps
    assert all(any(abs(s.record_end - t) < 1e-6 for t in beat_times) for s in shots)
    assert [s.clip_id for s in shots] == sorted((s.clip_id for s in shots), key=lambda c: int(c[1:]))
    counts = {c: sum(1 for s in shots if s.clip_id == c) for c in {s.clip_id for s in shots}}
    assert max(counts.values()) <= CFG["max_per_clip"]
    for s in shots:
        assert s.source_end - s.source_start == pytest.approx(s.record_end - s.record_start, abs=1e-3)


def test_speech_is_skipped_and_short_stretches_end_on_a_beat():
    clips = [clip(0, "2026-09-25T10:00:00Z", [(0.0, 20.0, 0.9)], speech=[(0.0, 20.0)]),
             clip(1, "2026-09-25T10:01:00Z", [(0.0, 3.1, 0.8)]),
             clip(2, "2026-09-25T10:02:00Z", [(0.0, 40.0, 0.7)])]
    shots = rc.plan(clips, song(), CFG)
    assert "c0" not in {s.clip_id for s in shots}
    short = next(s for s in shots if s.clip_id == "c1")
    assert short.record_end - short.record_start <= 3.1 + 1e-6
    assert (short.record_end - 0.5) / 0.5 == pytest.approx(round((short.record_end - 0.5) / 0.5), abs=1e-6)


def test_not_enough_footage_ends_early():
    shots = rc.plan([clip(0, "2026-09-25T10:00:00Z", [(0.0, 10.0, 0.9)])], song(), CFG)
    assert 0 < len(shots) <= 3 and shots[-1].record_end < 64
