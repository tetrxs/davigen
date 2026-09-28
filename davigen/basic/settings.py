"""The [basic_correction] section of config/workflow.toml, with defaults for keys it doesn't have.

The numbers belong in the TOML. DEFAULTS only keeps an older or edited config working and must have the same keys
(tests/test_basic_settings.py checks that).
"""

from __future__ import annotations

import copy

DEFAULTS: dict = {
    "samples": {"min": 5, "per_seconds": 4, "max": 12},
    "analysis_width": 480,
    "confidence_flag_below": 0.6,
    "measure": {
        "centre_sigma": 0.35,
        "black_stops": 5.0,
        "clip_tolerance": 0.002,
        "clip_min_fraction": 0.003,
        "midtone_stops": 2.5,
        "skin": {"hue": [98, 148], "chroma": [0.03, 0.22], "luma": [0.15, 0.90], "min_fraction": 0.02,
                 "line": 123, "max_off": 8},
        "high_key": {"min_stops": 0.7, "max_shadow_fraction": 0.05, "max_chroma": 15},
        "low_key": {"min_black_fraction": 0.35, "min_bright_fraction": 0.08},
        "white_balance": {"minkowski_p": 6, "edge_p": 6, "achromatic_top": 0.1, "achromatic_max_angle": 3.0,
                          "iterations": 3},
        "dominant_hue": {"bin": 30, "min_chroma": 0.04},
        "haze": {"max_range_stops": 5.0, "max_local_contrast": 0.012},
    },
    "flags": {
        "clipped": 0.03, "wb_spread": 5.0, "achromatic_min": 0.01, "dominant": 0.5, "mixed_light": 4.0,
        "exposure_spread": 0.5, "cct_spread": 800, "night_ev": 5.0,
    },
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        out[key] = _merge(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


def load(workflow: dict | None) -> dict:
    """Basic Correction settings from a loaded workflow.toml (Config.workflow), defaults filled in."""
    return _merge(DEFAULTS, (workflow or {}).get("basic_correction", {}))
