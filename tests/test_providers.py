from pathlib import Path

from davigen.providers import dji, model_tokens, tokens

PAGE = (Path(__file__).parent / "fixtures" / "dji_catalog_links.html").read_text()
DLOGM = r"D-Log ?M to Rec\.?709"


def test_tokens_split_letters_and_digits():
    assert tokens("DJI Air 3S") == ["dji", "air", "3", "s"]
    assert model_tokens("DJI Mini 4 Pro", ["dji"]) == ["mini", "4", "pro"]


def test_exact_model_match():
    files = dji.catalog(PAGE)
    assert dji.find("DJI Air 3", DLOGM, files)[0].filename.startswith("DJI Air 3 D-Log M")
    assert dji.find("DJI Air 3S", DLOGM, files)[0].filename.startswith("DJI Air 3S")
    assert dji.find("DJI Osmo Pocket 3", DLOGM, files)[0].filename.startswith("DJI OSMO Pocket 3")


def test_unknown_model_gives_nothing():
    assert dji.find("DJI Air 9", DLOGM, dji.catalog(PAGE)) == []


def test_prefers_technical_newest_lut():
    best = dji.find("DJI OSMO Pocket 4", r"D-Log to Rec\.?709", dji.catalog(PAGE))[0]
    assert "vivid" not in best.filename.lower() and "V2.0" in best.filename
