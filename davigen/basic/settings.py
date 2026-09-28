"""The [basic_correction] section of config/workflow.toml, with defaults for keys it doesn't have.

The numbers belong in the TOML. DEFAULTS only keeps an older or edited config working and must have the same keys
(tests/test_basic_settings.py checks that).
"""

from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path

from ..config import DATA_DIR

LEARNED = DATA_DIR / "basic_learned.json"       # per user, kept by updates (data/ is)

DEFAULTS: dict = {
    "wizard_default": True,
    "samples": {"min": 5, "per_seconds": 4, "max": 12},
    "analysis_width": 480,
    "confidence_flag_below": 0.6,
    "measure": {
        "centre_sigma": 0.35,
        "black_stops": 5.0,
        "clip_tolerance": 0.002,
        "clip_min_fraction": 0.003,
        "midtone_stops": 2.5,
        "skin": {"hue": [110, 136], "chroma": [0.03, 0.22], "luma": [0.15, 0.90], "min_fraction": 0.02,
                 "flag_fraction": 0.05, "line": 123, "max_off": 8},
        "high_key": {"min_stops": 0.7, "max_shadow_fraction": 0.05, "max_chroma": 15},
        "low_key": {"min_black_fraction": 0.35, "min_bright_fraction": 0.08},
        "white_balance": {"minkowski_p": 6, "edge_p": 6, "achromatic_top": 0.1, "achromatic_max_angle": 3.0,
                          "iterations": 3, "learned": True},
        "dominant_hue": {"bin": 30, "min_chroma": 0.04},
        "haze": {"max_range_stops": 5.0, "max_local_contrast": 0.012},
    },
    "flags": {
        "clipped": 0.03, "wb_spread": 12.0, "wb_disagree_learned": 8.0, "achromatic_min": 0.01, "dominant": 0.5, "mixed_light": 14.0,
        "exposure_spread": 3.3, "mired_spread": 150, "night_ev": 5.0,
    },
    "exposure": {
        "max_stops_down": 3.0, "max_stops_up": 1.5, "clipped_white": 0.9, "skin_ire": [60, 70],
        "skin_max_nudge": 0.5, "skin_disagree": 1.0, "ev_full": 10.0, "ev_low": 5.0,
        "ev_low_target": -1.0, "night_target": -1.5, "night_margin": 0.5, "high_key_max": 2.0, "low_key_min": -2.5,
    },
    "white_balance": {
        "cct_strength": [[7500, 0.6], [5000, 1.0], [3500, 0.6], [0, 0.35]], "max_duv": 0.02, "neutral_cct": 6504,
    },
    "contrast": {"range": [0.85, 1.35], "black": [0.02, 0.04], "white_max": 0.95, "white_ceiling": 0.98, "haze_factor": 0.5},
    "saturation": {"range": [0.85, 1.25], "chroma": [12, 26]},
    "confidence": {
        "clipped": 0.3, "changes": 0.3, "key": 0.3, "night": 0.2, "limit": 0.2, "skin_disagree": 0.2,
        "wb_spread_free": 6.0, "wb_spread_per_degree": 0.06,
        "wb_disagree_free": 2.0, "wb_disagree_per_degree": 0.08, "achromatic": 0.4, "dominant": 0.4, "mixed_light": 0.3, "haze": 0.3,
    },
    "dynamic": {
        "enabled": True, "min_change_stops": 1.0, "min_change_mired": 30, "min_wb_confidence": 0.6,
        "dead_stops": 0.5, "dead_mired": 15, "follow": 0.75, "wb_follow": 0.8,
        "refine_step_stops": 0.4, "refine_step_mired": 20, "refine_frames": 6, "refine_passes": 2,
        "tolerance_stops": 0.05, "max_keyframes": 16,
    },
    "scenes": {"gap_minutes": 10, "split_ev": 3.0, "split_cct": 1500, "pull_to_scene": 0.3, "pull_fixed_wb": 0.7},
    "learning": {"rate": 0.5, "max_exposure": 1.0, "max_kelvin": 1500, "max_black": 0.03, "chroma": [0.7, 1.4]},
}

# what davigen learned from the user's grades (concept §13): offsets to the targets, not thresholds
NEUTRAL_LEARNED = {"exposure": 0.0, "kelvin": 0.0, "black": 0.0, "chroma": 1.0, "clips": 0, "updated": ""}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        out[key] = _merge(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


def load(workflow: dict | None, learned: bool = True) -> dict:
    """Basic Correction settings from a loaded workflow.toml (Config.workflow), defaults filled in, plus what was
    learned from the user's grades (neutral when learned=False or nothing was learned yet)."""
    out = _merge(DEFAULTS, (workflow or {}).get("basic_correction", {}))
    out["learned"] = load_learned() if learned else dict(NEUTRAL_LEARNED)
    return out


def load_learned() -> dict:
    try:
        return {**NEUTRAL_LEARNED, **json.loads(LEARNED.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        return dict(NEUTRAL_LEARNED)


def learn(differences: list[dict], settings: dict, target: Path | None = None) -> dict:
    """Move the learned offsets towards the user's grades.

    differences: per clip, DAVIGEN_AUTO minus the user's version: exposure (stops), kelvin, black (display luma of
    the 0.5th percentile), chroma (ratio). The median closes `rate` of the gap; the result is clamped.
    """
    import numpy as np  # noqa: PLC0415
    if not differences:
        return load_learned()
    lc = settings["learning"]
    old = load_learned() if target is None else {**NEUTRAL_LEARNED, **_read(target)}
    med = {k: float(np.median([d[k] for d in differences])) for k in ("exposure", "kelvin", "black")}
    ratio = float(np.median([d["chroma"] for d in differences if d["chroma"] > 0] or [1.0]))
    rate = lc["rate"]

    def clamp(v, lim):
        return max(-lim, min(lim, v))
    new = {
        "exposure": clamp(old["exposure"] - rate * med["exposure"], lc["max_exposure"]),
        "kelvin": clamp(old["kelvin"] - rate * med["kelvin"], lc["max_kelvin"]),
        "black": clamp(old["black"] - rate * med["black"], lc["max_black"]),
        "chroma": min(max(old["chroma"] / ratio ** rate, lc["chroma"][0]), lc["chroma"][1]),
        "clips": int(old["clips"]) + len(differences),
        "updated": datetime.now().isoformat(timespec="seconds"),
    }
    path = target or LEARNED
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(new, indent=2), encoding="utf-8")
    return new


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
