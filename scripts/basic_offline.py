"""Basic Correction without Resolve, on camera files: a development and tuning tool.

    .venv/bin/python scripts/basic_offline.py ~/Movies/davigen/MARSEILLE_2026/01_MEDIA --out ~/Movies/davigen_dev/offline

Frames are decoded with ffmpeg (full range as tagged, Rec.709 matrix), which matched Resolve's analysis render
within 0.001 in step 01. Camera and log profile come from davigen's scanner, the input LUT from the LUT folder,
exactly as the colour groups use them. Writes a before/after contact sheet (PNG via sips), a JSON record and a
CSV, and caches the decoded thumbnails, so re-running with other settings in workflow.toml is instant.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import zlib
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from davigen import color, scanner  # noqa: E402
from davigen.basic import correct as c  # noqa: E402
from davigen.basic import measure as ms  # noqa: E402
from davigen.basic import pipeline as p  # noqa: E402
from davigen.basic import sampling, scenes, settings as settings_mod  # noqa: E402
from davigen.config import Config  # noqa: E402

OUTPUT_LUT = color.lut_path("DAVIGEN_OUT_DWG_TO_REC709_G24")
DECODE_WIDTH = 480
SHEET_WIDTH = 320


def decode(path: str, frame: int, fps: float, width: int = DECODE_WIDTH) -> np.ndarray:
    """One frame as camera code values (uint16). -ss before -i seeks accurately to the frame's time."""
    vf = f"scale={width}:-2:in_color_matrix=bt709:out_range=pc:flags=area,format=rgb48le"
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{frame / fps:.6f}", "-i", path, "-vf", vf, "-frames:v", "1",
           "-f", "rawvideo", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype="<u2").reshape(len(raw) // (width * 6), width, 3)


def clip_frames(path: str) -> tuple[int, float]:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
                          "stream=nb_read_packets,r_frame_rate", "-of", "csv=p=0", path],
                         capture_output=True, text=True).stdout.strip().split(",")
    num, den = out[0].split("/")
    return int(out[1]), float(num) / float(den)


def write_png(path: Path, img: np.ndarray) -> Path:
    """8-bit RGB PNG with the standard library (zlib)."""
    h, w, _ = img.shape
    raw = b"".join(b"\x00" + img[y].astype("uint8").tobytes() for y in range(h))

    def chunk(kind, data):
        return (len(data).to_bytes(4, "big") + kind + data
                + zlib.crc32(kind + data).to_bytes(4, "big"))
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", w.to_bytes(4, "big") + h.to_bytes(4, "big") + bytes([8, 2, 0, 0, 0]))
    png += chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")
    path.write_bytes(png)
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("folders", nargs="+")
    ap.add_argument("--out", default=str(Path.home() / "Movies" / "davigen_dev" / "offline"))
    ap.add_argument("--limit", type=int, default=0, help="only the first N clips")
    args = ap.parse_args()
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    cfg = Config()
    s = settings_mod.load(cfg.workflow)
    cache = sampling.Cache(out / "cache")

    clips = [ci for ci in scanner.scan(args.folders, cfg) if not ci.error]
    clips.sort(key=lambda ci: (ci.created, ci.name))
    if args.limit:
        clips = clips[:args.limit]
    shots, rows, sheet = [], [], []
    for n, ci in enumerate(clips):
        profile = cfg.profiles.get(ci.profile)
        in_lut = color.lut_path(color.input_lut_name(profile, ci.camera_key)) if profile else None
        if in_lut is None or not in_lut.exists():
            in_lut = color.lut_path(color.input_lut_name(profile, "")) if profile else None
        if in_lut is None or not in_lut.exists():
            print(f"skip {ci.name}: no input LUT for {ci.camera_key} / {ci.profile}")
            continue
        total, fps = clip_frames(ci.path)
        frames = sampling.choose_frames(0, total, fps, s["samples"])
        have = cache.load(ci.path)
        new = {f: sampling.thumbnail(decode(ci.path, f, fps)) for f in frames if f not in have}
        cache.save(ci.path, new)
        have.update(new)
        thumbs = [p.apply_lut(sampling.to_float(have[f]), in_lut) for f in frames]
        meta = ms.ClipMeta.from_clip_info(ci)
        meas = ms.measure(thumbs, meta, OUTPUT_LUT, s)
        corr = c.correct(meas, thumbs, OUTPUT_LUT, s)
        shots.append((scenes.Shot(id=ci.name, order=n, meta=meta, measurement=meas, correction=corr,
                                  used_seconds=total / fps, clip_seconds=total / fps), ci, in_lut, frames))
        print(f"{n + 1}/{len(clips)} {ci.name}: {meas.exposure_stops:+.2f} stops, {meas.cct:.0f} K, "
              f"flags {meas.flags}")

    scenes.match_scenes([sh for sh, *_ in shots], s)
    for sh, ci, in_lut, frames in shots:
        corr, meas = sh.correction, sh.measurement
        v = corr.values
        rows.append({"clip": ci.name, "camera": ci.camera_key, "profile": ci.profile, "created": ci.created,
                     "scene": sh.scene, "hero": sh.hero, "confidence": round(corr.overall, 2),
                     "key_stops": round(meas.exposure_stops, 2), "exposure": round(v["exposure_stops"], 2),
                     "reason": v["exposure_reason"], "cct": round(meas.cct), "cct_after": round(v["cct_after"]),
                     "contrast": round(v["contrast"], 3), "saturation": round(v["saturation"], 3),
                     "ev100": None if meas.ev100 is None else round(meas.ev100, 1), "flags": "; ".join(corr.flags)})
        # contact sheet: the middle sample, before | after
        mid = frames[len(frames) // 2]
        total, fps = clip_frames(ci.path)
        dwg = p.apply_lut(decode(ci.path, mid, fps, SHEET_WIDTH).astype("float64") / 65535.0, in_lut)
        before = p.apply_lut(dwg, OUTPUT_LUT)
        after = p.apply_lut(p.apply_nodes(dwg, corr.chain()), OUTPUT_LUT)
        sheet.append(np.concatenate([before, np.ones((before.shape[0], 4, 3)), after], 1))

    if sheet:
        width = max(im.shape[1] for im in sheet)
        padded = [np.pad(im, ((0, 6), (0, width - im.shape[1]), (0, 0))) for im in sheet]
        write_png(out / "contact_sheet.png", (np.clip(np.concatenate(padded), 0, 1) * 255).round())
    with open(out / "report.csv", "w", newline="", encoding="utf-8") as f:
        if rows:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    record = [{"clip": ci.name, "measurement": sh.measurement.to_dict(), "correction": sh.correction.to_dict(),
               "scene": sh.scene, "hero": sh.hero, "notes": sh.notes} for sh, ci, *_ in shots]
    (out / "record.json").write_text(json.dumps(record, indent=1, default=str), encoding="utf-8")
    print(f"\n{len(rows)} clips · {out / 'contact_sheet.png'} · {out / 'report.csv'}")


if __name__ == "__main__":
    main()
