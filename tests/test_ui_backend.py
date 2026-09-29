"""What the web UI gets beyond the flows: project pictures and the update check."""

import json

import pytest

from davigen import posters, update


def test_pick_takes_one_clip_per_group_first():
    items = [{"id": str(i), "name": f"c{i}", "group": g, "scene": s, "correction": {"values": {}}, "frames": [0],
              "source_frames": n, "clip_fps": 25.0}
             for i, (g, s, n) in enumerate([("A", 0, 900), ("A", 1, 800), ("B", 2, 100), ("A", 0, 700)])]
    picked = [e["id"] for e in posters.pick({"items": items}, 3)]
    assert picked[:2] == ["0", "2"]            # the longest of A, then B before A's second scene
    assert picked[2] == "1"


def test_sources_fall_back_to_camera_files(tmp_path, monkeypatch):
    monkeypatch.setattr(posters, "LUT_DIR", tmp_path / "luts")
    info = tmp_path / "00_ADMIN" / "PROJECT_INFO"
    info.mkdir(parents=True)
    (info / "davigen.json").write_text(json.dumps({"groups": [{"group": "G_AIR3_DLOGM", "profile": "DLOGM"}]}))
    cam = tmp_path / "01_MEDIA" / "01_AIR3" / "day1"
    cam.mkdir(parents=True)
    for n in range(3):
        (cam / f"DJI_{n}.MP4").write_bytes(b"x")
    (cam / "._DJI_0.MP4").write_bytes(b"x")                 # macOS resource forks are not clips
    src = posters.sources(tmp_path)
    assert src and all(s["group"] == "G_AIR3_DLOGM" for s in src)
    assert all(not s["name"].startswith(".") for s in src)
    assert src[0]["entry"]["luts"] == ["", ""]              # no baked LUTs here: the log picture as it is


def test_poster_of_an_empty_project(tmp_path):
    with pytest.raises(FileNotFoundError):
        posters.poster(tmp_path, 0)


def test_update_never_offered_to_a_checkout():
    u = update.Updater()
    u.state["latest"] = {"commit": "0000000", "full_commit": "0" * 40, "date": "", "message": "", "version": "9.9"}
    snap = u.snapshot()
    if snap["installed"]["dev"]:
        assert snap["available"] is None
        assert u.run()["ok"] is False


def test_preview_cube_without_luts_is_the_identity(tmp_path):
    np = pytest.importorskip("numpy")
    from davigen.edit import preview
    from davigen.lut import Lut3D
    cube = Lut3D.read(preview.bake(None, [], None, tmp_path / "id.cube"))
    for rgb in ([0.1, 0.5, 0.9], [1.0, 0.0, 0.25]):
        assert np.allclose(cube.sample(*rgb), rgb, atol=1e-6)


def test_preview_fills_the_project_aspect(tmp_path):
    from davigen.edit import preview
    info = tmp_path / "00_ADMIN" / "PROJECT_INFO"
    info.mkdir(parents=True)
    (info / "davigen.json").write_text(json.dumps({"format": {"width": 3240, "height": 2160}}))
    assert preview._size(tmp_path, 960) == (960, 640)
    assert preview._size(tmp_path / "nothing", 960) == (960, 540)
