import re
from pathlib import Path

import pytest

from davigen import drx

TEMPLATE = Path(__file__).parent.parent / "templates" / "drx" / "CST_BASE.drx"


@pytest.fixture(scope="module")
def template_text():
    return TEMPLATE.read_text()


def test_protobuf_roundtrip_is_byte_identical(template_text):
    for hex_body in re.findall(r"<Body>([0-9a-f]+)</Body>", template_text):
        payload = drx.decode_body(hex_body)
        assert drx.parse(payload).encode() == payload


def test_reads_template_cst(template_text):
    assert drx.read_cst(template_text) == {
        "inputColorSpace": "VLOG_COLORSPACE",
        "inputGamma": "PANASONIC_VLOG_GAMMA",
        "outputColorSpace": "DWG_COLORSPACE",
        "outputGamma": "DAV_INTER_OETF_GAMMA",
        "resolvefxVersion": "1.4",
        "tmType": "TM_NONE",
    }


def test_rewrites_cst_and_keeps_rest_of_file(template_text):
    out = drx.make_cst_drx(template_text, {
        "inputColorSpace": "SGAMUT3CINE_COLORSPACE",
        "inputGamma": "SONY_SLOG3_GAMMA",
    })
    params = drx.read_cst(out)
    assert params["inputColorSpace"] == "SGAMUT3CINE_COLORSPACE"
    assert params["inputGamma"] == "SONY_SLOG3_GAMMA"
    assert params["outputGamma"] == "DAV_INTER_OETF_GAMMA"
    strip = lambda t: re.sub(r"<Body>[0-9a-f]+</Body>", "", t)
    assert strip(out) == strip(template_text)


def test_none_removes_param_and_new_params_are_added(template_text):
    out = drx.make_cst_drx(template_text, {"tmType": None, "gamutMappingType": "GM_NONE"})
    params = drx.read_cst(out)
    assert "tmType" not in params
    assert params["gamutMappingType"] == "GM_NONE"
