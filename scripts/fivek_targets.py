"""Where do experts put the key? Fit Basic Correction's exposure target on MIT-Adobe FiveK (a development tool).

    .venv/bin/pip install rawpy            # dev venv only, not davigen's runtime
    .venv/bin/python scripts/fivek_targets.py ~/Movies/davigen_dev/datasets/fivek/fivek_dataset

FiveK (Bychkovsky et al., CVPR 2011) is 5,000 raw photos, each edited by five experts in Lightroom; the catalog
(raw_photos/fivek.lrcat) holds their slider values. The images are licensed for research only, so nothing derived
from them beyond the fitted numbers in [basic_correction.exposure] leaves this machine.

Each raw is decoded as shot (camera white balance, linear, no auto brightness), converted to DWG and measured with
measure._sample exactly like a clip. The expert's final key is the raw key plus the Exposure slider, centred per
camera model (each camera has its own baseline exposure). Printed: how that final key depends on the headroom
(stops from the key to the 99.5th percentile) – the numbers behind headroom_weight / headroom_typical.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PLACE = 0.18 * 2 ** 3.5          # the raw's white (1.0) lands 3.5 stops over grey, a typical headroom


def expert_settings(catalog: Path, expert: str = "C") -> dict[str, dict]:
    """Image base name → the expert's develop settings (numbers only)."""
    con = sqlite3.connect(catalog)
    q = """select f.baseName, s.text, m.value from AgLibraryCollection c
           join AgLibraryCollectionImage ci on ci.collection = c.id_local
           join Adobe_images i on i.id_local = ci.image
           join AgLibraryFile f on f.id_local = i.rootFile
           join Adobe_imageDevelopSettings s on s.image = i.id_local
           left join AgHarvestedExifMetadata e on e.image = i.id_local
           left join AgInternedExifCameraModel m on m.id_local = e.cameraModelRef
           where c.name = ?"""
    num = re.compile(r"^\s*(\w+) = (-?[0-9.]+),?$", re.M)
    out = {}
    for base, text, camera in con.execute(q, (expert,)):
        if text:
            out[base] = {**{k: float(v) for k, v in num.findall(text)}, "camera": camera or "?"}
    return out


def measure_raw(path: str) -> tuple[str, dict]:
    import colour
    import rawpy
    from davigen import color
    from davigen.basic import measure as ms, pipeline as p, settings as st
    s = st.load(None, learned=False)
    s["measure"]["white_balance"]["learned"] = False
    try:
        with rawpy.imread(path) as r:
            rgb = r.postprocess(half_size=True, use_camera_wb=True, no_auto_bright=True, gamma=(1, 1),
                                output_bps=16, output_color=rawpy.ColorSpace.sRGB, user_flip=0)
        rgb = rgb.astype("float64") / 65535.0
        h, w = rgb.shape[:2]
        k = max(1, w // 240)
        rgb = rgb[: h // k * k, : w // k * k].reshape(h // k, k, w // k, k, 3).mean((1, 3))
        m = colour.matrix_RGB_to_RGB(colour.RGB_COLOURSPACES["sRGB"], colour.RGB_COLOURSPACES["DaVinci Wide Gamut"])
        di = p.to_log(np.maximum(rgb @ m.T, 0) * PLACE)
        smp = ms._sample(di, color.lut_path("DAVIGEN_OUT_DWG_TO_REC709_G24"), None, s["measure"])
        return Path(path).stem, {"key": smp.exposure_stops, "white_point": smp.white_point}
    except Exception as e:  # noqa: BLE001 - a broken raw is skipped
        return Path(path).stem, {"error": str(e)}


def headroom(white_point: float, key: float) -> float:
    from davigen import color
    from davigen.basic import pipeline as p
    lut = color.lut_path("DAVIGEN_OUT_DWG_TO_REC709_G24")
    di = np.linspace(0, 1, 2001)
    y = p.luminance(p.apply_lut(np.repeat(di[:, None], 3, 1), lut))
    white = np.interp(white_point, y, di)
    return float(np.log2(max(float(p.to_linear(white)), 1e-6) / 0.18) - key)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dataset", help="the extracted fivek_dataset folder")
    ap.add_argument("--expert", default="C")
    ap.add_argument("--jobs", type=int, default=6)
    args = ap.parse_args()
    root = Path(args.dataset).expanduser()
    cache = root.parent / "features.json"
    settings = expert_settings(root / "raw_photos" / "fivek.lrcat", args.expert)
    feats = json.loads(cache.read_text()) if cache.exists() else {}
    todo = [str(f) for f in sorted((root / "raw_photos").rglob("*.dng")) if f.stem not in feats]
    with ProcessPoolExecutor(args.jobs) as pool:
        for n, (stem, d) in enumerate(pool.map(measure_raw, todo, chunksize=4), 1):
            feats[stem] = d
            if n % 100 == 0:
                cache.write_text(json.dumps(feats))
                print(f"{n}/{len(todo)}", flush=True)
    cache.write_text(json.dumps(feats))

    rows = []
    for name, v in settings.items():
        f = feats.get(name, {})
        k = f.get("key", f.get("exposure_stops"))          # older caches used the Sample field name
        if k is not None and "white_point" in f:
            rows.append(({"key": k, "white_point": f["white_point"]}, v))
    key = np.array([f["key"] for f, _ in rows])
    final = key + np.array([v.get("Exposure", 0.0) for _, v in rows])
    room = np.array([headroom(f["white_point"], f["key"]) for f, _ in rows])
    cams = np.array([v["camera"] for _, v in rows])
    centred = np.full(len(rows), np.nan)
    for cam in set(cams):
        sel = cams == cam
        if sel.sum() >= 10:
            centred[sel] = final[sel] - np.median(final[sel])
    ok = ~np.isnan(centred) & (room + key < 5.5)      # leave out raws whose white sits at the placement ceiling
    a, b = np.polyfit(room[ok], centred[ok], 1)
    r2 = 1 - np.var(centred[ok] - (a * room[ok] + b)) / np.var(centred[ok])
    print(f"{ok.sum()} images, {len(set(cams[ok]))} cameras, expert {args.expert}")
    print(f"final key = {a:+.2f} × headroom {b:+.2f}   (R² {r2:.2f}; zero at a headroom of {-b / a:.1f} stops)")
    print(f"→ headroom_weight = {-a:.2f}, headroom_typical = {-b / a:.1f}")


if __name__ == "__main__":
    main()
