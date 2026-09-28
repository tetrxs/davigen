"""The [edit_assist] section of config/workflow.toml, with defaults for keys it doesn't have (same keys; a test
checks that)."""

from __future__ import annotations

import copy

DEFAULTS: dict = {
    "selects": {
        "sharp_rel": [0.35, 0.75],      # sharpness relative to the shot around it: unusable … fully good
        "sharp_window": 5.0,            # seconds either side that "the shot around it" means
        "blur_abs": 0.01,               # below this absolute sharpness a frame is blurred whatever the clip
        "shake": [0.08, 0.35],          # jitter in frame widths per second: fine … bad
        "pan_speed": [0.6, 1.4],        # camera motion in frame widths per second: fine … whip pan
        "dark": 0.04,                   # mean luma below this: dark / covered
        "no_detail": 0.002,             # mean gradient below this: nothing in frame
        "unusable_below": 0.2,
        "unusable_min": 1.0,            # seconds
        "good_above": 0.6,
        "good_min": 2.0,
        "voice_above": 0.5,
        "speech_min": 1.5,
    },
    "selects_timeline": {"max_seconds": 6.0, "min_rating": 0.5},
    "clip_flags": {"flag_good_seconds": 4.0, "flag_bad_share": 0.5},
    "transcribe": {"enabled": True, "model": "mlx-community/whisper-large-v3-turbo", "pad": 0.3},
    "rough_cut": {
        "bars_calm": 2,                 # shot length in bars where the music is calm …
        "bars_energetic": 1,            # … and where it is energetic
        "energy_split": 0.55,           # section energy (0–1) from which a section counts as energetic
        "half_bar_above_bpm": 0,        # cut every half bar above this tempo (0 = never)
        "max_per_clip": 3,              # shots from one clip at most
        "min_gap": 4.0,                 # seconds between two shots from the same clip
        "skip_speech": True,
    },
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        out[key] = _merge(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


def load(workflow: dict | None) -> dict:
    return _merge(DEFAULTS, (workflow or {}).get("edit_assist", {}))
