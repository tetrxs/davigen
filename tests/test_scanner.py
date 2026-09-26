import os
from pathlib import Path

import pytest

from davigen import naming, scanner
from davigen.config import Config

# A folder with Panasonic S1II V-Log and DJI Air 3 D-Log M clips, e.g. DAVIGEN_SAMPLE_FOOTAGE=~/footage/test
SAMPLES = Path(os.path.expanduser(os.environ.get("DAVIGEN_SAMPLE_FOOTAGE", "/nonexistent")))


def test_normalize_and_validate():
    assert naming.normalize("Italien Sommer 2026") == "ITALIEN_SOMMER_2026"
    assert naming.normalize("Côte d'Azur – Süd") == "COTE_D_AZUR_SUED"
    assert naming.validate_project_name("ITALY_2026") == []
    assert any("FINAL" in p for p in naming.validate_project_name("ITALY_FINAL"))


def test_camera_key_fallback():
    assert naming.camera_key("Panasonic", "DC-S9") == "PANASONIC_DC_S9"
    assert naming.camera_key("", "") == "UNKNOWN_CAMERA"
    assert naming.group_name("LUMIX_S1II", "VLOG") == "G_LUMIX_S1II_VLOG"


def test_known_cameras_match():
    cfg = Config()
    assert cfg.find_camera("Panasonic", "DC-S1M2").key == "LUMIX_S1II"
    assert cfg.find_camera("DJI", "Air3").key == "DJI_AIR3"
    assert cfg.find_camera("SONY", "ILCE-7M4").key == "SONY_A7IV"


def test_every_camera_profile_exists():
    cfg = Config()
    for cam in cfg.cameras:
        for pid in cam.profiles:
            assert pid in cfg.profiles, f"{cam.key}: unknown profile {pid}"


@pytest.mark.skipif(not SAMPLES.exists(), reason="set DAVIGEN_SAMPLE_FOOTAGE to run")
def test_real_footage_detection():
    cfg = Config()
    groups = scanner.group_clips(scanner.scan([str(SAMPLES)], cfg))
    by_name = {g.summary(cfg)["group_name"]: g for g in groups}
    lumix = by_name["G_LUMIX_S1II_VLOG"]
    assert lumix.confidence == scanner.METADATA
    assert {c.width for c in lumix.clips} == {5952}
    dji = by_name["G_DJI_AIR3_DLOGM"]
    assert dji.confidence == scanner.INFERRED
    assert dji.summary(cfg)["fps"] == ["59.94"]
