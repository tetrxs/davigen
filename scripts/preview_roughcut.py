"""Render a quick preview of the rough cut, graded with Basic Correction, without Resolve (a development tool).

    .venv/bin/python scripts/preview_roughcut.py \
        --edit ~/Movies/davigen_dev/edit_offline/edit_offline.json \
        --basic ~/Movies/davigen_dev/offline_marseille/record.json \
        --media ~/Movies/davigen/MARSEILLE_2026/01_MEDIA --music song.mp3 --out preview.mp4

Per clip, the whole grade – input LUT, nodes 01–04 of DAVIGEN_AUTO, output LUT – is baked into one 33-point
.cube that ffmpeg's lut3d applies, so this shows what Resolve would show, at 1280 px.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from davigen import color, scanner  # noqa: E402
from davigen.basic import pipeline as p  # noqa: E402
from davigen.config import Config  # noqa: E402

OUTPUT_LUT = color.lut_path("DAVIGEN_OUT_DWG_TO_REC709_G24")
SIZE = 33


def bake(in_lut: Path, nodes: list[p.Cdl], target: Path) -> Path:
    grid = np.linspace(0, 1, SIZE)
    b, g, r = np.meshgrid(grid, grid, grid, indexing="ij")
    rgb = np.stack([r, g, b], axis=-1).reshape(-1, 3)
    out = np.clip(p.apply_lut(p.apply_nodes(p.apply_lut(rgb, in_lut), nodes), OUTPUT_LUT), 0, 1)
    lines = [f"LUT_3D_SIZE {SIZE}"] + [f"{x:.6f} {y:.6f} {z:.6f}" for x, y, z in out]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--edit", required=True)
    ap.add_argument("--basic", required=True)
    ap.add_argument("--media", required=True)
    ap.add_argument("--music", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--fps", type=int, default=25)
    args = ap.parse_args()
    cfg = Config()
    edit = json.loads(Path(args.edit).expanduser().read_text())
    basic = {r["clip"]: r for r in json.loads(Path(args.basic).expanduser().read_text())}
    files = {f.name: f for f in Path(args.media).expanduser().rglob("*") if f.is_file()}
    infos = {ci.name: ci for ci in scanner.scan([str(files[n]) for n in {s["clip_name"] for s in edit["shots"]}], cfg)}
    with tempfile.TemporaryDirectory(prefix="davigen_preview_") as tmp:
        tmp = Path(tmp)
        parts = []
        for n, shot in enumerate(edit["shots"]):
            name = shot["clip_name"]
            ci = infos[name]
            profile = cfg.profiles[ci.profile]
            in_lut = color.lut_path(color.input_lut_name(profile, ci.camera_key))
            if not in_lut.exists():
                in_lut = color.lut_path(color.input_lut_name(profile, ""))
            nodes_d = basic.get(name, {}).get("correction", {}).get("nodes", {})
            nodes = [p.Cdl(tuple(v["slope"]), tuple(v["offset"]), tuple(v["power"]), v["sat"])
                     for _, v in sorted(nodes_d.items())]
            cube = tmp / f"{Path(name).stem}.cube"
            if not cube.exists():
                bake(in_lut, nodes, cube)
            length = shot["record_end"] - shot["record_start"]
            part = tmp / f"{n:03d}.mov"
            vf = (f"scale={args.width}:-2:in_color_matrix=bt709:out_range=pc:flags=bicubic,format=rgb48le,"
                  f"lut3d=file={cube}:interp=tetrahedral,fps={args.fps},"
                  f"crop=iw:min(ih\\,iw*9/16),scale={args.width}:{args.width * 9 // 16},setsar=1,format=yuv420p")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{shot['source_start']:.3f}", "-i", str(files[name]),
                            "-t", f"{length:.3f}", "-an", "-vf", vf, "-c:v", "libx264", "-crf", "20", "-preset",
                            "veryfast", str(part)], check=True)
            parts.append(part)
            print(f"{n + 1}/{len(edit['shots'])} {name}", flush=True)
        listing = tmp / "list.txt"
        listing.write_text("".join(f"file '{x}'\n" for x in parts))
        total = edit["shots"][-1]["record_end"]
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
                        "-i", args.music, "-t", f"{total:.3f}", "-map", "0:v", "-map", "1:a", "-c:v", "copy",
                        "-c:a", "aac", "-b:a", "192k", "-af", f"afade=t=out:st={max(0, total - 2):.2f}:d=2",
                        "-movflags", "+faststart", str(Path(args.out).expanduser())], check=True)
    print(f"written {args.out}")


if __name__ == "__main__":
    main()
