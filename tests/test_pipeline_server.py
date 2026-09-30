"""The pipeline through davigen's server: new project, a step waiting for input, the assets page, deleting."""

import time

import pytest

pytest.importorskip("numpy")

import test_pipeline_flows as tf  # noqa: E402
from davigen import config, delete, filesystem, server  # noqa: E402
from davigen.config import Config  # noqa: E402

env = tf.env


def wait(app, until, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        snap = app.progress()
        if until(snap):
            return snap
        time.sleep(0.02)
    raise AssertionError(f"timed out: {app.progress()}")


@pytest.fixture
def app(env, monkeypatch, tmp_path):
    resolve, card, root = env
    monkeypatch.setattr(config, "SETTINGS", tmp_path / "data" / "settings.json")
    monkeypatch.setattr(server, "settings", lambda: {"transfer": "copy", "default_root": str(root)})
    a = server.App(resolve, Config())
    a.assets = [tf.clip(card, "A.MOV", "2026-09-25T10:00:00Z"), tf.clip(card, "B.MOV", "2026-09-25T11:00:00Z")]
    return a, resolve, card, root


def test_new_project_pauses_for_the_look_and_goes_on(app):
    a, resolve, card, root = app
    body = {"project": "Trip 2026", "root": str(root), "actions": ["basic_correction"],
            "format": {"width": 3240, "height": 2160, "fps": 25, "aspect": "3:2", "deliveries": ["master"]},
            "groups": [{"id": "LUMIX_S1II:VLOG", "include": True, "profile": "VLOG"}]}
    assert a.start_create(body) == {"ok": True}
    snap = wait(a, lambda s: any(x["state"] == "input" for x in s["steps"]))
    step = next(x for x in snap["steps"] if x["state"] == "input")
    assert step["id"] == "basic_correction" and step["form"] == "basic_look"
    assert {i["id"] for i in step["inputs"]} == {"brightness", "contrast", "warmth", "saturation"}
    assert a.run_input({"step": "basic_correction", "values": {**step["values"], "contrast": "soft"}})["ok"]
    snap = wait(a, lambda s: s["done"])
    assert snap["state"] == "done", snap["error"]
    base = root / "TRIP_2026"
    assert (card / "A.MOV").exists() and list((base / "01_MEDIA").rglob("A.MOV"))   # copied: original stays
    import json
    look = json.loads((base / "00_ADMIN/PROJECT_INFO/basic_correction/look.json").read_text())
    assert look["contrast"] == "soft"
    # the assets page: both clips, every action done
    rows = a.assets_list({})
    assert rows["ok"] and rows["live"] and len(rows["assets"]) == 2
    assert all(r["status"]["basic_correction"] == "done" for r in rows["assets"])


def test_stop_while_waiting(app):
    a, resolve, card, root = app
    body = {"project": "STOPPED", "root": str(root), "actions": ["basic_correction"],
            "format": {"width": 3240, "height": 2160, "fps": 25, "aspect": "3:2"}}
    a.start_create(body)
    wait(a, lambda s: any(x["state"] == "input" for x in s["steps"]))
    assert a.run_stop({})["ok"]
    snap = wait(a, lambda s: s["done"])
    assert snap["state"] == "stopped"


def test_delete_project(app, monkeypatch):
    a, resolve, card, root = app
    a.start_create({"project": "GONE", "root": str(root),
                    "format": {"width": 3240, "height": 2160, "fps": 25, "aspect": "3:2"}})
    wait(a, lambda s: s["done"])
    base = root / "GONE"
    trashed = []
    monkeypatch.setattr(delete, "trash", lambda p: trashed.append(p))
    assert a.delete_project({"folder": str(base), "confirm": "nope"})["ok"] is False
    assert a.delete_project({"folder": str(base), "confirm": "GONE"}) == {"ok": True}
    snap = wait(a, lambda s: s["done"])
    assert not snap["error"], snap["error"]
    assert trashed == [base] and (base / "02_RESOLVE/01_PROJECT_FILES/GONE_before_delete.drp").exists()
    assert "GONE" not in resolve.pm.folders["VIDEO_PROJECTS/ACTIVE"]
    assert str(base) not in [p["folder"] for p in filesystem.recent_projects(check=False)]
