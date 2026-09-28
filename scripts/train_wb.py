"""Train and evaluate Basic Correction's learned white-balance estimator (concept §13, step 2).

    .venv/bin/python scripts/train_wb.py ~/Movies/davigen_dev/datasets/SimpleCube++

Data: SimpleCube++ (Ershov et al., 2020, CC BY 4.0, https://github.com/Visillect/CubePlusPlus): linear raw images
of a Canon 550D/600D with the illuminant measured on a SpyderCube in every scene. Images and ground truth are
converted from the camera's RGB to linear DaVinci Wide Gamut (via the camera's Adobe DNG colour matrix), and cut
down to the size of davigen's analysis thumbnails, so the model sees what Basic Correction sees.

Model: Convolutional Color Constancy (Barron, ICCV 2015): a learned filter on the log-chroma histogram of the
pixels and of the edges; the illuminant is the soft-argmax of the filtered histogram. Trained with numpy only
(cross-entropy on a smoothed target + smoothness prior, Adam). Needs opencv-python-headless to read 16-bit PNG
(dev only; the trained model is a small .npz davigen reads with numpy).
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from davigen.basic import measure as ms  # noqa: E402
from davigen.basic import pipeline as p  # noqa: E402
from davigen.basic import wb_model  # noqa: E402

BLACK = 2048
THUMB = 96
# Canon EOS 550D / 600D, XYZ (D65) → camera, from Adobe DNG Converter / dcraw's adobe_coeff
CANON_XYZ_TO_CAM = np.array([[6941, -1164, -857], [-3825, 11597, 2534], [-416, 1540, 6039]]) / 10000.0


def cam_to_dwg_matrix() -> np.ndarray:
    colour = p._colour()
    xyz_to_dwg = colour.RGB_COLOURSPACES["DaVinci Wide Gamut"].matrix_XYZ_to_RGB
    return xyz_to_dwg @ np.linalg.inv(CANON_XYZ_TO_CAM)


def load(root: Path, split: str, cache: Path):
    """Thumbnails in linear DWG (n, h, w, 3), validity masks, ground-truth illuminants in DWG (n, 3), names."""
    target = cache / f"{split}.npz"
    if target.exists():
        d = np.load(target)
        return d["thumbs"], d["masks"], d["gt"], list(d["names"])
    import cv2  # noqa: PLC0415 - dev dependency
    m = cam_to_dwg_matrix()
    rows = list(csv.DictReader(open(root / split / "gt.csv")))
    thumbs, masks, gts, names = [], [], [], []
    for n, row in enumerate(rows):
        raw = cv2.imread(str(root / split / "PNG" / f"{row['image']}.png"), cv2.IMREAD_UNCHANGED)[..., ::-1]
        raw = raw.astype("float64")
        sat = raw.max() - 2
        valid = (raw.min(axis=2) > 0) & (raw.max(axis=2) < sat)        # cube masked to 0; saturated out
        cam = np.clip(raw - BLACK, 0, None)
        h, w = cam.shape[:2]
        k = w // THUMB
        hh, ww = h // k, w // k
        c = cam[:hh * k, :ww * k].reshape(hh, k, ww, k, 3)
        v = valid[:hh * k, :ww * k].reshape(hh, k, ww, k)
        weight = v.sum(axis=(1, 3))
        thumb = (c * v[..., None]).sum(axis=(1, 3)) / np.maximum(weight, 1)[..., None]
        dwg = thumb @ m.T
        thumbs.append(dwg / max(float(np.percentile(dwg, 99)), 1e-9))
        masks.append(weight > 0.5 * k * k)
        gt = np.array([float(row["mean_r"]), float(row["mean_g"]), float(row["mean_b"])]) @ m.T
        gts.append(gt / gt.sum())
        names.append(row["image"])
        if n % 200 == 0:
            print(f"  {split}: {n}/{len(rows)}", flush=True)
    cache.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(target, thumbs=np.array(thumbs), masks=np.array(masks), gt=np.array(gts),
                        names=np.array(names))
    return np.array(thumbs), np.array(masks), np.array(gts), names


def angular(a, b) -> np.ndarray:
    a, b = np.atleast_2d(a), np.atleast_2d(b)
    cos = (a * b).sum(1) / np.linalg.norm(a, axis=1) / np.linalg.norm(b, axis=1)
    return np.degrees(np.arccos(np.clip(cos, -1, 1)))


def stats(err: np.ndarray) -> str:
    s = np.sort(err)
    q = len(s) // 4
    return (f"mean {s.mean():5.2f}°  median {np.median(s):5.2f}°  best 25% {s[:q].mean():5.2f}°  "
            f"worst 25% {s[-q:].mean():5.2f}°")


def classic(thumbs, masks) -> dict[str, np.ndarray]:
    """davigen's four estimators and their median, as measure.py runs them."""
    wb = {"minkowski_p": 6, "edge_p": 6, "achromatic_top": 0.1, "iterations": 3}
    out = {k: [] for k in ("grey world", "shades of grey", "grey edge", "achromatic", "median of 4")}
    for img, mask in zip(thumbs, masks):
        lum = p.dwg_luminance(np.maximum(img, 0))
        mid = mask & (lum > np.percentile(lum[mask], 2)) & (lum < np.percentile(lum[mask], 98))
        ests = ms._estimators(np.maximum(img, 0), mid, wb)
        for k, e in zip(("grey world", "shades of grey", "grey edge", "achromatic"), ests):
            out[k].append(e)
        out["median of 4"].append(ms._norm(np.median(ests, axis=0)))
    return {k: np.array(v) for k, v in out.items()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--out", default=str(wb_model.MODEL))
    args = ap.parse_args()
    root = Path(args.root).expanduser()
    cache = root.parent / "davigen_cache"
    tr = load(root, "train", cache)
    te = load(root, "test", cache)
    print(f"train {len(tr[0])}, test {len(te[0])} images")

    print("\ndavigen's classic estimators on the test set (angular error in linear DWG):")
    base = classic(te[0], te[1])
    for k, v in base.items():
        print(f"  {k:15s} {stats(angular(v, te[2]))}")

    x_tr = np.array([wb_model.histograms(img, m) for img, m in zip(tr[0], tr[1])])
    x_te = np.array([wb_model.histograms(img, m) for img, m in zip(te[0], te[1])])
    t = time.time()
    model = wb_model.train(x_tr, tr[2], epochs=args.epochs, validate=(x_te, te[2]), log=print)
    print(f"trained in {time.time() - t:.0f} s")
    pred = np.array([wb_model.predict(model, x) for x in x_te])
    print(f"\n  {'learned (CCC)':15s} {stats(angular(pred, te[2]))}")
    fused = np.array([ms._norm(np.median(np.vstack([base[k][i] for k in
                      ("grey world", "shades of grey", "grey edge", "achromatic")] + [pred[i]] * 2), axis=0))
                      for i in range(len(pred))])
    print(f"  {'median with it':15s} {stats(angular(fused, te[2]))}")
    wb_model.save(model, Path(args.out), note="Trained on SimpleCube++ (Ershov et al. 2020, CC BY 4.0), "
                                              f"test median {np.median(angular(pred, te[2])):.2f} deg")
    print(f"saved {args.out}")


if __name__ == "__main__":
    main()
