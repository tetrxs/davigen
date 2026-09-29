import pytest

pytest.importorskip("numpy")

import fake_resolve as fr  # noqa: E402 - tests/ is on sys.path under pytest
from davigen.basic import correct as c  # noqa: E402
from davigen.basic import pipeline as p  # noqa: E402
from davigen.basic import write  # noqa: E402

GROUP = fr.Group("S1II_VLOG", "", "")


def correction():
    nodes = {c.EXPOSURE: p.Cdl.exposure(0.5), c.WHITE_BALANCE: p.Cdl(offset=(0.01, 0.0, -0.01)),
             c.CONTRAST: p.Cdl.contrast(1.1), c.SATURATION: p.Cdl.saturation(1.1)}
    return c.Correction(nodes=nodes, confidence={n: 0.9 for n in c.NODES}, flags=[])


def item(labels=fr.LABELS, group=GROUP):
    mpi = fr.MediaPoolItem("/x/P1.MOV", "S1II_VLOG", 100, None)
    return fr.TimelineItem(mpi, 90000, 50, 0, labels=labels, group=group)


def project():
    return fr.Project("P", [fr.Timeline("TL")], [GROUP])


def test_new_version_is_added_and_written():
    it = item()
    out = write.write_item(project(), it, correction())
    assert out.written == {n: True for n in c.NODES} and not out.skipped
    assert out.user_version == "Version 1" and it.current == write.AUTO
    assert it.versions["Version 1"]["cdl"] == {}                       # the user's grade is untouched
    assert it.versions[write.AUTO]["cdl"][1]["Offset"] == p.Cdl.exposure(0.5).to_resolve(1)["Offset"]
    assert set(it.versions[write.AUTO]["cdl"]) == {1, 2, 3, 4}


def test_existing_version_is_kept_unless_recompute():
    it = item()
    write.write_item(project(), it, correction())
    it.calls.clear()
    out = write.write_item(project(), it, correction())
    assert "exists" in out.skipped and "SetCDL" not in it.calls
    out = write.write_item(project(), it, correction(), recompute=True, previous_user_version="Version 1")
    assert out.written and "AddVersion" not in it.calls
    assert out.user_version == "Version 1"                             # remembered, though AUTO was active


def test_nodes_found_by_label_and_missing_reported():
    shuffled = ["03_CONTRAST", "01_EXPOSURE", "05_SECONDARIES", "02_WHITE_BALANCE", "06_FINISH"]
    it = item(labels=shuffled)
    out = write.write_item(project(), it, correction())
    cdl = it.versions[write.AUTO]["cdl"]
    assert cdl[2]["Offset"] == p.Cdl.exposure(0.5).to_resolve(2)["Offset"]      # 01 sits at index 2
    assert cdl[1]["Slope"] == "1.1 1.1 1.1"                                        # 03 at index 1
    assert 3 not in cdl and 5 not in cdl                                           # never written elsewhere
    assert any("04_SATURATION" in w for w in out.warnings)


def test_refused_setcdl_is_a_warning():
    it = item()
    it.refuse_cdl = True
    out = write.write_item(project(), it, correction())
    assert out.written == {n: False for n in c.NODES} and len(out.warnings) == 4


def test_dry_run_writes_nothing():
    it = item()
    out = write.write_item(project(), it, correction(), dry_run=True)
    assert out.skipped == "dry run"
    assert not {"AddVersion", "LoadVersionByName", "SetCDL"} & set(it.calls)


def test_never_writes_into_user_version():
    it = item()
    it.add_version_activates = False
    it.refuse_load = True                       # a Resolve that behaves differently than 21.0
    out = write.write_item(project(), it, correction())
    assert "isn't the active version" in out.skipped
    assert "SetCDL" not in it.calls and it.versions["Version 1"]["cdl"] == {}


def test_group_is_assigned_or_flagged():
    it = item(group=None)
    assert write.write_item(project(), it, correction()).written
    assert it.GetColorGroup() is GROUP
    orphan = fr.TimelineItem(fr.MediaPoolItem("/x/a.mov", "", 10, None), 0, 10, 0, labels=fr.LABELS)
    assert write.write_item(project(), orphan, correction()).skipped == write.NOT_IN_GROUP


def test_blank_clip_gets_the_structure():
    it = item(labels=[""])
    out = write.write_item(project(), it, correction())
    if write.color.CLIP_TEMPLATE.exists():
        assert out.written and it.versions["Version 1"]["labels"] == fr.LABELS
    else:
        assert out.skipped == write.NO_NODES


def test_markers():
    it = item()
    it.AddMarker(5, "Blue", "mine", "user note", 1, "")
    it.AddMarker(0, "Yellow", "davigen: old", "", 1, write.MARKER_DATA)
    write.clear_markers([it])
    assert list(it.markers) == [5]                                     # user marker survives
    assert write.mark(it, ["dominant colour"], 0.9, 0.6) == ""         # confident: no marker
    assert write.mark(it, ["dominant colour", "haze"], 0.3, 0.6) == "davigen: dominant colour"
    assert it.markers[0]["color"] == "Yellow" and "haze" in it.markers[0]["note"]
    assert write.mark(it, [], None, 0.6, write.NOT_IN_GROUP) == f"davigen: {write.NOT_IN_GROUP}"
    assert it.markers[0]["color"] == "Red"


def test_record_roundtrip(tmp_path):
    write.save_record(tmp_path, "TL_01", {"items": [{"id": "a", "cdl": p.Cdl.exposure(1.0)}]})
    rec = write.load_record(tmp_path, "TL_01")
    assert rec["items"][0]["cdl"]["offset"][0] == pytest.approx(p.STOP)
    assert write.load_record(tmp_path, "missing") == {}
