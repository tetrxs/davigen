import pytest

np = pytest.importorskip("numpy")

from davigen import colormath  # noqa: E402
from davigen.basic import evaluate, run, write  # noqa: E402
from davigen.config import Config  # noqa: E402
from davigen.creator import Reporter  # noqa: E402
from test_basic_run import setup  # noqa: E402

pytestmark = pytest.mark.skipif(not colormath.available(), reason="colour-science not installed")


def display(lin):
    return np.clip(lin, 0, 1) ** (1 / 2.4)


def test_score_identical_and_known_differences():
    rng = np.random.default_rng(0)
    lin = rng.uniform(0.02, 0.4, (32, 48, 3))
    same = evaluate.score(display(lin), display(lin))
    assert abs(same.exposure) < 1e-9 and same.wb_angle < 1e-6 and same.delta_e < 1e-6
    brighter = evaluate.score(display(lin), display(lin * 2))
    assert brighter.exposure == pytest.approx(1.0, abs=1e-6) and brighter.wb_angle < 1e-6
    warmer = evaluate.score(display(lin), display(lin * [1.1, 1.0, 0.9]))
    assert warmer.wb_angle > 3 and warmer.delta_e > 1


def test_summary_and_markdown():
    results = [{"clip": f"c{i}", "category": "daylight" if i < 8 else "night", "flagged": i >= 8,
                "score": {"exposure": 0.1 * (-1) ** i, "wb_angle": 1.0, "delta_e": 1.0 if i < 8 else 7.0}}
               for i in range(10)]
    s = evaluate.summarise(results)
    assert s["clips"] == 10 and s["small_or_none"] == pytest.approx(0.8)
    assert s["big_misses"] == 2 and s["big_misses_flagged"] == 1.0 and s["flags_on_big_misses"] == 1.0
    assert s["worst"][0]["delta_e"] == 7.0 and set(s["by_category"]) == {"daylight", "night"}
    md = evaluate.report_markdown(s, "Test")
    assert "| Exposure difference, median | 0.10 stops" in md and "night" in md
    assert evaluate.summarise([]) == {"clips": 0}


def test_evaluate_timeline_renders_both_versions_and_restores(tmp_path):
    luts_dir = tmp_path / "luts"
    luts_dir.mkdir()
    luts = (str(colormath.input_lut("V-Log", "V-Gamut", luts_dir / "in.cube", size=17)),
            str(colormath.output_lut(luts_dir / "out.cube", size=33)))
    resolve, proj, tl, items = setup(luts)
    rep = Reporter(run.STEPS)
    run.basic_correction(resolve, Config(), rep, base=tmp_path)
    items[0].LoadVersionByName("Version 1", 0)                       # the user looks at their own grade
    tl = proj.GetCurrentTimeline()                                   # the analysis render invalidated the old one
    record = write.load_record(tmp_path, tl.GetName())
    from davigen.basic import sampling
    results = evaluate.evaluate_timeline(resolve, proj, tl, record,
                                         cache=sampling.Cache(tmp_path / "03_WORK" / "ANALYSIS"))
    assert {r["clip"] for r in results} == {"A.MOV", "B.MOV"}
    a = next(r for r in results if r["clip"] == "A.MOV")
    assert a["score"]["exposure"] > 0.3                              # A was brightened in DAVIGEN_AUTO
    assert items[0].current == "Version 1" and items[1].current == write.AUTO     # active versions restored
    assert items[0].versions["Version 1"]["cdl"] == {}               # nothing written into the user's grade
    assert proj.jobs == []                                           # render jobs removed
    # the fake renders exactly what the simulator computes, so only resampling differences remain
    assert all(r["simulator_error"] is not None and r["simulator_error"] < 0.02 for r in results)
