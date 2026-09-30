"""The pipeline runner with toy actions: order, what is left to do, input, transaction, errors, stop, crash."""

import json
import time

import pytest

from davigen.pipeline import core, runner
from davigen.pipeline.assets import Asset, AssetStore
from davigen.pipeline.core import DONE, PROJECT, TODO, Action, Check, Context, Input, Option


class Log(list):
    pass


LOG = Log()


def _reg(cls):
    core.REGISTRY[cls.id] = cls()
    return cls


@_reg
class Setup(Action):
    id, label, scope = "t_setup", "Setup", PROJECT

    def check(self, ctx, asset):
        return Check(DONE if ctx.shared.get("setup") else TODO)

    def run_one(self, ctx, asset, values, progress):
        ctx.shared["setup"] = True
        LOG.append("setup")


@_reg
class BringIn(Action):
    id, label, kinds, transactional = "t_bring", "Bring in", frozenset({"camera", "music"}), True

    def check(self, ctx, asset):
        return Check(DONE if asset.path else TODO)

    def run_one(self, ctx, asset, values, progress):
        if asset.name == "broken.mov":
            raise core.ActionError("disk full")
        asset.path = f"/project/{asset.name}"
        ctx.store.put(asset)
        LOG.append(f"bring {asset.name}")
        return {"was": asset.source}

    def undo_one(self, ctx, asset, info):
        asset.path = ""
        ctx.store.drop(asset.id)
        LOG.append(f"undo bring {asset.name}")


@_reg
class Grade(Action):
    id, label, kinds, mandatory = "t_grade", "Grade", frozenset({"camera"}), False
    after = ("t_bring",)
    on_error = "skip"
    inputs = (Input("look", "Look", options=[Option("even", "Even"), Option("dark", "Darker")], default="even"),)

    def check(self, ctx, asset):
        return Check(DONE if "t_grade" in asset.done else TODO)

    def run_one(self, ctx, asset, values, progress):
        if asset.name == "bad.mov":
            raise RuntimeError("no frames")
        LOG.append(f"grade {asset.name} {values['look']}")


@_reg
class Mark(Action):
    id, label, kinds, mandatory, batch = "t_mark", "Mark", frozenset({"music"}), False, True
    after = ("t_bring",)

    def check(self, ctx, asset):
        return Check(DONE if "t_mark" in asset.done else TODO)

    def run_batch(self, ctx, assets, values, progress):
        for n, a in enumerate(assets, 1):
            progress(fraction=n / len(assets))
            LOG.append(f"mark {a.name}")
        return {}


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    LOG.clear()
    monkeypatch.setattr(runner, "PENDING", tmp_path / "pending_runs.json")
    (tmp_path / "00_ADMIN" / "PROJECT_INFO").mkdir(parents=True)
    return Context(resolve=None, cfg=None, base=tmp_path, store=AssetStore(tmp_path))


def assets(*names, kind="camera"):
    return [Asset(id=f"id_{n}", kind=kind, name=n, source=f"/card/{n}") for n in names]


def test_order_follows_after():
    cat = core.REGISTRY
    assert runner.order(["t_grade", "t_setup", "t_bring"], cat) == ["t_bring", "t_grade", "t_setup"]
    assert runner.order(["t_grade"], cat) == ["t_grade"]            # dependencies only count when in the run


def test_run_does_only_what_is_left(ctx):
    items = assets("a.mov", "b.mov") + assets("song.wav", kind="music")
    r = runner.Run(ctx, ["t_setup", "t_bring", "t_grade", "t_mark"], items, values={"t_grade": {"look": "dark"}})
    r.run()
    assert r.state == "done", r.error
    assert LOG == ["setup", "bring a.mov", "bring b.mov", "bring song.wav", "grade a.mov dark", "grade b.mov dark",
                   "mark song.wav"]
    snap = r.snapshot()
    assert [s["state"] for s in snap["steps"]] == ["done"] * 4
    assert snap["steps"][2]["total"] == 2                               # only the camera clips
    # the same again: nothing left, every step says so
    LOG.clear()
    r2 = runner.Run(ctx, ["t_setup", "t_bring", "t_grade", "t_mark"], items, values={"t_grade": {"look": "dark"}})
    r2.run()
    assert LOG == [] and all(s["state"] == "skipped" for s in r2.snapshot()["steps"])
    assert r2.snapshot()["steps"][2]["detail"] == "already done (2)"
    # a new clip later: only it is done
    LOG.clear()
    r3 = runner.Run(ctx, ["t_bring", "t_grade"], items + assets("c.mov"), values={"t_grade": {"look": "even"}})
    r3.run()
    assert LOG == ["bring c.mov", "grade c.mov even"]


def test_redo_forces_the_action(ctx):
    items = assets("a.mov")
    runner.Run(ctx, ["t_bring", "t_grade"], items, values={"t_grade": {}}).run()
    LOG.clear()
    runner.Run(ctx, ["t_grade"], items, values={"t_grade": {"look": "dark"}}, redo={"t_grade"}).run()
    assert LOG == ["grade a.mov dark"]


def test_pauses_for_input(ctx):
    r = runner.Run(ctx, ["t_bring", "t_grade"], assets("a.mov"))
    r.start()
    for _ in range(100):
        if r.snapshot()["steps"][1]["state"] == "input":
            break
        time.sleep(0.02)
    snap = r.snapshot()
    assert snap["steps"][0]["state"] == "done" and snap["steps"][1]["state"] == "input"
    assert snap["steps"][1]["inputs"][0]["id"] == "look" and snap["steps"][1]["values"] == {"look": "even"}
    assert LOG == ["bring a.mov"]
    assert not r.provide("t_bring", {})
    assert r.provide("t_grade", {"look": "dark", "unknown": 1})
    r.join(5)
    assert r.state == "done" and LOG[-1] == "grade a.mov dark"


def test_transaction_is_undone_when_bringing_in_fails(ctx):
    items = assets("a.mov", "b.mov", "broken.mov")
    r = runner.Run(ctx, ["t_bring", "t_grade"], items, values={"t_grade": {}})
    r.run()
    assert r.state == "failed" and "disk full" in r.error and "put back" in r.error
    assert LOG == ["bring a.mov", "bring b.mov", "undo bring b.mov", "undo bring a.mov"]
    assert len(ctx.store) == 0 and all(not a.path for a in items)


def test_later_actions_keep_what_they_finished(ctx):
    items = assets("a.mov", "bad.mov", "c.mov")
    r = runner.Run(ctx, ["t_bring", "t_grade"], items, values={"t_grade": {}})
    r.run()
    assert r.state == "done"
    grade = r.snapshot()["steps"][1]
    assert grade["errors"] == ["bad.mov: no frames"] and "1 failed" in grade["detail"]
    assert "t_grade" in items[0].done and "t_grade" not in items[1].done and "t_grade" in items[2].done
    assert len(ctx.store) == 3                                          # nothing brought in was undone


def test_stop_while_waiting_for_input_undoes_nothing_after_commit(ctx):
    r = runner.Run(ctx, ["t_bring", "t_grade"], assets("a.mov"))
    r.start()
    for _ in range(100):
        if r.snapshot()["steps"][1]["state"] == "input":
            break
        time.sleep(0.02)
    r.stop()
    r.join(5)
    assert r.state == "stopped"
    assert len(ctx.store) == 1                                          # the bring-in was committed already


def test_journal_and_recovery_after_a_crash(ctx, monkeypatch):
    items = assets("a.mov", "b.mov", "c.mov")
    r = runner.Run(ctx, ["t_bring", "t_grade"], items, values={"t_grade": {}})

    class Crash(BaseException):
        """davigen or the Mac going down: no finally-cleanup reaches the journal."""

    original = BringIn.run_one

    def crash_on_c(self, ctx_, asset, values, progress):
        if asset.name == "c.mov":
            raise Crash()
        return original(self, ctx_, asset, values, progress)
    monkeypatch.setattr(BringIn, "run_one", crash_on_c)
    with pytest.raises(Crash):
        r.run()
    journal = json.loads(r.journal_file.read_text())
    assert journal["committed"] is False and len(journal["tx"]) == 2
    assert journal["last"] == {"step": "t_bring", "unit": "id_c.mov", "phase": "start"}
    assert runner._pending() == [str(r.journal_file)]
    ctx.store.save()
    reports = runner.recover(lambda base: Context(resolve=None, cfg=None, base=base, store=AssetStore(base)))
    assert reports and reports[0]["undone"] == 2
    assert LOG[-2:] == ["undo bring b.mov", "undo bring a.mov"]
    assert runner._pending() == []
    assert json.loads(r.journal_file.read_text())["state"] == "interrupted"


def test_estimate_follows_progress(ctx):
    items = assets("a.mov", "b.mov")
    r = runner.Run(ctx, ["t_bring", "t_grade"], items, values={"t_grade": {}})
    r._estimate()
    assert r.snapshot()["remaining"] > 0
    assert [s.total for s in r.steps] == [2, 2]
