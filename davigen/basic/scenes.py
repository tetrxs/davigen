"""Balance, then match (concept §6): group shots into scenes, pick a hero, pull the others towards it.

Pushing every clip to the same absolute target flattens a sequence. Colorists balance each shot, then match the
shots of a scene to a hero. Only nodes 01 (exposure) and 02 (white balance) are matched; contrast and saturation
stay per shot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from . import correct as c
from . import measure as ms
from . import pipeline as p

MATCHED = "matched to scene"
WB_UNSURE = "white balance left alone"


@dataclass
class Shot:
    id: str                             # timeline item id
    order: int                          # position on the timeline
    meta: ms.ClipMeta
    measurement: ms.Measurement
    correction: c.Correction
    used_seconds: float = 0.0           # how long it is on the timeline
    clip_seconds: float = 0.0           # how long the recording is (for the gap to the next one)
    scene: int = -1
    hero: bool = False
    notes: dict = field(default_factory=dict)


def match_scenes(shots: list[Shot], settings: dict) -> list[Shot]:
    """Group, choose heroes, and pull 01/02 of every shot. Changes the shots' corrections in place."""
    sc = settings["scenes"]
    threshold = settings["confidence_flag_below"]
    for n, scene in enumerate(group(shots, settings)):
        hero = max(scene, key=lambda s: (s.correction.overall * max(s.used_seconds, 0.1), -s.order))
        for shot in scene:
            shot.scene, shot.hero = n, shot is hero
            shot.notes["hero"] = hero.id
        fixed_wb = _fixed_white_balance(scene)
        for shot in scene:
            _pull(shot, hero, sc["pull_to_scene"], sc["pull_fixed_wb"] if fixed_wb else None, threshold)
    return shots


# ------------------------------------------------------------------------------------------------ grouping

def group(shots: list[Shot], settings: dict) -> list[list[Shot]]:
    """Scenes in recording order. A pause, a jump in scene brightness or a change of light starts a new one."""
    sc, threshold = settings["scenes"], settings["confidence_flag_below"]
    if not shots:
        return []
    times = [_time(s.meta.created) for s in shots]
    if all(t is not None for t in times):
        ordered = [s for _, s in sorted(zip(times, shots), key=lambda ts: (ts[0], ts[1].order))]
    else:
        ordered = sorted(shots, key=lambda s: s.order)
    scenes = [[ordered[0]]]
    for prev, shot in zip(ordered, ordered[1:]):
        if _new_scene(prev, shot, sc, threshold):
            scenes.append([])
        scenes[-1].append(shot)
    return scenes


def _new_scene(prev: Shot, shot: Shot, sc: dict, threshold: float) -> bool:
    t0, t1 = _time(prev.meta.created), _time(shot.meta.created)
    if t0 is not None and t1 is not None:
        pause = (t1 - t0).total_seconds() - prev.clip_seconds
        if pause > sc["gap_minutes"] * 60:
            return True
    e0, e1 = prev.measurement.ev100, shot.measurement.ev100
    if e0 is not None and e1 is not None and abs(e1 - e0) > sc["split_ev"]:
        return True
    wb0 = prev.correction.confidence[c.WHITE_BALANCE]
    wb1 = shot.correction.confidence[c.WHITE_BALANCE]
    if wb0 >= threshold and wb1 >= threshold and abs(shot.measurement.cct - prev.measurement.cct) > sc["split_cct"]:
        return True
    return False


def _time(created: str):
    if not created:
        return None
    try:
        return datetime.fromisoformat(created.replace("Z", "+00:00"))
    except ValueError:
        return None


def _fixed_white_balance(scene: list[Shot]) -> bool:
    """All shots with the same manual Kelvin: colour differences between them are real, not the camera's AWB."""
    kelvins = {s.meta.kelvin for s in scene}
    modes = {s.meta.white_balance.lower() for s in scene}
    return len(scene) > 1 and len(kelvins) == 1 and 0 not in kelvins and bool(modes) and "auto" not in modes \
        and "" not in modes


# ---------------------------------------------------------------------------------------------------- pull

def _pull(shot: Shot, hero: Shot, pull: float, fixed_pull: float | None, threshold: float) -> None:
    corr, m = shot.correction, shot.measurement
    hv, v = hero.correction.values, corr.values
    changed = False

    # 01: the key after correction, in stops relative to grey
    own_after = m.exposure_stops + v["exposure_stops"]
    ref_after = hero.measurement.exposure_stops + hv["exposure_stops"]
    if shot is not hero:
        if {ms.HIGH_KEY, ms.LOW_KEY, ms.NIGHT} & set(m.flags):
            weight = 0.0                    # a silhouette or a night shot is dark on purpose
        elif corr.confidence[c.EXPOSURE] < threshold <= hero.correction.confidence[c.EXPOSURE]:
            weight = 1.0                    # unsure: take the scene's level
        else:
            weight = pull
        stops = v["exposure_stops"] + weight * (ref_after - own_after)
        lo, hi = v.get("exposure_range", [-99.0, 99.0])       # the shot's own limits still hold
        stops = min(max(stops, min(lo, v["exposure_stops"])), max(hi, v["exposure_stops"]))
        if abs(stops - v["exposure_stops"]) > 1e-6:
            corr.nodes[c.EXPOSURE] = c.exposure_cdl(m, stops)
            shot.notes["exposure_pulled"] = stops - v["exposure_stops"]
            v["exposure_stops"] = stops
            changed = True

    # 02: the white it is balanced to, or with a fixed camera Kelvin the scene's correction itself
    exposure = corr.nodes[c.EXPOSURE]
    own_wb = corr.confidence[c.WHITE_BALANCE]
    hero_wb = hero.correction.confidence[c.WHITE_BALANCE]
    cct, duv = v["cct_after"], v["duv_after"]
    light = None
    if own_wb < threshold:
        if hero_wb >= threshold and shot is not hero:
            light = hero.measurement.illuminant     # a close-up of a green bush gets the wide shot's light
            cct, duv = hv["cct_after"], hv["duv_after"]
            shot.notes["white_balance"] = "from the scene"
        else:
            corr.nodes[c.WHITE_BALANCE] = p.Cdl()   # nobody in the scene knows the light: leave it
            shot.notes["white_balance"] = "left alone (unsure)"
            if WB_UNSURE not in corr.flags:
                corr.flags.append(WB_UNSURE)
            if changed and MATCHED not in corr.flags:
                corr.flags.append(MATCHED)
            return
    elif shot is not hero:
        if fixed_pull is not None:
            # same manual Kelvin: the camera didn't adapt, so lean on the scene's light, not the shot's estimate
            light = _mix(m.illuminant, hero.measurement.illuminant, fixed_pull)
            weight = fixed_pull
        else:
            weight = pull
        cct = cct + weight * (hv["cct_after"] - cct)
        duv = duv + weight * (hv["duv_after"] - duv)
    # node 02 is exact for node 01's result, so it is solved again whenever either changed
    cdl, gains = c.white_balance_cdl(m, exposure, cct, duv, light)
    if cdl != corr.nodes[c.WHITE_BALANCE]:
        corr.nodes[c.WHITE_BALANCE] = cdl
        v.update(cct_after=float(cct), duv_after=float(duv), wb_gains=[float(g) for g in gains])
        changed = changed or shot is not hero
    if changed and MATCHED not in corr.flags:
        corr.flags.append(MATCHED)


def _mix(a, b, t: float) -> list:
    """Blend two lights in log (a gain halfway is a geometric mean)."""
    a, b = np.log(np.maximum(np.asarray(a, float), 1e-9)), np.log(np.maximum(np.asarray(b, float), 1e-9))
    return list(np.exp(a + t * (b - a)))
