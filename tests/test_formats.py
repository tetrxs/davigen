from types import SimpleNamespace

from davigen import formats
from davigen.config import Config
from davigen.formats import Format


def test_tokens():
    assert Format(5952, 3968, 25, "3:2").aspect_token == "3X2"
    assert Format(3840, 1608, 23.976, "2.39:1").aspect_token == "239X1"
    assert Format(6000, 2000, 29.97, "custom").aspect_token == "6000X2000"
    assert Format(1, 1, 23.976, "1:1").fps_token == "2398"
    assert Format(1, 1, 25.0, "1:1").fps_token == "25"
    assert Format(1, 1, 23.976, "1:1").resolve_fps == "23.976"
    assert Format(1, 1, 50.0, "1:1").resolve_fps == "50"


def test_free_limit():
    cfg = Config()
    assert formats.fits_free(cfg, 3840, 2160)
    assert formats.fits_free(cfg, 2160, 3840)
    assert not formats.fits_free(cfg, 5952, 3968)
    assert formats.clamp_free(cfg, 5952, 3968) == (3240, 2160)
    assert formats.clamp_free(cfg, 7680, 4320) == (3840, 2160)


def test_presets_per_edition():
    cfg = Config()
    studio = formats.presets(cfg, "3:2", studio=True)
    free = formats.presets(cfg, "3:2", studio=False)
    assert (studio[0]["width"], studio[0]["height"]) == (5952, 3968)
    assert all(formats.fits_free(cfg, p["width"], p["height"]) for p in free)
    assert len({(p["width"], p["height"]) for p in free}) == len(free)      # clamped duplicates removed


def test_aspect_detection():
    cfg = Config()
    assert formats.aspect_of(cfg, 5952, 3968) == "3:2"
    assert formats.aspect_of(cfg, 3840, 2160) == "16:9"
    assert formats.aspect_of(cfg, 1080, 1920) == "9:16"
    assert formats.aspect_of(cfg, 1234, 777) == "custom"


def test_timelines_and_deliveries():
    cfg = Config()
    fmt = Format(5952, 3968, 25, "3:2", ["master", "16x9_uhd", "9x16_1080"])
    names = [t["name"] for t in formats.timelines(cfg, fmt)]
    assert names[:3] == ["TL_01_ASSEMBLY_3X2_25_v001", "TL_02_EDIT_3X2_25_v001", "TL_03_MASTER_3X2_25_v001"]
    assert "TL_04_DELIVERY_16X9_25_v001" in names and "TL_05_DELIVERY_9X16_25_v001" in names
    jobs = formats.deliveries(cfg, fmt, "ITALY_2026")
    assert [j["preset"] for j in jobs] == ["DAVIGEN_MASTER", "DAVIGEN_16X9_UHD", "DAVIGEN_9X16_1080"]
    assert jobs[0]["filename"] == "ITALY_2026_MASTER_3X2_v001"
    assert formats.timeline_for(cfg, fmt, jobs[1]) == "TL_04_DELIVERY_16X9_25_v001"


def test_suggest_from_footage():
    cfg = Config()
    clip = lambda w, h, fps, dur: SimpleNamespace(error="", width=w, height=h, fps=fps, duration=dur)  # noqa: E731
    clips = [clip(5952, 3968, 25.0, 600), clip(3840, 2160, 59.94, 100)]
    s = formats.suggest(cfg, clips, studio=True)
    assert (s["width"], s["height"], s["fps"], s["aspect"]) == (5952, 3968, 25, "3:2")
    s = formats.suggest(cfg, clips, studio=False)
    assert (s["width"], s["height"], s["clamped"]) == (3240, 2160, True)
    assert formats.suggest(cfg, [], studio=True) is None
