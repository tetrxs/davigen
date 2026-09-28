import json

import pytest

np = pytest.importorskip("numpy")

import fake_resolve as fr  # noqa: E402 - tests/ is on sys.path under pytest
from davigen.config import Config  # noqa: E402
from davigen.creator import Reporter  # noqa: E402
from davigen.edit import apply, decode, run, watch  # noqa: E402
from test_edit_music_selects import click_track, synthetic_watch  # noqa: E402


@pytest.fixture
def project(tmp_path, monkeypatch):
    info = tmp_path / "00_ADMIN" / "PROJECT_INFO"
    info.mkdir(parents=True)
    (info / "davigen.json").write_text(json.dumps({"format": {"width": 3240, "height": 2160, "fps": 25,
                                                              "aspect": "3:2"}}))
    clips = [fr.MediaPoolItem(f"/footage/P10000{n}.MOV", "G_LUMIX_S1II_VLOG", 750, None) for n in range(6)]
    proj = fr.Project("TEST", [fr.Timeline("TL_01_ASSEMBLY_3X2_25_v001")], [])
    proj.mp.root.clips = clips
    monkeypatch.setattr(decode, "available", lambda: True)
    monkeypatch.setattr(watch, "watch", lambda path, cache=None: synthetic_watch(voice_from=24.0))
    monkeypatch.setattr(decode, "audio", lambda path, rate=22050, duration=0.0: click_track(120.0, 40.0))
    return fr.Resolve(proj), proj, clips, tmp_path


def test_selects_only(project):
    resolve, proj, clips, base = project
    rep = Reporter(run.STEPS)
    record = run.edit_assist(resolve, Config(), rep, base=base)
    assert rep.steps["music"]["state"] == "skipped" and rep.steps["timeline"]["state"] == "done"
    marks = clips[0].markers
    colours = {m["color"] for m in marks.values()}
    assert {"Green", "Red", "Blue"} <= colours
    assert all(m["customData"] == apply.MARKER_DATA for m in marks.values())
    tl = next(t for t in proj.timelines if t.name.startswith("TL_00_SELECTS_AUTO"))
    assert tl.name == "TL_00_SELECTS_AUTO_3X2_25_v001" and len(tl.items) >= 6
    assert record["selects_timeline"] == tl.name
    assert (base / "00_ADMIN/PROJECT_INFO/edit_assist.json").exists()
    # again: a new version, markers replaced not doubled
    run.edit_assist(resolve, Config(), Reporter(run.STEPS), base=base)
    assert any(t.name.endswith("_v002") for t in proj.timelines)
    assert len(clips[0].markers) == len(marks)


def test_user_markers_survive(project):
    resolve, proj, clips, base = project
    clips[1].AddMarker(3, "Yellow", "mine", "", 1, "")
    run.edit_assist(resolve, Config(), Reporter(run.STEPS), base=base)
    assert clips[1].markers[3]["name"] == "mine"


def test_rough_cut_to_music(project):
    resolve, proj, clips, base = project
    rep = Reporter(run.STEPS)
    record = run.edit_assist(resolve, Config(), rep, music_path="/music/song.wav", base=base)
    assert rep.steps["roughcut"]["state"] == "done", rep.steps
    cut = next(t for t in proj.timelines if t.name.startswith("TL_02_EDIT_AUTO"))
    video = [i for i in cut.items if i.kind == "video"]
    audio = [i for i in cut.items if i.kind == "audio"]
    assert len(video) == len(record["shots"]) >= 4
    assert audio and audio[0].start == 90000                            # music from the start of A1
    for a, b in zip(video, video[1:]):
        assert a.start + a.duration == b.start                          # back to back
    starts = [(i.start - 90000) / 25 for i in video]
    beats = set(round(b, 2) for b in record["music"]["beats"]) | {0.0}
    assert all(min(abs(t - b) for b in beats) <= 0.041 for t in starts)  # every cut within one frame of a beat
    audio_bin = next(f for f in proj.mp.root.subs if f.name == "04_AUDIO")
    music_clip = next(f for f in audio_bin.subs if f.name == "MUSIC").clips[0]
    assert any("bar" in m["name"] for m in music_clip.markers.values())
