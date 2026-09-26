"""Strip personal data from a grade still (.drx) before it goes into templates/drx/.

Resolve writes the gallery path, the source timeline name and JPEG thumbnails of your footage into every
exported still. The grade itself lives in <Body> and is left untouched.

    python scripts/scrub_drx.py templates/drx/*.drx
"""
import re
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path


def grey_jpeg(width: int, height: int) -> bytes:
    """A flat mid-grey JPEG (made via a PNG and macOS' sips, no image library needed)."""
    row = b"\x00" + b"\x80\x80\x80" * width
    raw = zlib.compress(row * height, 9)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", raw) + chunk(b"IEND", b""))
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = Path(tmp, "t.png"), Path(tmp, "t.jpg")
        src.write_bytes(png)
        subprocess.run(["sips", "-s", "format", "jpeg", str(src), "--out", str(dst)], check=True, capture_output=True)
        return dst.read_bytes()


def scrub(text: str) -> str:
    text = re.sub(r"<GalleryPath>[^<]*</GalleryPath>", "<GalleryPath/>", text)
    text = re.sub(r"<SrcHint>[^<]*</SrcHint>", "<SrcHint>davigen</SrcHint>", text)
    text = re.sub(r"<ClipThumbnails>[^<]*</ClipThumbnails>", "<ClipThumbnails/>", text)

    def thumb(m: re.Match) -> str:
        w, h = int(m.group(1)), int(m.group(2))
        return f"{m.group(0)[:m.start(3) - m.start(0)]}{grey_jpeg(w, h).hex()}</Buffer>"

    return re.sub(r"<ImgWidth>(\d+)</ImgWidth>\s*<ImgHeight>(\d+)</ImgHeight>\s*<Buffer>([0-9a-f]*)</Buffer>",
                  thumb, text)


if __name__ == "__main__":
    for name in sys.argv[1:]:
        path = Path(name)
        before = path.read_text(encoding="utf-8")
        path.write_text(scrub(before), encoding="utf-8")
        print(f"{path}: {len(before)} → {path.stat().st_size} bytes")
