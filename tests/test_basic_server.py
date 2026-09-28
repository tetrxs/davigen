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


def test_plan_default_is_off():
    assert creator.Plan("X", "/tmp", []).basic_correction is False



def test_step_reporter_forwards_into_one_step():
    rep = creator.Reporter([("basic", "Basic correction")])
    sub = creator._StepReporter(rep, "basic", {"sample": "Sample frames"})
    sub.start("sample", "rendering 12 frames")
    assert rep.steps["basic"]["detail"] == "Sample frames · rendering 12 frames"
    sub.warn(["clip A: node missing"])
    assert rep.warnings == ["clip A: node missing"]
