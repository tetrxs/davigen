from davigen import catalog
from davigen.config import Config

CAT = {"updated": "2026-09-26", "cameras": [
    {"qid": "Q1", "name": "DJI Air 3", "maker": "DJI", "kind": "drone", "aliases": [], "thumb": "Q1.jpg", "year": "2023"},
    {"qid": "Q2", "name": "DJI Air 3S", "maker": "DJI", "kind": "drone", "aliases": [], "thumb": "Q2.jpg", "year": "2024"},
    {"qid": "Q3", "name": "Sony FX3", "maker": "Sony Group", "kind": "camera", "aliases": ["ILME-FX3"], "thumb": "", "year": "2021"},
    {"qid": "Q4", "name": "Canon PowerShot SX400 IS", "maker": "Canon Inc.", "kind": "camera", "aliases": [], "thumb": "", "year": "2014"},
    {"qid": "Q5", "name": "Fujifilm X-T5", "maker": "Fujifilm Corporation", "kind": "camera", "aliases": [], "thumb": "Q5.jpg", "year": "2022"},
]}


def test_match_detected_camera_to_photo():
    cfg = Config()
    assert catalog.match(cfg, "DJI Air 3", "Air3", catalog=CAT)["qid"] == "Q1"
    assert catalog.match(cfg, "DJI Air 3S", catalog=CAT)["qid"] == "Q2"
    assert catalog.match(cfg, "Sony FX3", "ILME-FX3", catalog=CAT)["qid"] == "Q3"


def test_search_word_starts_and_known_first():
    cfg = Config()
    names = [r["name"] for r in catalog.search(cfg, "x4", catalog=CAT)]
    assert "Canon PowerShot SX400 IS" not in names
    hits = catalog.search(cfg, "air 3", catalog=CAT)
    assert hits[0]["known"] and hits[0]["key"] == "DJI_AIR3" and hits[0]["thumb"] == "Q1.jpg"


def test_unknown_catalog_camera_gets_brand_profiles():
    cfg = Config()
    xt5 = next(r for r in catalog.search(cfg, "x-t5", catalog=CAT) if r["name"] == "Fujifilm X-T5")
    assert xt5["brand"] == "fujifilm" and xt5["profiles"][0].startswith("FLOG")
    assert xt5["profiles"][-1] in ("HLG", "REC709")
