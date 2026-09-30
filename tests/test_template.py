"""The project template: playback frame rate and proxy folder written into an exported empty project (Resolve 21)."""

from pathlib import Path

import pytest

from davigen import template

EMPTY = Path(__file__).parent / "fixtures" / "empty_project_r21.drp"
pytestmark = pytest.mark.skipif(template.zstd is None, reason="needs Python 3.14 (zstd)")


def test_reads_the_exported_project():
    setup = template.read_settings(EMPTY)
    assert template.playback_fps(setup) is None                    # never set: Resolve's default
    assert template.working_folders(setup)["proxy"].endswith("/ProxyMedia")


@pytest.mark.parametrize("fps", [23.976, 25.0, 29.97, 50.0, 59.94])
def test_writes_playback_fps_and_proxy_folder(tmp_path, fps):
    out = template.write(EMPTY, tmp_path / "t.drp", fps=fps, proxy="/Volumes/X/PROJ/03_WORK/PROXIES")
    setup = template.read_settings(out)
    assert template.playback_fps(setup) == pytest.approx(fps, abs=1e-3)
    folders = template.working_folders(setup)
    assert folders["proxy"] == "/Volumes/X/PROJ/03_WORK/PROXIES"
    assert folders["cache"] == "/Users/example/Movies/Projects/EMPTY/CACHE"      # the rest stays as it was
    again = template.read_settings(template.write(out, tmp_path / "t2.drp", fps=25.0))
    assert template.playback_fps(again) == 25.0 and template.working_folders(again)["proxy"].startswith("/Volumes")


def test_other_files_of_the_project_are_kept(tmp_path):
    import zipfile
    out = template.write(EMPTY, tmp_path / "t.drp", fps=25.0)
    assert sorted(zipfile.ZipFile(out).namelist()) == sorted(zipfile.ZipFile(EMPTY).namelist())


def test_unknown_layout_is_reported(tmp_path):
    import zipfile
    bad = tmp_path / "bad.drp"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("project.xml", "<SM_Project><FieldsBlob>00</FieldsBlob></SM_Project>")
    with pytest.raises(template.TemplateError):
        template.read_settings(bad)


def test_protobuf_put_replaces_or_appends():
    b = template.put(b"", 5, 0, template._enc(7))
    b = template.put(b, 5, 0, template._enc(9))
    assert [f[0] for f in template.fields(b)] == [5]
    b = template.put(b, 40, 2, b"abc")
    assert template.get(b, 40) == (2, b"abc")
