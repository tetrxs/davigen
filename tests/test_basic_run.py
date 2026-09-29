import pytest

np = pytest.importorskip("numpy")

import fake_resolve as fr  # noqa: E402 - tests/ is on sys.path under pytest
from davigen import colormath  # noqa: E402
from davigen.basic import pipeline as p, run, write  # noqa: E402
from davigen.config import Config  # noqa: E402
from davigen.creator import Reporter  # noqa: E402

pytestmark = pytest.mark.skipif(not colormath.available(), reason="colour-science not installed")


@pytest.fixture(scope="module")
def luts(tmp_path_factory):
    d = tmp_path_factory.mktemp("luts")
    return (str(colormath.input_lut("V-Log", "V-Gamut", d / "in.cube", size=17)),
            str(colormath.output_lut(d / "out.cube", size=33)))


def vlog_scene(stops: float, tint=(1.0, 1.0, 1.0), seed=0):
    """A camera frame in V-Log: grey patches around 18 % × 2^stops under a coloured light."""
    colour = colormath._colour()
    rng = np.random.default_rng(seed)
    level = 0.18 * 2 ** stops * np.exp(rng.normal(0, 0.7, (8, 12, 1)))
    lin = np.kron(level * np.asarray(tint), np.ones((8, 8, 1)))
    return lambda frame: colour.models.log_encoding_VLog(lin)


def setup(luts):
    group = fr.Group("S1II_VLOG", *luts)
    a = fr.MediaPoolItem("/footage/A.MOV", "S1II_VLOG", 500, vlog_scene(-1.0))
    b = fr.MediaPoolItem("/footage/B.MOV", "S1II_VLOG", 500, vlog_scene(0.5, (1.2, 1.0, 0.8), seed=1))
    orphan = fr.MediaPoolItem("/footage/C.MOV", "", 500, vlog_scene(0.0))
    items = [fr.TimelineItem(a, 90000, 250, 100, labels=fr.LABELS, group=group),
             fr.TimelineItem(b, 90250, 100, 0, labels=fr.LABELS),
             fr.TimelineItem(orphan, 90350, 50, 0, labels=fr.LABELS)]
    tl = fr.Timeline("TL_01_ASSEMBLY", items)
    proj = fr.Project("TEST", [tl], [group])
    return fr.Resolve(proj), proj, tl, items


def test_full_flow(luts, tmp_path):
    resolve, proj, tl, items = setup(luts)
    rep = Reporter(run.STEPS)
    record = run.basic_correction(resolve, Config(), rep, base=tmp_path)
    assert all(s["state"] == "done" for k, s in rep.steps.items() if k != "spread"), rep.steps   # flow() spreads
    a, b, orphan = items
    assert a.current == write.AUTO and b.current == write.AUTO            # written, and left active
    assert a.versions["Version 1"]["cdl"] == {}                             # user grade untouched
    off = float(a.versions[write.AUTO]["cdl"][1]["Offset"].split()[0])
    assert off > 0.03                                                       # the dark clip is brightened
    assert b.GetColorGroup() is not None                                    # assigned on the way
    assert orphan.markers[0]["color"] == "Red" and "Version 1" == orphan.current
    assert [t.GetName() for t in proj.timelines] == ["TL_01_ASSEMBLY"]     # scratch timeline gone
    assert proj.preset == "DAVIGEN_MASTER_3X2" and proj.jobs == []
    assert (tmp_path / "00_ADMIN/PROJECT_INFO/basic_correction/TL_01_ASSEMBLY.json").exists()
    assert len(record["items"]) == 3 and rep.result["rows"][0]["name"] == "C.MOV"     # problems first
    assert resolve.pm.saved == 1
    # frames are sampled inside the used range
    assert all(100 + 5 <= f <= 100 + 250 - 6 for f in record["items"][0]["frames"])


def test_second_run_uses_cache_and_keeps_versions(luts, tmp_path):
    resolve, proj, tl, items = setup(luts)
    run.basic_correction(resolve, Config(), Reporter(run.STEPS), base=tmp_path)
    renders = proj.renders
    items[0].versions[write.AUTO]["cdl"][1]["Offset"] = "9 9 9"             # a tweak made inside DAVIGEN_AUTO
    rep = Reporter(run.STEPS)
    run.basic_correction(resolve, Config(), rep, base=tmp_path)
    assert proj.renders == renders                                         # everything came from the cache
    assert items[0].versions[write.AUTO]["cdl"][1]["Offset"] == "9 9 9"    # not overwritten
    run.basic_correction(resolve, Config(), Reporter(run.STEPS), base=tmp_path, recompute=True)
    assert items[0].versions[write.AUTO]["cdl"][1]["Offset"] != "9 9 9"


def test_dry_run(luts, tmp_path):
    resolve, proj, tl, items = setup(luts)
    run.basic_correction(resolve, Config(), Reporter(run.STEPS), base=tmp_path, dry_run=True)
    assert all(write.AUTO not in i.versions for i in items)
    assert resolve.pm.saved == 0


def test_goto(luts):
    resolve, proj, tl, items = setup(luts)
    assert run.goto(resolve, items[1].uid)
    assert tl.timecode == "01:00:10:00" and resolve.page == "color"


def tunnel(luts):
    """One clip that leaves a tunnel at source frame 250: three stops darker before."""
    colour = colormath._colour()
    rng = np.random.default_rng(3)
    level = 0.18 * np.exp(rng.normal(0, 0.7, (8, 12, 1)))
    lin = np.kron(level * np.ones(3), np.ones((8, 8, 1)))

    def frame(n):
        return colour.models.log_encoding_VLog(lin * (2.0 ** -3.5 if n < 250 else 1.0))
    group = fr.Group("S1II_VLOG", *luts)
    mpi = fr.MediaPoolItem("/footage/T.MOV", "S1II_VLOG", 600, frame)
    item = fr.TimelineItem(mpi, 90000, 400, 100, labels=fr.LABELS, group=group)
    proj = fr.Project("TEST", [fr.Timeline("TL_01_ASSEMBLY", [item])], [group])
    return fr.Resolve(proj), proj, item


def test_tunnel_exit_is_keyframed(luts, tmp_path):
    resolve, proj, item = tunnel(luts)
    rep = Reporter(run.STEPS)
    record = run.basic_correction(resolve, Config(), rep, base=tmp_path)
    # the live view got the clip with a picture and where its keyframes sit
    assert rep.live and rep.live[-1]["keyframes"] and rep.images.get(rep.live[-1]["n"], b"").startswith(b"\x89PNG")
    e = record["items"][0]
    kf = e["keyframes"]
    assert kf and kf["frames"][0] < 250 < kf["frames"][-1]
    # the change was found to within a few frames by measuring more around it
    before = max(f for f in kf["frames"] if f < 250)
    after = min(f for f in kf["frames"] if f >= 250)
    assert after - before <= 8
    assert e["outcome"]["keyframes"] == len(kf["frames"]) and "ApplyGradeFromDRX:2" in item.calls
    assert item.versions["Version 1"].get("keyframes") is None          # the user's version is untouched
    # rendered through the fake: the tunnel is lifted, the street is not
    inside, outside = proj._graded(item, 200), proj._graded(item, 300)
    nodes = [p.Cdl(tuple(v["slope"]), tuple(v["offset"]), tuple(v["power"]), v["sat"])
             for _, v in sorted(e["correction"]["nodes"].items())]
    static_in = p.apply_lut(p.apply_nodes(p.apply_lut(item.mpi.log_frame(200), luts[0]), nodes), luts[1])
    assert p.luminance(inside).mean() > 1.5 * p.luminance(static_in).mean()         # lifted against the street's grade
    assert abs(float(p.luminance(outside).mean()) - 0.45) < 0.2
    # a second run with recompute replaces the keyframed grade again
    run.basic_correction(resolve, Config(), Reporter(run.STEPS), base=tmp_path, recompute=True)
    assert item.calls.count("ApplyGradeFromDRX:2") == 2
    # with keyframes switched off, the next recompute starts DAVIGEN_AUTO from the plain structure again
    cfg = Config()
    cfg.workflow.setdefault("basic_correction", {}).setdefault("dynamic", {})["enabled"] = False
    run.basic_correction(resolve, cfg, Reporter(run.STEPS), base=tmp_path, recompute=True)
    assert "DeleteVersionByName" in item.calls and not item.versions[write.AUTO].get("keyframes")
    assert item.versions[write.AUTO]["cdl"] and "SetCDL into keyframes" not in item.calls
    assert item.versions["Version 1"].get("keyframes") is None and item.current == write.AUTO


def test_reset_removes_davigen_auto_only(luts, tmp_path):
    resolve, proj, tl, items = setup(luts)
    run.basic_correction(resolve, Config(), Reporter(run.STEPS), base=tmp_path)
    assert any(write.AUTO in i.versions for i in items)
    user_before = {i.uid: dict(i.versions["Version 1"]) for i in items}
    rep = Reporter(run.RESET_STEPS)
    import davigen.creator as creator
    old = creator.project_base
    creator.project_base = lambda p: tmp_path
    try:
        run.reset_flow(resolve, Config(), {}, rep)
    finally:
        creator.project_base = old
    assert all(write.AUTO not in i.versions and i.current == "Version 1" for i in items)
    assert all(i.versions["Version 1"] == user_before[i.uid] for i in items)       # the user's grade untouched
    assert not any(m["customData"] == write.MARKER_DATA for i in items for m in i.markers.values())
    assert rep.result["removed"] == 2


def test_other_timelines_get_the_same_grade(luts, tmp_path):
    """A rough cut built from the footage starts ungraded; carry_over writes what the record has, keyframes too."""
    from davigen.basic import carry
    resolve, proj, item = tunnel(luts)
    run.basic_correction(resolve, Config(), Reporter(run.STEPS), base=tmp_path)
    cut_item = fr.TimelineItem(item.mpi, 90000, 150, 180)            # a trimmed piece across the tunnel exit
    cut = fr.Timeline("TL_02_EDIT_AUTO", [cut_item])
    proj.timelines.append(cut)
    counts = carry.carry_over(proj, tmp_path, proj.timelines)
    assert counts["written"] == 1 and counts["keyframed"] == 1 and counts["kept"] == 0
    assert cut_item.GetColorGroup() is not None                           # in its colour group: input LUT
    assert "ApplyGradeFromDRX:2" in cut_item.calls
    # the same picture as on the assembly at the same source frames, before and after the exit
    for f in (200, 300):
        assert np.allclose(proj._graded(cut_item, f), proj._graded(item, f), atol=1e-6)
    # the assembly itself isn't touched, and a second pass keeps what is there
    again = carry.carry_over(proj, tmp_path, proj.timelines)
    assert again["written"] == 0 and again["kept"] == 1
