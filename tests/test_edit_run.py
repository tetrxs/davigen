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
    assert rep.steps["music"]["state"] == "skipped" and rep.steps["markers"]["state"] == "done"
    marks = clips[0].markers
    colours = {m["color"] for m in marks.values()}
    assert {"Green", "Red", "Blue"} <= colours
    assert all(m["customData"] == apply.MARKER_DATA for m in marks.values())
    assert [t.name for t in proj.timelines] == ["TL_01_ASSEMBLY_3X2_25_v001"]      # no timeline of its own
    assert record["selects"] and all(x["end"] > x["start"] for x in record["selects"])
    assert clips[0].flags == {"Green"} and "davigen good" in clips[0].metadata["Keywords"]
    assert "davigen speech" in clips[0].metadata["Keywords"]
    assert (base / "00_ADMIN/PROJECT_INFO/edit_assist.json").exists()
    # again: markers replaced, not doubled, and still no extra timeline
    run.edit_assist(resolve, Config(), Reporter(run.STEPS), base=base)
    assert len(clips[0].markers) == len(marks) and len(proj.timelines) == 1


def test_user_markers_survive(project):
    resolve, proj, clips, base = project
    clips[1].AddMarker(3, "Yellow", "mine", "", 1, "")
    clips[1].SetMetadata({"Keywords": "LUMIX_S1II,V-Log,beach"})
    run.edit_assist(resolve, Config(), Reporter(run.STEPS), base=base)
    run.edit_assist(resolve, Config(), Reporter(run.STEPS), base=base)
    assert clips[1].markers[3]["name"] == "mine"
    assert clips[1].metadata["Keywords"].split(",")[:3] == ["LUMIX_S1II", "V-Log", "beach"]      # the user's stay
    assert clips[1].metadata["Keywords"].count("davigen good") == 1


def test_rough_cut_to_music(project):
    resolve, proj, clips, base = project
    rep = Reporter(run.STEPS)
    record = run.edit_assist(resolve, Config(), rep, music_path="/music/song.wav", base=base)
    assert rep.steps["roughcut"]["state"] == "done", rep.steps
    cut = next(t for t in proj.timelines if t.name == "TL_02_EDIT_3X2_25_v001")      # the edit timeline
    assert record["rough_cut"] == cut.name
    video = [i for i in cut.items if i.kind == "video"]
    audio = [i for i in cut.items if i.kind == "audio"]
    assert audio and audio[0].track == cut.track_names["audio"].index("MUSIC") + 1    # on the MUSIC track
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


def test_transcripts_go_into_markers_and_srt(project, monkeypatch):
    from davigen.edit import transcribe
    resolve, proj, clips, base = project
    monkeypatch.setattr(transcribe, "supported", lambda: True)
    monkeypatch.setattr(transcribe, "available", lambda: True)
    calls = []

    def fake_run(jobs, model, timeout=3600):
        calls.append(jobs)
        out = []
        for n, j in enumerate(jobs):
            if n % 2:                                    # every other stretch: Whisper hears no words
                out.append({"text": "", "language": "", "segments": []})
            else:
                out.append({"text": "Wir haben jetzt alle Zeit der Welt.", "language": "de",
                            "segments": [{"start": j["start"], "end": j["end"], "text": "Wir haben jetzt alle Zeit der Welt."}]})
        return out
    monkeypatch.setattr(transcribe, "run", fake_run)
    rep = Reporter(run.STEPS)
    run.edit_assist(resolve, Config(), rep, base=base, transcribe_speech=True)
    assert rep.steps["transcribe"]["state"] == "done", rep.steps["transcribe"]
    assert all(j["start"] >= 0 and j["end"] > j["start"] for j in calls[0])
    notes = [m["note"] for c in clips for m in c.markers.values() if m["name"] == "davigen: speech"]
    assert notes and all("Zeit der Welt" in n for n in notes)            # dropped stretches lose their marker
    assert (base / "03_WORK/TRANSCRIPTS").is_dir() and list((base / "03_WORK/TRANSCRIPTS").glob("*.srt"))
    assert "Zeit der Welt" in (base / "00_ADMIN/PROJECT_INFO/transcripts.md").read_text()


def test_whisper_cleaning_and_srt():
    from davigen.edit import transcribe
    segs = [{"start": 0, "end": 2, "text": " Hallo zusammen.", "avg_logprob": -0.3, "no_speech_prob": 0.1},
            {"start": 2, "end": 4, "text": "Thank you.", "avg_logprob": -0.2, "no_speech_prob": 0.1},
            {"start": 4, "end": 6, "text": "mumble", "avg_logprob": -1.6, "no_speech_prob": 0.2},
            {"start": 6, "end": 8, "text": "wind", "avg_logprob": -0.4, "no_speech_prob": 0.9},
            {"start": 3661.5, "end": 3663.25, "text": "Später.", "avg_logprob": -0.4, "no_speech_prob": 0.1}]
    kept = transcribe.clean(segs)
    assert [k["text"] for k in kept] == ["Hallo zusammen.", "Später."]
    text = transcribe.srt(kept)
    assert "1\n00:00:00,000 --> 00:00:02,000\nHallo zusammen.\n" in text
    assert "01:01:01,500 --> 01:01:03,250" in text


def test_rough_cut_goes_into_the_empty_edit_timeline_and_never_over_an_edit(project):
    resolve, proj, clips, base = project
    edit = fr.Timeline("TL_02_EDIT_3X2_25_v001")
    proj.timelines.append(edit)
    run.edit_assist(resolve, Config(), Reporter(run.STEPS), music_path="/music/song.wav", base=base)
    assert edit.items and [t.name for t in proj.timelines].count("TL_02_EDIT_3X2_25_v001") == 1
    before = list(edit.items)
    record = run.edit_assist(resolve, Config(), Reporter(run.STEPS), music_path="/music/song.wav", base=base)
    assert record["rough_cut"] == "TL_02_EDIT_3X2_25_v002" and edit.items == before     # the edit stays
