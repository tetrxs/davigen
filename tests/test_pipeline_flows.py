"""The whole pipeline against a fake Resolve: a new project, adding later, a failed import, a clip removed in
Resolve, and a song with markers."""

import wave
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

import fake_pipeline as fp  # noqa: E402
from davigen import color, decode, filesystem, naming, template, transfer  # noqa: E402
from davigen.config import Config  # noqa: E402
from davigen.formats import Format  # noqa: E402
from davigen.pipeline import flows, runner, state  # noqa: E402
from davigen.pipeline.assets import Asset, AssetStore  # noqa: E402
from davigen.pipeline.core import Context  # noqa: E402
from test_music import click_track, with_crashes  # noqa: E402

CFG = Config()
GROUP = naming.group_name("LUMIX_S1II", CFG.profiles["VLOG"].short)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "PENDING", tmp_path / "data" / "pending_runs.json")
    monkeypatch.setattr(transfer, "PENDING", tmp_path / "data" / "pending_transfers.json")
    monkeypatch.setattr(filesystem, "PROJECTS", tmp_path / "data" / "projects.json")
    monkeypatch.setattr(template, "TEMPLATES", tmp_path / "data" / "templates")

    def fake_groups(resolve, project, cfg, groups, scratch, progress=None):
        made = {}
        for g in groups:
            grp = next((x for x in project.GetColorGroupsList() if x.GetName() == g["group_name"]), None) \
                or project.AddColorGroup(g["group_name"])
            grp.pre.SetLUT(1, f"davigen/IN_{g['profile']}.cube")
            grp.post.SetLUT(1, "davigen/OUT.cube")
            made[g["group_name"]] = grp
        return made, []
    monkeypatch.setattr(color, "setup_groups", fake_groups)
    monkeypatch.setattr(color, "CLIP_TEMPLATE", Path(__file__))            # exists: the structure gets applied

    from davigen.basic import carry, run as basic

    def fake_basic(resolve, cfg, rep, timeline_name="", base=None, only_paths=None, **kw):
        proj = resolve.GetProjectManager().GetCurrentProject()
        tl = next(t for t in proj.timelines if t.name == timeline_name)
        items = []
        for it in tl.items:
            path = it.clip.path
            written = only_paths is None or path in only_paths
            if written:
                it.versions.append("DAVIGEN_AUTO")
            items.append({"path": path, "outcome": {"written": {"01_EXPOSURE": True} if written else {}}})
        return {"items": items}
    monkeypatch.setattr(basic, "basic_correction", fake_basic)
    monkeypatch.setattr(carry, "carry_over", lambda *a, **k: {"written": 0, "warnings": []})

    card = tmp_path / "card" / "DCIM"
    card.mkdir(parents=True)
    root = tmp_path / "Movies"
    root.mkdir()
    resolve = fp.Resolve()
    return resolve, card, root


def clip(card: Path, name: str, when: str) -> Asset:
    f = card / name
    f.write_bytes(name.encode() * 1000)
    return Asset(id=f"id_{name}", kind="camera", name=name, source=str(f), size=f.stat().st_size, created=when,
                 camera_key="LUMIX_S1II", camera_name="Lumix S1II", make="Panasonic", model="DC-S1M2",
                 profile="VLOG", group=GROUP)


def song(card: Path, name: str = "song.wav", rate: int = 48000) -> Asset:
    f = card / name
    samples = with_crashes(click_track(120.0, 30.0, rate=rate), [12.5, 22.5], rate=rate)
    with wave.open(str(f), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(samples, -1, 1) * 32000).astype("<i2").tobytes())
    return Asset(id=f"id_{name}", kind="music", name=name, source=str(f), size=f.stat().st_size)


def context(resolve, root, name="TEST", new=True, transfer_mode="move"):
    base = root / name
    fmt = Format(3240, 2160, 25.0, "3:2", ["master", "16x9_uhd"])
    return Context(resolve=resolve, cfg=CFG, settings={"transfer": transfer_mode}, base=base, fmt=fmt,
                   store=AssetStore(base), spec={"new": new, "name": name})


LOOK = {"brightness": "even", "contrast": "medium", "warmth": "neutral", "saturation": "natural"}


def test_new_project_then_add_later(env):
    resolve, card, root = env
    items = [clip(card, "P1000001.MOV", "2026-09-25T10:00:00Z"), clip(card, "P1000002.MOV", "2026-09-25T09:00:00Z")]
    ctx = context(resolve, root)
    run = runner.Run(ctx, flows.new_project(["basic_correction"]), items, values={"basic_correction": LOOK})
    run.run()
    assert run.state == "done", run.error
    proj = resolve.pm.current
    base = ctx.base
    # the project: from the template, with the playback frame rate and every working folder in the project
    assert proj.name == "TEST" and "TEST" in resolve.pm.imported
    assert proj.settings["timelinePlaybackFrameRate"] == "25"
    assert proj.proxy_location == str(base / "03_WORK" / "PROXIES")
    assert proj.settings["perfCacheClipsLocation"] == str(base / "03_WORK" / "CACHE")
    assert proj.settings["projectMediaLocation"] == str(base / "03_WORK" / "PROJECT_MEDIA")
    assert not any("Playback" in m for m in ctx.manual)
    # the files: moved into the camera folder
    moved = sorted(p.name for p in (base / "01_MEDIA").rglob("*.MOV"))
    assert moved == ["P1000001.MOV", "P1000002.MOV"] and not list(card.glob("*.MOV"))
    # Resolve: camera bin, assembly in recording order, colour group, node structure, DAVIGEN_AUTO
    assembly = next(t for t in proj.timelines if t.name.startswith("TL_01_ASSEMBLY"))
    assert [i.clip.GetName() for i in assembly.items] == ["P1000002.MOV", "P1000001.MOV"]
    assert all(i.group.name == GROUP and i.nodes[0] == "01_EXPOSURE" for i in assembly.items)
    assert all("DAVIGEN_AUTO" in i.versions for i in assembly.items)
    assert proj.presets                                                    # render presets
    store = AssetStore(base)
    assert len(store) == 2 and all({"bring_in", "import_media", "colour", "basic_correction"} <= set(a.done)
                                   for a in store)
    info = filesystem.read_project_info(base)
    assert info["groups"][0]["group"] == GROUP and info["groups"][0]["clips"] == 2

    # --- later: the same card again plus a new clip and a song: only the new ones are done
    items.append(clip(card, "P1000003.MOV", "2026-09-26T10:00:00Z"))
    items.append(song(card))
    ctx2 = context(resolve, root, new=False)
    known = [ctx2.store.get(a.id) or a for a in items]
    songs = decode.available()                              # song markers listen through ffmpeg
    run2 = runner.Run(ctx2, flows.add(["basic_correction", *(["song_markers"] if songs else [])]), known,
                      values={"basic_correction": LOOK, "song_markers": {"kinds": ["bars", "phrases", "accents"]}})
    run2.run()
    assert run2.state == "done", run2.error
    steps = {s["id"]: s for s in run2.snapshot()["steps"]}
    assert steps["bring_in"]["total"] == 2 and steps["bring_in"]["already"] == 2
    assert steps["basic_correction"]["total"] == 1 and steps["basic_correction"]["already"] == 2
    assert [i.clip.GetName() for i in assembly.items][-1] == "P1000003.MOV" and len(assembly.items) == 3
    music = next(c for f, c in proj.mp._all() if c.GetName() == "song.wav")
    if songs:
        kinds = {m["customData"] for m in music.markers.values()}
        assert kinds == {"davigen-song:bars", "davigen-song:phrases", "davigen-song:accents"}
    assert (base / "04_ASSETS" / "MUSIC" / "song.wav").exists()
    bins = {f.name: [c.GetName() for c in f.clips] for f, c in proj.mp._all()}
    assert "song.wav" in bins["MUSIC"]


def test_failed_import_puts_everything_back(env):
    resolve, card, root = env
    items = [clip(card, "A.MOV", "2026-09-25T10:00:00Z"), clip(card, "B.MOV", "2026-09-25T11:00:00Z")]
    ctx = context(resolve, root)
    runner.Run(ctx, flows.new_project([]), items[:1]).run()
    proj = resolve.pm.current
    proj.mp.refuse = {"B.MOV"}
    new = clip(card, "C.MOV", "2026-09-25T12:00:00Z")
    ctx2 = context(resolve, root, new=False)
    run = runner.Run(ctx2, flows.add([]), [new, items[1]])
    run.run()
    assert run.state == "failed" and "couldn't import B.MOV" in run.error and "put back" in run.error
    assert (card / "B.MOV").exists() and (card / "C.MOV").exists()           # files back on the card
    names = [c.GetName() for _, c in proj.mp._all()]
    assert names == ["A.MOV"]                                                # C.MOV's import was undone too
    assert [a.name for a in AssetStore(ctx.base)] == ["A.MOV"]
    camera = ctx.base / "01_MEDIA" / "01_LUMIX_S1II"
    assert [f.name for f in camera.iterdir()] == ["2026-09-25_DCIM"]           # no empty folder left behind


def test_clip_removed_in_resolve_is_left_alone(env):
    resolve, card, root = env
    items = [clip(card, "A.MOV", "2026-09-25T10:00:00Z"), clip(card, "B.MOV", "2026-09-25T11:00:00Z")]
    ctx = context(resolve, root)
    runner.Run(ctx, flows.new_project([]), items).run()
    proj = resolve.pm.current
    b = next(c for _, c in proj.mp._all() if c.GetName() == "B.MOV")
    proj.mp.DeleteClips([b])
    ctx2 = context(resolve, root, new=False)
    counts = state.reconcile(ctx2)
    assert counts["removed"] == 1
    run = runner.Run(ctx2, flows.apply(["basic_correction"]), list(ctx2.store), values={"basic_correction": LOOK})
    run.run()
    assert run.state == "done", run.error
    step = next(s for s in run.snapshot()["steps"] if s["id"] == "basic_correction")
    assert step["total"] == 1 and not step["errors"]
    rows = {r["name"]: r for r in state.table(ctx2)["assets"]}
    assert rows["B.MOV"]["removed"] and rows["A.MOV"]["status"]["basic_correction"] == "done"


def test_link_mode_keeps_the_originals_on_the_card(env):
    resolve, card, root = env
    items = [clip(card, "A.MOV", "2026-09-25T10:00:00Z")]
    ctx = context(resolve, root, transfer_mode="link")
    run = runner.Run(ctx, flows.new_project([]), items)
    run.run()
    assert run.state == "done", run.error
    link = next((ctx.base / "01_MEDIA").rglob("A.MOV"))
    assert link.is_symlink() and Path(link.resolve()) == (card / "A.MOV").resolve()
    proj = resolve.pm.current
    assert next(c for _, c in proj.mp._all()).path == str(link)             # Resolve knows the project path
    # collect: the link becomes the file, at the same place
    ctx2 = context(resolve, root, new=False)
    run2 = runner.Run(ctx2, flows.apply(["collect"]), list(ctx2.store), values={"collect": {"how": "copy"}})
    run2.run()
    assert run2.state == "done", run2.error
    assert not link.is_symlink() and link.read_bytes() == (card / "A.MOV").read_bytes()


@pytest.mark.skipif(not decode.available(), reason="needs ffmpeg")
def test_audio_is_made_readable(env):
    resolve, card, root = env
    ctx = context(resolve, root)
    track = song(card, "voice_441.wav", rate=44100)
    track.kind = "voice"
    run = runner.Run(ctx, flows.new_project([]), [track])
    run.run()
    assert run.state == "done", run.error
    wav = ctx.base / "01_MEDIA" / "90_AUDIO" / "voice_441_davigen.wav"
    assert wav.exists() and (ctx.base / "01_MEDIA" / "90_AUDIO" / "voice_441.wav").exists()
    with wave.open(str(wav)) as w:
        assert w.getframerate() == 48000
    names = [c.GetName() for _, c in resolve.pm.current.mp._all()]
    assert names == ["voice_441_davigen.wav"]
