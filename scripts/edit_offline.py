"""Edit Assist without Resolve: watch camera files and print their selects (a development tool).

    .venv/bin/python scripts/edit_offline.py ~/Movies/davigen/MARSEILLE_2026/01_MEDIA [--music song.mp3]
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from davigen.config import Config  # noqa: E402
from davigen.edit import decode, music, roughcut, selects as sel, settings, watch  # noqa: E402

VIDEO = {".mov", ".mp4", ".mxf", ".mts", ".avi"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("folders", nargs="+")
    ap.add_argument("--music", default="")
    ap.add_argument("--out", default=str(Path.home() / "Movies" / "davigen_dev" / "edit_offline"))
    args = ap.parse_args()
    out = Path(args.out).expanduser()
    s = settings.load(Config().workflow)
    files = sorted(p for f in args.folders for p in Path(f).expanduser().rglob("*")
                   if p.suffix.lower() in VIDEO and not p.name.startswith("."))
    cache = watch.Cache(out / "cache")
    with ThreadPoolExecutor(max_workers=2) as pool:
        watched = list(pool.map(lambda p: watch.watch(str(p), cache), files))
    plans, total = [], {"good": 0.0, "unusable": 0.0, "speech": 0.0, "footage": 0.0}
    for n, (path, w) in enumerate(zip(files, watched)):
        segs = sel.segments(w, s["selects"])
        total["footage"] += w.duration
        for x in segs:
            total[x.kind] += x.length
        print(f"{path.name:32s} {w.duration:6.1f}s  " + "  ".join(
            f"{x.kind[0].upper()}{x.start:.0f}-{x.end:.0f}{'(' + x.reason + ')' if x.reason else ''}" for x in segs))
        plans.append(roughcut.ClipPlan(str(n), path.name, path.name, n, segs, w))
    print({k: round(v / 60, 1) for k, v in total.items()}, "minutes")
    result = {"clips": [{"name": p.name, "segments": [x.to_dict() for x in p.segments]} for p in plans]}
    if args.music:
        track = music.analyse(decode.audio(args.music, music.RATE))
        shots = roughcut.plan(plans, track, s["rough_cut"])
        print(f"music {track.tempo} BPM, {len(track.downbeats)} bars, sections {track.sections}")
        for x in shots:
            print(f"  {x.record_start:6.2f}-{x.record_end:6.2f}  {x.clip_name} {x.source_start:.1f}-{x.source_end:.1f}")
        result.update(music=track.to_dict(), shots=[x.to_dict() for x in shots])
    out.mkdir(parents=True, exist_ok=True)
    (out / "edit_offline.json").write_text(json.dumps(result, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
