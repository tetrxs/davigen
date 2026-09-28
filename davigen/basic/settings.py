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
    "exposure": {
        "max_stops": 1.5, "skin_ire": [60, 70], "skin_disagree": 1.0, "ev_full": 10.0, "ev_low": 5.0,
        "ev_low_target": -1.0, "night_target": -1.5, "night_margin": 0.5, "high_key_max": 2.0, "low_key_min": -2.5,
    },
    "white_balance": {
        "cct_strength": [[7500, 0.6], [5000, 1.0], [3500, 0.6], [0, 0.35]], "max_duv": 0.02, "neutral_cct": 6504,
    },
    "contrast": {"range": [0.85, 1.35], "black": [0.02, 0.04], "white_max": 0.95, "white_ceiling": 0.98, "haze_factor": 0.5},
    "saturation": {"range": [0.85, 1.25], "chroma": [16, 28]},
    "confidence": {
        "clipped": 0.3, "changes": 0.3, "key": 0.3, "night": 0.2, "limit": 0.2, "skin_disagree": 0.2,
        "wb_spread_per_degree": 0.08, "achromatic": 0.4, "dominant": 0.4, "mixed_light": 0.3, "haze": 0.3,
    },
    "scenes": {"gap_minutes": 10, "split_ev": 3.0, "split_cct": 1500, "pull_to_scene": 0.3, "pull_fixed_wb": 0.7},
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        out[key] = _merge(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


def load(workflow: dict | None) -> dict:
    """Basic Correction settings from a loaded workflow.toml (Config.workflow), defaults filled in."""
    return _merge(DEFAULTS, (workflow or {}).get("basic_correction", {}))
