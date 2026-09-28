"""Transcribe the speech stretches with Whisper (mlx-whisper, Apple Silicon), in a separate process.

Whisper runs outside Resolve's script process (GPU work inside fuscript is asking for trouble) with davigen's
own Python. It is optional: `install()` adds mlx-whisper to davigen's runtime once; the model (~1.6 GB) is fetched
from Hugging Face on first use and cached in ~/.cache/huggingface. Whisper doubles as a check of the voice
detector: a stretch where it hears no words wasn't speech.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

from ..config import ROOT

HALLUCINATIONS = {"thank you.", "thanks for watching!", "thank you for watching.", "you", "vielen dank.",
                  "untertitel im auftrag des zdf, 2017", "untertitel der amara.org-community", "sous-titrage st' 501",
                  "merci.", "."}


def python() -> str:
    """davigen's own Python (not Resolve's embedded one)."""
    runtime = ROOT / "runtime" / "python" / "bin" / "python"
    return str(runtime) if runtime.exists() else sys.executable


def supported() -> bool:
    return platform.system() == "Darwin" and platform.machine() == "arm64"


def available() -> bool:
    if not supported():
        return False
    res = subprocess.run([python(), "-c", "import mlx_whisper"], capture_output=True, timeout=120)
    return res.returncode == 0


def install() -> str:
    """pip install mlx-whisper into davigen's runtime. Returns '' or an error."""
    res = subprocess.run([python(), "-m", "pip", "install", "--quiet", "mlx-whisper"], capture_output=True,
                         text=True, encoding="utf-8", errors="replace", timeout=1800)
    return "" if res.returncode == 0 else (res.stderr or res.stdout).strip()[-400:]


def run(jobs: list[dict], model: str, timeout: float = 3600) -> list[dict]:
    """jobs: [{"path", "start", "end"}] → [{"text", "language", "segments": [{"start", "end", "text"}]}], with
    times in seconds of the clip."""
    if not jobs:
        return []
    with tempfile.TemporaryDirectory(prefix="davigen_whisper_") as tmp:
        src, dst = Path(tmp) / "jobs.json", Path(tmp) / "out.json"
        src.write_text(json.dumps({"jobs": jobs, "model": model}), encoding="utf-8")
        env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8")
        res = subprocess.run([python(), "-m", "davigen.edit.transcribe", str(src), str(dst)], capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=timeout, env=env)
        if res.returncode != 0 or not dst.exists():
            raise RuntimeError(f"Whisper failed: {(res.stderr or res.stdout).strip()[-400:]}")
        return json.loads(dst.read_text(encoding="utf-8"))["results"]


def clean(segments: list[dict], min_logprob: float = -1.0, max_no_speech: float = 0.6) -> list[dict]:
    """Drop what Whisper itself doubts, and its well-known hallucinations on silence and wind."""
    out = []
    for s in segments:
        text = (s.get("text") or "").strip()
        if not text or text.lower() in HALLUCINATIONS:
            continue
        if s.get("avg_logprob", 0) < min_logprob or s.get("no_speech_prob", 0) > max_no_speech:
            continue
        if s.get("compression_ratio", 1) > 2.4:          # "ja ja ja ja ja …"
            continue
        out.append({"start": float(s["start"]), "end": float(s["end"]), "text": text})
    return out


def srt(entries: list[dict]) -> str:
    """entries: [{"start", "end", "text"}] in seconds → SubRip text."""
    def tc(t):
        ms = int(round(t * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
    return "\n".join(f"{n}\n{tc(e['start'])} --> {tc(e['end'])}\n{e['text']}\n" for n, e in enumerate(entries, 1))


def _worker(src: Path, dst: Path) -> None:
    """Runs in davigen's Python: decode each stretch, transcribe, keep what survives `clean`."""
    import mlx_whisper  # noqa: PLC0415 - only in the worker process

    from .decode import audio  # noqa: PLC0415
    spec = json.loads(src.read_text(encoding="utf-8"))
    results = []
    for job in spec["jobs"]:
        samples = audio(job["path"], 16000, start=job["start"], length=job["end"] - job["start"])
        if len(samples) < 16000 * 0.5:
            results.append({"text": "", "language": "", "segments": []})
            continue
        r = mlx_whisper.transcribe(samples, path_or_hf_repo=spec["model"], condition_on_previous_text=False,
                                   verbose=None)
        segs = [dict(s, start=s["start"] + job["start"], end=s["end"] + job["start"]) for s in r["segments"]]
        kept = clean(segs, spec.get("min_logprob", -1.0), spec.get("max_no_speech", 0.6))
        results.append({"text": " ".join(s["text"] for s in kept), "language": r.get("language", ""),
                        "segments": kept})
    dst.write_text(json.dumps({"results": results}, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    _worker(Path(sys.argv[1]), Path(sys.argv[2]))
