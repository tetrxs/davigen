"""What a song offers to cut on: beats, bars, phrases and parts, accents and vocal entries – signal analysis only,
no model (docs/concepts/PROJECT_PIPELINE.md §7.8).

Beat tracking follows Ellis (2007), "Beat Tracking by Dynamic Programming" – the method behind librosa's
beat_track: an onset-strength envelope, a global tempo from its autocorrelation, then the beat sequence that
best balances strong onsets against a steady tempo. Parts come from the bars' self-similarity (Foote novelty).

Accents are hits that stand out from the same place in the neighbouring bars, so a steady backbeat isn't one but
a crash on a new phrase is. Vocal entries are estimated: the harmonic part (median-filter HPSS, Fitzgerald 2010) of
what sits in the middle of the stereo image, in the voice band, rising after a pause – lead vocals are mixed to the
centre. They are the least reliable kind and labelled so.
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
    phrases: list[float] = field(default_factory=list)  # every 4 bars
    accents: list[dict] = field(default_factory=list)   # [{"time", "strength"}]
    vocals: list[float] = field(default_factory=list)   # estimated vocal entries

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
    """Parts of the song (verse, chorus, drop, calm bridge): Foote novelty on the bars' self-similarity.

    Each bar is described by its mean log-mel spectrum (timbre) and loudness; a checkerboard kernel slid along
    the diagonal of the bar-by-bar cosine similarity peaks where the music changes character. Boundaries are the
    strongest peaks, at least `min_bars` apart.
    """
    if len(downbeats) < 4:
        return [{"start": 0.0, "end": duration, "energy": 0.5}]
    bars = np.append(downbeats, len(spec) - 1)
    feats = np.array([spec[a:max(b, a + 1)].mean(axis=0) for a, b in zip(bars[:-1], bars[1:])])
    loud = feats.mean(axis=1)
    x = feats - feats.mean(axis=0)
    x /= np.maximum(np.linalg.norm(x, axis=1, keepdims=True), 1e-9)
    sim = x @ x.T
    n, k = len(sim), max(2, min_bars // 2)
    sign = np.sign(np.arange(-k, k) + 0.5)
    kernel = np.outer(sign, sign) * np.outer(np.hanning(2 * k), np.hanning(2 * k))
    padded = np.pad(sim, k, mode="edge")
    novelty = np.array([(padded[i:i + 2 * k, i:i + 2 * k] * kernel).sum() for i in range(n)])
    novelty = np.maximum(novelty, 0)
    threshold = novelty.mean() + 0.5 * novelty.std()
    peaks = [i for i in range(1, n - 1) if novelty[i] >= novelty[i - 1] and novelty[i] >= novelty[i + 1]
             and novelty[i] > threshold]
    cuts = [0]
    for i in sorted(peaks, key=lambda i: -novelty[i]):
        if all(abs(i - c) >= min_bars for c in cuts) and n - i >= min_bars // 2:
            cuts.append(i)
    cuts = sorted(cuts) + [n]
    out = []
    for a, b in zip(cuts[:-1], cuts[1:]):
        start = float(downbeats[a] * FRAME)
        end = float(downbeats[b] * FRAME) if b < len(downbeats) else duration
        # energy relative to this song: the share of its bars that are quieter than this section
        energy = float((loud < loud[a:b].mean()).mean())
        out.append({"start": start, "end": end, "energy": round(energy, 3)})
    out[0]["start"] = 0.0
    return out


def analyse(samples: np.ndarray, meter: int = 4, stereo: np.ndarray | None = None, phrase_bars: int = 4) -> Music:
    """samples: mono at RATE. stereo: (n, 2) at RATE for the vocal entries (mono is used when there is none)."""
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
    down_s = [round(float(b * FRAME + LATENCY), 3) for b in downs]
    return Music(duration=duration, tempo=round(bpm, 2),
                 beats=[round(float(b * FRAME + LATENCY), 3) for b in beats],
                 downbeats=down_s, sections=secs, phrases=down_s[::phrase_bars],
                 accents=accents(spec, env, bpm, meter),
                 vocals=vocal_entries(stereo if stereo is not None else samples))


def accents(spec: np.ndarray, env: np.ndarray, bpm: float, meter: int = 4, ratio: float = 2.0,
            per_bars: float = 4.0) -> list[dict]:
    """Hits clearly stronger than the same moment in the neighbouring bars (up to 4 either side): within one beat
    only the strongest (a drum fill is one accent, not six), at most one per `per_bars` bars on average."""
    from scipy.signal import find_peaks  # noqa: PLC0415
    low = np.maximum(np.diff(spec[:, :12], axis=0, prepend=spec[:1, :12]), 0).sum(axis=1)
    strength = env + 0.5 * low / max(float(low.std()), 1e-9)
    bar = meter * 60.0 / bpm / FRAME
    peaks, _ = find_peaks(strength, distance=max(1, int(0.25 / FRAME)), prominence=float(strength.std()))
    if not len(peaks):
        return []
    floor = float(np.percentile(strength[peaks], 60))
    out = []
    for f in peaks:
        if strength[f] < floor:
            continue
        near = []
        for k in (-4, -3, -2, -1, 1, 2, 3, 4):
            c = int(round(f + k * bar))
            if 2 <= c < len(strength) - 2:
                near.append(float(strength[c - 2:c + 3].max()))
        if len(near) < 2:
            continue
        score = float(strength[f]) / (float(np.median(near)) + 1e-6)
        if score >= ratio:
            out.append({"time": round(float(f * FRAME + LATENCY), 3), "strength": round(score, 2)})
    beat = 60.0 / bpm
    kept: list[dict] = []
    for a in sorted(out, key=lambda a: -a["strength"]):
        if all(abs(a["time"] - k["time"]) >= beat for k in kept):
            kept.append(a)
    bars = len(strength) / bar
    kept = kept[:max(1, int(bars / per_bars))]
    return sorted(kept, key=lambda a: a["time"])


def vocal_entries(audio: np.ndarray, band: tuple[float, float] = (200.0, 4000.0), min_voiced: float = 0.8,
                  min_pause: float = 1.0) -> list[float]:
    """Estimated starts of sung lines, in seconds. audio: mono (n,) or stereo (n, 2) at RATE."""
    from scipy.ndimage import median_filter, uniform_filter1d  # noqa: PLC0415
    audio = np.asarray(audio, dtype="float32")
    if audio.ndim == 2 and audio.shape[1] == 2:
        mid, side = (audio[:, 0] + audio[:, 1]) / 2, (audio[:, 0] - audio[:, 1]) / 2
    else:
        mid, side = audio.reshape(-1), None
    if len(mid) < RATE * 3:
        return []
    freqs = np.fft.rfftfreq(N_FFT, 1 / RATE)
    lo, hi = np.searchsorted(freqs, band[0]), np.searchsorted(freqs, band[1])
    mag_m = _stft_band(mid, lo, hi)
    centre = np.maximum(mag_m - _stft_band(side, lo, hi), 0) if side is not None else mag_m
    harm = median_filter(centre, size=(17, 1), mode="nearest")          # steady in time: tones, voices
    perc = median_filter(centre, size=(1, 17), mode="nearest")          # broad in frequency: hits
    mask = harm ** 2 / (harm ** 2 + perc ** 2 + 1e-12)
    energy = 10 * np.log10((centre * mask) ** 2 + 1e-10).mean(axis=1)
    energy = uniform_filter1d(energy, size=max(1, int(0.25 / FRAME)))
    base = median_filter(energy, size=max(3, int(10.0 / FRAME)), mode="nearest")
    act = energy - base
    spread = float(np.median(np.abs(act - np.median(act)))) or 1.0
    voiced = act > np.median(act) + 0.8 * spread
    entries, run_start, last_end = [], None, -1e9
    for i, v in enumerate(np.append(voiced, False)):
        if v and run_start is None:
            run_start = i
        elif not v and run_start is not None:
            length = (i - run_start) * FRAME
            if length >= min_voiced:
                if (run_start - last_end) * FRAME >= min_pause:
                    entries.append(round(float(run_start * FRAME + LATENCY), 3))
                last_end = i
            run_start = None
    return entries


def _stft_band(x: np.ndarray, lo: int, hi: int) -> np.ndarray:
    """Magnitudes of bins lo…hi, frames × bins (in chunks: a song is ~10k frames)."""
    if len(x) < N_FFT:
        x = np.pad(x, (0, N_FFT - len(x)))
    count = 1 + (len(x) - N_FFT) // HOP
    window = np.hanning(N_FFT).astype("float32")
    out = np.empty((count, hi - lo), dtype="float32")
    for a in range(0, count, 2048):
        idx = np.arange(N_FFT)[None, :] + HOP * np.arange(a, min(a + 2048, count))[:, None]
        out[a:a + len(idx)] = np.abs(np.fft.rfft(x[idx] * window, axis=1))[:, lo:hi]
    return out
