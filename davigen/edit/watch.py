"""Watch a clip once: per-frame picture measures and per-window audio measures (concept §2).

Everything is plain numpy on small grey frames, so a clip costs roughly its decode time. Results are cached per
file (size + modification time), like Basic Correction's samples.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, fields
from pathlib import Path

import numpy as np

from . import decode

FPS = 5.0
WIDTH = 320
AUDIO_RATE = 16000
AUDIO_HOP = 0.2                 # seconds per loudness / voice value
VERSION = 2                     # bump when the measures change, so old caches are ignored


@dataclass
class Watch:
    fps: float
    duration: float
    motion: np.ndarray          # (n, 2) shift to the previous frame, in frame widths per second (x, y)
    shake: np.ndarray           # (n,) jitter: motion minus its 1 s average, frame widths per second
    sharpness: np.ndarray       # (n,) Laplacian variance / contrast, ~0.05 blurred … 1+ crisp
    brightness: np.ndarray      # (n,) mean luma 0–1 (display, as decoded)
    detail: np.ndarray          # (n,) mean gradient magnitude
    change: np.ndarray          # (n,) histogram distance to the previous frame, 0–1
    loudness: np.ndarray        # (m,) dBFS per AUDIO_HOP
    voice: np.ndarray           # (m,) 0–1 likelihood of speech per AUDIO_HOP

    @property
    def times(self) -> np.ndarray:
        return np.arange(len(self.sharpness)) / self.fps

    @property
    def audio_times(self) -> np.ndarray:
        return np.arange(len(self.loudness)) * AUDIO_HOP


# ---------------------------------------------------------------------------------------------- picture

def phase_shift(a: np.ndarray, b: np.ndarray, window: np.ndarray) -> tuple[float, float]:
    """Global translation from b to a in pixels (dx, dy), by phase correlation."""
    fa, fb = np.fft.rfft2(a * window), np.fft.rfft2(b * window)
    cross = fa * np.conj(fb)
    corr = np.fft.irfft2(cross / np.maximum(np.abs(cross), 1e-9), s=a.shape)
    dy, dx = np.unravel_index(int(np.argmax(corr)), corr.shape)
    h, w = a.shape
    return float(dx - w if dx > w // 2 else dx), float(dy - h if dy > h // 2 else dy)


def sharpness(img: np.ndarray) -> float:
    """Variance of the Laplacian, normalised by the frame's contrast (so a dark frame isn't 'blurred')."""
    lap = img[1:-1, :-2] + img[1:-1, 2:] + img[:-2, 1:-1] + img[2:, 1:-1] - 4 * img[1:-1, 1:-1]
    return float(lap.var() / max(float(img.var()), 1e-4))


def measure_frames(frames: np.ndarray, fps: float) -> dict[str, np.ndarray]:
    n, h, w = frames.shape
    window = np.outer(np.hanning(h), np.hanning(w)).astype("float32")
    small = frames[:, ::2, ::2]                       # phase correlation doesn't need full size
    win_small = window[::2, ::2]
    motion = np.zeros((n, 2))
    change = np.zeros(n)
    hist_prev = None
    for i in range(n):
        if i:
            dx, dy = phase_shift(small[i], small[i - 1], win_small)
            motion[i] = (dx * 2 * fps / w, dy * 2 * fps / w)
        hist = np.histogram(frames[i], bins=32, range=(0, 1))[0] / frames[i].size
        if hist_prev is not None:
            change[i] = 0.5 * float(np.abs(hist - hist_prev).sum())
        hist_prev = hist
    k = max(1, int(round(fps)))                       # 1-second moving average of the motion (edges repeated,
    kernel = np.ones(k) / k                           # so a clip's first and last second don't look shaky)
    smooth = np.stack([np.convolve(np.pad(motion[:, c], (k // 2, k - 1 - k // 2), mode="edge"), kernel,
                                   mode="valid") for c in range(2)], axis=1)
    gy, gx = np.gradient(frames, axis=(1, 2))
    return {
        "motion": motion,
        "shake": np.hypot(*(motion - smooth).T),
        "sharpness": np.array([sharpness(f) for f in frames]),
        "brightness": frames.mean(axis=(1, 2)),
        "detail": np.hypot(gx, gy).mean(axis=(1, 2)),
        "change": change,
    }


# ------------------------------------------------------------------------------------------------ audio

def measure_audio(samples: np.ndarray, rate: int = AUDIO_RATE) -> dict[str, np.ndarray]:
    """Loudness and a voice likelihood per AUDIO_HOP.

    Voice: most energy in the speech band (250–4000 Hz), and that energy rising and falling at the syllable rate
    (2–8 Hz). Music is band-limited too, but steadier; wind and traffic are broadband.
    """
    hop = int(rate * AUDIO_HOP)
    m = len(samples) // hop
    if m == 0:
        return {"loudness": np.zeros(0), "voice": np.zeros(0)}
    blocks = samples[: m * hop].reshape(m, hop)
    loud = 10 * np.log10(np.maximum((blocks ** 2).mean(axis=1), 1e-10))
    # 10 ms spectral frames for the band ratio and the envelope
    n_fft, step = 512, rate // 100
    count = (len(samples) - n_fft) // step
    if count < 10:
        return {"loudness": loud, "voice": np.zeros(m)}
    idx = np.arange(n_fft)[None, :] + step * np.arange(count)[:, None]
    spec = np.abs(np.fft.rfft(samples[idx] * np.hanning(n_fft), axis=1)) ** 2
    freqs = np.fft.rfftfreq(n_fft, 1 / rate)
    band = spec[:, (freqs >= 250) & (freqs <= 4000)].sum(axis=1)
    ratio = band / np.maximum(spec.sum(axis=1), 1e-12)
    env = np.log(band + 1e-9)
    voice = np.zeros(m)
    per_hop = int(AUDIO_HOP * 100)
    win = 100                                           # 1 s of 10 ms frames for the modulation spectrum
    mod_freqs = np.fft.rfftfreq(win, 0.01)
    syllable = (mod_freqs >= 2) & (mod_freqs <= 8)
    for j in range(m):
        centre = j * per_hop + per_hop // 2
        a, b = max(0, centre - win // 2), max(0, centre - win // 2) + win
        if b > count:
            a, b = max(0, count - win), count
        seg = env[a:b] - env[a:b].mean()
        if len(seg) < win:
            continue
        mod = np.abs(np.fft.rfft(seg * np.hanning(win))) ** 2
        mod_share = mod[syllable].sum() / max(mod[1:].sum(), 1e-12)
        band_share = ratio[a:b].mean()
        voice[j] = float(np.clip((band_share - 0.55) / 0.3, 0, 1) * np.clip((mod_share - 0.25) / 0.35, 0, 1))
    voice[loud < -50] = 0.0
    return {"loudness": loud, "voice": voice}


# ------------------------------------------------------------------------------------------------ cache

class Cache:
    def __init__(self, folder: Path):
        self.folder = folder

    def _file(self, path: str) -> Path:
        return self.folder / f"{Path(path).stem}_{hashlib.sha1(path.encode('utf-8')).hexdigest()[:12]}.npz"

    @staticmethod
    def _stamp(path: str) -> str:
        try:
            st = Path(path).stat()
            return f"{VERSION}:{st.st_size}:{int(st.st_mtime)}"
        except OSError:
            return ""

    def load(self, path: str) -> Watch | None:
        f = self._file(path)
        try:
            with np.load(f) as data:
                if str(data["stamp"]) != self._stamp(path):
                    return None
                return Watch(**{fl.name: (float(data[fl.name]) if fl.name in ("fps", "duration") else data[fl.name])
                                for fl in fields(Watch)})
        except (OSError, KeyError, ValueError):
            return None

    def save(self, path: str, w: Watch) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(self._file(path), stamp=np.array(self._stamp(path)),
                            **{fl.name: getattr(w, fl.name) for fl in fields(Watch)})


def watch(path: str, cache: Cache | None = None) -> Watch:
    """Decode and measure a clip, or take it from the cache."""
    if cache is not None:
        hit = cache.load(path)
        if hit is not None:
            return hit
    info = decode.probe(path)
    pics = measure_frames(decode.frames(path, FPS, WIDTH, info["duration"]), FPS)
    snd = measure_audio(decode.audio(path, AUDIO_RATE, info["duration"])) if info["audio"] else \
        {"loudness": np.zeros(0), "voice": np.zeros(0)}
    result = Watch(fps=FPS, duration=info["duration"], **pics, **snd)
    if cache is not None:
        cache.save(path, result)
    return result
