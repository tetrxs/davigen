"""Learned white balance: Convolutional Color Constancy (Barron, ICCV 2015) on linear DWG, numpy only.

A frame becomes two log-chroma histograms (u = log g/r, v = log g/b): one of its pixels, one of its local edges.
A learned filter per histogram, plus a learned bias, turns them into a score for every candidate light; the
light is the soft-argmax. Trained by scripts/train_wb.py; the model is a small .npz next to this file.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

MODEL = Path(__file__).resolve().parent / "models" / "wb_ccc.npz"
BINS = 64
LO, HI = -2.0, 2.0                      # log-chroma range; DWG daylight sits near (0, 0)
STEP = (HI - LO) / BINS

_cache: dict[str, dict] = {}


def _uv(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    r, g, b = (np.maximum(rgb[..., i], 1e-6) for i in range(3))
    return np.log(g / r), np.log(g / b)


def histograms(img: np.ndarray, mask: np.ndarray | None = None) -> np.ndarray:
    """(2, BINS, BINS): normalised log-chroma histograms of pixels and of edges."""
    img = np.maximum(np.asarray(img, dtype="float64"), 0)
    if mask is None:
        mask = np.ones(img.shape[:2], bool)
    ok = mask & (img.min(axis=2) > 1e-4)
    blur = (img + np.roll(img, 1, 0) + np.roll(img, -1, 0) + np.roll(img, 1, 1) + np.roll(img, -1, 1)) / 5
    edges = np.abs(img - blur)
    ok_e = ok & (edges.min(axis=2) > 1e-5)
    out = np.zeros((2, BINS, BINS))
    for c, (src, keep) in enumerate(((img, ok), (edges, ok_e))):
        u, v = _uv(src[keep])
        h, _, _ = np.histogram2d(u, v, bins=BINS, range=[[LO, HI], [LO, HI]])
        out[c] = h / max(h.sum(), 1.0)
    return out


def _uv_to_rgb(u: float, v: float) -> np.ndarray:
    rgb = np.array([np.exp(-u), 1.0, np.exp(-v)])
    return rgb / rgb.sum()


def _centres() -> np.ndarray:
    return LO + STEP * (np.arange(BINS) + 0.5)


def _scores(model: dict, fx: np.ndarray) -> np.ndarray:
    """fx: FFT of the histograms (..., 2, BINS, BINS) → scores (..., BINS, BINS)."""
    return np.fft.ifft2((fx * model["FF"]).sum(axis=-3)).real + model["B"]


def _softmax(s: np.ndarray) -> np.ndarray:
    flat = s.reshape(*s.shape[:-2], -1)
    e = np.exp(flat - flat.max(axis=-1, keepdims=True))
    return (e / e.sum(axis=-1, keepdims=True)).reshape(s.shape)


def predict(model: dict, hist: np.ndarray, with_confidence: bool = False):
    """Illuminant (rgb, sums to 1) from a frame's histograms; optionally a confidence 0–1 (peakiness)."""
    p_ = _softmax(_scores(model, np.fft.fft2(hist)))
    c = _centres()
    u, v = float((p_.sum(axis=1) * c).sum()), float((p_.sum(axis=0) * c).sum())
    rgb = _uv_to_rgb(u, v)
    if not with_confidence:
        return rgb
    uu, vv = np.meshgrid(c, c, indexing="ij")
    spread = float(np.sqrt((p_ * ((uu - u) ** 2 + (vv - v) ** 2)).sum()))     # in log-chroma units
    return rgb, float(np.clip(1 - spread / 0.15, 0, 1))


def train(x: np.ndarray, gt: np.ndarray, epochs: int = 60, steps_per_epoch: int = 10, lr: float = 0.05,
          smooth: float = 1e-3, l2: float = 1e-5, sigma_bins: float = 1.0, validate=None, log=None) -> dict:
    """Full-batch Adam on cross-entropy against a Gaussian around the true light's bin."""
    n = len(x)
    fx = np.fft.fft2(x)                                  # (n, 2, B, B)
    c = _centres()
    uu, vv = np.meshgrid(c, c, indexing="ij")
    gu, gv = _uv(gt)
    target = np.exp(-(((uu[None] - gu[:, None, None]) ** 2 + (vv[None] - gv[:, None, None]) ** 2)
                      / (2 * (sigma_bins * STEP) ** 2)))
    target /= target.sum(axis=(1, 2), keepdims=True)
    F = np.zeros((2, BINS, BINS))
    B = np.zeros((BINS, BINS))
    m = {"F": np.zeros_like(F), "B": np.zeros_like(B)}
    v2 = {"F": np.zeros_like(F), "B": np.zeros_like(B)}
    b1, b2, eps = 0.9, 0.999, 1e-8
    step = 0

    def lap(a):
        return (np.roll(a, 1, -1) + np.roll(a, -1, -1) + np.roll(a, 1, -2) + np.roll(a, -1, -2) - 4 * a)

    for epoch in range(epochs):
        for _ in range(steps_per_epoch):
            model = {"FF": np.fft.fft2(F), "B": B}
            p_ = _softmax(_scores(model, fx))
            ds = (p_ - target) / n                                       # d(mean CE)/d(scores)
            fds = np.fft.fft2(ds)
            gF = np.fft.ifft2(np.conj(fx) * fds[:, None]).real.sum(axis=0) + 2 * smooth * lap(lap(F)) + 2 * l2 * F
            gB = ds.sum(axis=0) + 2 * smooth * lap(lap(B))
            step += 1
            for name, param, grad in (("F", F, gF), ("B", B, gB)):
                m[name] = b1 * m[name] + (1 - b1) * grad
                v2[name] = b2 * v2[name] + (1 - b2) * grad ** 2
                mhat, vhat = m[name] / (1 - b1 ** step), v2[name] / (1 - b2 ** step)
                param -= lr * mhat / (np.sqrt(vhat) + eps)
        if log and (epoch % 10 == 9 or epoch == epochs - 1):
            model = {"FF": np.fft.fft2(F), "B": B}
            ce = float(-(target * np.log(_softmax(_scores(model, fx)) + 1e-12)).sum() / n)
            msg = f"  epoch {epoch + 1}: train CE {ce:.3f}"
            if validate is not None:
                xv, gv_ = validate
                pred = np.array([predict(model, h) for h in xv])
                cos = (pred * gv_).sum(1) / np.linalg.norm(pred, axis=1) / np.linalg.norm(gv_, axis=1)
                msg += f", test median {np.median(np.degrees(np.arccos(np.clip(cos, -1, 1)))):.2f}°"
            log(msg)
    return {"FF": np.fft.fft2(F), "B": B, "F": F}


def save(model: dict, path: Path, note: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, F=model["F"].astype("float32"), B=model["B"].astype("float32"),
                        bins=BINS, lo=LO, hi=HI, note=np.array(note))


def load(path: Path = MODEL) -> dict | None:
    """The trained model, or None if there is none (Basic Correction then uses the classic estimators only)."""
    key = str(path)
    if key not in _cache:
        try:
            with np.load(path) as d:
                if int(d["bins"]) != BINS or float(d["lo"]) != LO or float(d["hi"]) != HI:
                    return None
                F = d["F"].astype("float64")
                _cache[key] = {"FF": np.fft.fft2(F), "B": d["B"].astype("float64"), "F": F, "note": str(d["note"])}
        except (OSError, KeyError, ValueError):
            return None
    return _cache[key]
