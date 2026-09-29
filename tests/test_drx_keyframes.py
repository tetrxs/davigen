"""Keyframed primaries in a .drx (concept BASIC_CORRECTION §12)."""

import pytest

from davigen import drx

LABELS = ["01_EXPOSURE", "02_WHITE_BALANCE", "03_CONTRAST", "04_SATURATION", "05_SECONDARIES", "06_FINISH"]


@pytest.fixture(scope="module")
def template():
    return drx.KEYFRAME_TEMPLATE.read_text(encoding="utf-8")


def test_template_is_clean(template):
    kf = drx.read_keyframes(template)
    assert list(kf) == LABELS
    for rows in kf.values():
        # only the two parameters Resolve always writes; no stray lift/gain from SetCDL
        assert all(set(values) <= {drx.P_LUM_MIX} for _, values in rows)
    assert "/Users/" not in template


def test_writes_keyframes_at_source_frames(template):
    off = {pid: 0.1 for pid in drx.P_OFFSET}
    text = drx.make_keyframe_drx(template, [600, 640, 700], {
        "01_EXPOSURE": [{pid: 0.0 for pid in drx.P_OFFSET}, off, off],
        "03_CONTRAST": [{drx.P_CONTRAST: 1.2, drx.P_PIVOT: 0.336}] * 3,
    })
    kf = drx.read_keyframes(text)
    assert [t for t, _ in kf["01_EXPOSURE"]] == [None, 600, 640, 700]
    assert kf["01_EXPOSURE"][0][1][drx.P_OFFSET[0]] == 0.0              # base = first keyframe
    assert kf["01_EXPOSURE"][2][1][drx.P_OFFSET[2]] == pytest.approx(0.1)
    assert kf["03_CONTRAST"][3][1][drx.P_CONTRAST] == pytest.approx(1.2)
    # every node carries the same times (Resolve keyframes a grade as a whole)
    assert all([t for t, _ in rows] == [None, 600, 640, 700] for rows in kf.values())
    assert set(kf["05_SECONDARIES"][1][1]) <= {drx.P_LUM_MIX}


def test_parameters_stay_sorted_by_id(template):
    text = drx.make_keyframe_drx(template, [10, 20], {
        "02_WHITE_BALANCE": [{drx.P_CONTRAST: 1.0, drx.P_OFFSET[1]: 0.2, drx.P_SATURATION: 1.1}] * 2})
    for _, values in drx.read_keyframes(text)["02_WHITE_BALANCE"]:
        assert list(values) == sorted(values)


def test_rejects_bad_input(template):
    with pytest.raises(ValueError):
        drx.make_keyframe_drx(template, [20, 10], {})
    with pytest.raises(ValueError):
        drx.make_keyframe_drx(template, [10, 20], {"01_EXPOSURE": [{}]})
    with pytest.raises(ValueError):
        drx.make_keyframe_drx(template, [10], {"99_NOPE": [{}]})


def test_static_nodes_have_no_keyframes(template):
    text = drx.make_keyframe_drx(template, [10, 20], {"01_EXPOSURE": [{drx.P_OFFSET[0]: 0.0}, {drx.P_OFFSET[0]: 0.1}]},
                                 static=("03_CONTRAST",))
    kf = drx.read_keyframes(text)
    assert [t for t, _ in kf["03_CONTRAST"]] == [None] and [t for t, _ in kf["01_EXPOSURE"]] == [None, 10, 20]
