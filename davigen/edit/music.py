"""Tempo, beats, bars and sections of a music track (concept §4), in numpy only.

Beat tracking follows Ellis (2007), "Beat Tracking by Dynamic Programming" – the method behind librosa's
beat_track: an onset-strength envelope, a global tempo from its autocorrelation, then the beat sequence that
best balances strong onsets against a steady tempo.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

RATE = 22050
N_FFT, HOP = 2048, 512
FRAME = HOP / RATE              # seconds per onset frame (~23 ms)
# an onset shows in the frame whose window centre it has just passed, one hop after the difference starts rising:
# frame k is heard at k·FRAME + LATENCY (measured on click tracks: beats land within ±5 ms)
LATENCY = (N_FFT / 2 + HOP) / RATE


@dataclass
class Music:
    duration: float
    tempo: float                                        # BPM
    beats: list[float] = field(default_factory=list)    # seconds
    downbeats: list[float] = field(default_factory=list)
    sections: list[dict] = field(default_factory=list)  # [{"start", "end", "energy" 0–1}]

    def to_dict(self) -> dict:
        return asdict(self)


def mel_filters(n_mels: int = 64, fmin: float = 30.0, fmax: float = 8000.0) -> np.ndarray:
    def hz_to_mel(f):
        return 2595 * np.log10(1 + f / 700)

    def mel_to_hz(m):
        return 700 * (10 ** (m / 2595) - 1)
    freqs = np.fft.rfftfreq(N_FFT, 1 / RATE)
    edges = mel_to_hz(np.linspace(hz_to_mel(fmin), hz_to_mel(fmax), n_mels + 2))
    bank = np.zeros((n_mels, len(freqs)))
    for i in range(n_mels):
        lo, mid, hi = edges[i], edges[i + 1], edges[i + 2]
        bank[i] = np.clip(np.minimum((freqs - lo) / (mid - lo), (hi - freqs) / (hi - mid)), 0, None)
    return bank


def spectrogram(samples: np.ndarray) -> np.ndarray:
    """Log-mel spectrogram, frames × 64."""
    if len(samples) < N_FFT:
        samples = np.pad(samples, (0, N_FFT - len(samples)))
    count = 1 + (len(samples) - N_FFT) // HOP
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(count)[:, None]
    window = np.hanning(N_FFT).astype("float32")
    mag = np.empty((count, N_FFT // 2 + 1), dtype="float32")
    for a in range(0, count, 2048):                     # in chunks: a song is ~10k frames
        mag[a:a + 2048] = np.abs(np.fft.rfft(samples[idx[a:a + 2048]] * window, axis=1))
    return np.log1p(1000 * (mag ** 2) @ mel_filters().T)


def onset_envelope(spec: np.ndarray) -> np.ndarray:
    """Spectral flux: summed positive change across mel bands, detrended and normalised."""
    flux = np.maximum(np.diff(spec, axis=0, prepend=spec[:1]), 0).sum(axis=1)
    k = int(round(1.0 / FRAME))                          # remove the slow loudness trend (~1 s)
    trend = np.convolve(np.pad(flux, (k // 2, k - 1 - k // 2), mode="edge"), np.ones(k) / k, mode="valid")
    env = np.maximum(flux - trend, 0)
    return env / max(float(env.std()), 1e-9)


def tempo(env: np.ndarray, lo: float = 60.0, hi: float = 180.0, prior: float = 120.0) -> float:
    """Global tempo in BPM: autocorrelation peak, weighted by a log-normal prior around 120 BPM."""
    n = len(env)
    if n < 10:
        return prior
    spectrum = np.fft.rfft(env - env.mean(), n=2 * n)
    ac = np.fft.irfft(np.abs(spectrum) ** 2)[:n]
    lags = np.arange(1, n)
    bpm = 60.0 / (lags * FRAME)
    ok = (bpm >= lo) & (bpm <= hi)
    weight = np.exp(-0.5 * (np.log2(bpm / prior) / 1.0) ** 2)
    score = np.where(ok, ac[1:] * weight, -np.inf)
    best = int(np.argmax(score)) + 1
    lag = float(best)
    if 1 < best < n - 1:                                 # parabolic refinement of the peak
        y0, y1, y2 = ac[best - 1], ac[best], ac[best + 1]
        curve = y0 - 2 * y1 + y2
        if curve < 0:
            lag = best + float(np.clip(0.5 * (y0 - y2) / curve, -0.5, 0.5))
    return float(np.clip(60.0 / (lag * FRAME), lo, hi))


def track_beats(env: np.ndarray, bpm: float, tightness: float = 100.0) -> np.ndarray:
    """Beat frames by dynamic programming (Ellis 2007)."""
    period = 60.0 / bpm / FRAME
    n = len(env)
    score = env.copy().astype(float)
    back = np.full(n, -1)
    lo, hi = int(round(period / 2)), int(round(2 * period))
    offsets = np.arange(-hi, -lo + 1)
    penalty = -tightness * np.log(-offsets / period) ** 2
    for t in range(n):
        prev = t + offsets
        valid = prev >= 0
        if not valid.any():
            continue
        cand = score[prev[valid]] + penalty[valid]
        j = int(np.argmax(cand))
        if cand[j] > 0:
            score[t] = env[t] + cand[j]
            back[t] = prev[valid][j]
    # start from the best score in the last period, then walk back
    tail = max(0, n - int(period))
    t = tail + int(np.argmax(score[tail:]))
    beats = []
    while t >= 0:
        beats.append(t)
        t = back[t]
    beats = np.array(beats[::-1])
    # drop weak beats at the very start and end (fade in/out)
    strong = env[beats] > 0.1 * np.median(env[beats]) if len(beats) else beats.astype(bool)
    return beats[strong] if strong.any() else beats


def downbeat_phase(env: np.ndarray, beats: np.ndarray, spec: np.ndarray, meter: int = 4) -> int:
    """Which beat of every `meter` starts the bar: the phase with the strongest low-frequency onsets."""
    if len(beats) < meter * 2:
        return 0
    low = np.maximum(np.diff(spec[:, :12], axis=0, prepend=spec[:1, :12]), 0).sum(axis=1)
    strength = low[beats] + 0.5 * env[beats]
    return int(np.argmax([strength[k::meter].mean() for k in range(meter)]))


def sections(spec: np.ndarray, downbeats: np.ndarray, duration: float, min_bars: int = 8) -> list[dict]:
    """Parts of the song: boundaries where loudness per bar changes most, at least `min_bars` apart."""
    if len(downbeats) < 2:
        return [{"start": 0.0, "end": duration, "energy": 0.5}]
    loud = spec.mean(axis=1)
    bars = np.append(downbeats, len(loud) - 1)
    per_bar = np.array([loud[a:max(b, a + 1)].mean() for a, b in zip(bars[:-1], bars[1:])])
    level = np.convolve(np.pad(per_bar, (1, 0), mode="edge"), np.ones(2) / 2, mode="valid")   # no zero edges
    novelty = np.abs(np.diff(level, prepend=level[:1]))
    cuts = [0]
    for i in np.argsort(-novelty):
        if novelty[i] < 0.25 * novelty.max() or i == 0:
            continue
        if all(abs(i - c) >= min_bars for c in cuts) and i <= len(per_bar) - min_bars // 2:
            cuts.append(int(i))
    cuts = sorted(cuts) + [len(per_bar)]
    lo, hi = float(per_bar.min()), float(per_bar.max())
    out = []
    for a, b in zip(cuts[:-1], cuts[1:]):
        start = float(downbeats[a] * FRAME)
        end = float(downbeats[b] * FRAME) if b < len(downbeats) else duration
        energy = (float(per_bar[a:b].mean()) - lo) / max(hi - lo, 1e-9)
        out.append({"start": start, "end": end, "energy": round(energy, 3)})
    out[0]["start"] = 0.0
    return out


def analyse(samples: np.ndarray, meter: int = 4) -> Music:
    duration = len(samples) / RATE
    spec = spectrogram(samples.astype("float32"))
    env = onset_envelope(spec)
    bpm = tempo(env)
    beats = track_beats(env, bpm)
    phase = downbeat_phase(env, beats, spec, meter)
    downs = beats[phase::meter]
    secs = sections(spec, downs, duration)
    for sec in secs[1:]:
        sec["start"] = round(sec["start"] + LATENCY, 3)
    for a, b in zip(secs[:-1], secs[1:]):
        a["end"] = b["start"]
    return Music(duration=duration, tempo=round(bpm, 2),
                 beats=[round(float(b * FRAME + LATENCY), 3) for b in beats],
                 downbeats=[round(float(b * FRAME + LATENCY), 3) for b in downs], sections=secs)
