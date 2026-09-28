import pytest

np = pytest.importorskip("numpy")

import fake_resolve as fr  # noqa: E402 - tests/ is on sys.path under pytest
from davigen import colormath  # noqa: E402
from davigen.basic import run, write  # noqa: E402
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
    assert all(s["state"] == "done" for s in rep.steps.values()), rep.steps
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
