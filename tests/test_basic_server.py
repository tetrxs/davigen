import threading

import pytest

pytest.importorskip("numpy")

from davigen import creator, server  # noqa: E402
from davigen.config import Config  # noqa: E402


def test_basic_refuses_while_something_runs():
    app = server.App(resolve=None, cfg=Config())
    gate = threading.Event()
    app.job = threading.Thread(target=gate.wait, daemon=True)
    app.job.start()
    try:
        assert app.start_basic({"dry_run": True}) == {"ok": False, "error": "Something is already running"}
    finally:
        gate.set()


def test_step_reporter_forwards_into_one_step():
    rep = creator.Reporter([("basic", "Basic correction")])
    sub = creator._StepReporter(rep, "basic", {"sample": "Sample frames"})
    sub.start("sample", "rendering 12 frames")
    assert rep.steps["basic"]["detail"] == "Sample frames · rendering 12 frames"
    sub.warn(["clip A: node missing"])
    assert rep.warnings == ["clip A: node missing"]


def test_clip_detail_and_preview(tmp_path, monkeypatch):
    from davigen import colormath
    if not colormath.available():
        pytest.skip("colour-science not installed")
    import test_basic_run as tr
    from davigen.basic import run
    d = tmp_path / "luts"
    d.mkdir()
    luts = (str(colormath.input_lut("V-Log", "V-Gamut", d / "in.cube", size=17)),
            str(colormath.output_lut(d / "out.cube", size=33)))
    resolve, proj, item = tr.tunnel(luts)
    run.basic_correction(resolve, Config(), creator.Reporter(run.STEPS), base=tmp_path)
    monkeypatch.setattr(creator, "project_base", lambda p: tmp_path)
    app = server.App(resolve=resolve, cfg=Config())
    detail = app.basic_clip({"id": [item.uid]})
    assert detail["ok"] and detail["keyframes"]["frames"] and detail["samples_over_time"]
    assert detail["correction"]["values"]["exposure_reason"]
    png = app.basic_preview({"id": [item.uid], "frame": ["200"]})    # no ffmpeg for a fake file: the cache
    assert png.startswith(b"\x89PNG")
    assert app.basic_clip({"id": ["nope"]})["ok"] is False


def test_look_setup(tmp_path, monkeypatch):
    from davigen import colormath
    if not colormath.available():
        pytest.skip("colour-science not installed")
    import test_basic_run as tr
    from davigen.basic import run
    d = tmp_path / "luts"
    d.mkdir()
    luts = (str(colormath.input_lut("V-Log", "V-Gamut", d / "in.cube", size=17)),
            str(colormath.output_lut(d / "out.cube", size=33)))
    resolve, proj, item = tr.tunnel(luts)
    run.basic_correction(resolve, Config(), creator.Reporter(run.STEPS), base=tmp_path)
    monkeypatch.setattr(creator, "project_base", lambda p: tmp_path)
    app = server.App(resolve=resolve, cfg=Config())
    look = app.basic_look({})
    assert look["look"]["contrast"] == "medium" and {o["name"] for o in look["options"]["warmth"]} >= {"neutral", "warm"}
    assert look["samples"] and look["samples"][0]["id"] == item.uid
    saved = app.basic_look_save({"look": {"warmth": "warm", "nonsense": "x"}})
    assert saved["look"]["warmth"] == "warm" and app.basic_look({})["look"]["warmth"] == "warm"
    for option in ("before", "strong"):
        png = app.basic_look_preview({"id": [item.uid], "dim": ["contrast"], "option": [option]})
        assert png.startswith(b"\x89PNG")
    record = run.basic_correction(resolve, Config(), creator.Reporter(run.STEPS), base=tmp_path, recompute=True)
    assert record["look"]["warmth"] == "warm"
