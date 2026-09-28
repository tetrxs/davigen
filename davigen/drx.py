"""Generate Resolve grade files (.drx): Color Space Transform nodes, and keyframed primaries.

A .drx is XML whose <Body> holds ``0x81 + zstd(protobuf)``. The protobuf carries the
CST parameters as plain enum strings (e.g. ``VLOG_COLORSPACE``), so a template
exported once from Resolve can be rewritten for any input/output combination.

The XML is edited as text (tag names like ``Gallery::GyStill`` are not valid XML
for ElementTree) so everything outside the changed <Body> stays byte-identical.
"""

from __future__ import annotations

import copy
import re
import shutil
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path

try:  # Python 3.14+ ships zstd in the standard library.
    from compression import zstd as _zstd
except ImportError:  # pragma: no cover - fallback for older interpreters
    _zstd = None

BODY_PREFIX = b"\x81"
CST_PLUGIN_ID = b"com.blackmagicdesign.resolvefx.colorspacetransformv2"


# --------------------------------------------------------------------------- zstd

def _decompress(data: bytes) -> bytes:
    if _zstd is not None:
        return _zstd.decompress(data)
    return _zstd_cli(["-dc"], data)


def _compress(data: bytes) -> bytes:
    if _zstd is not None:
        return _zstd.compress(data)
    return _zstd_cli(["-c", "-q"], data)


def _zstd_cli(args: list[str], data: bytes) -> bytes:
    # Resolve's script process has a minimal PATH, so also look in the usual Homebrew locations
    exe = shutil.which("zstd") or shutil.which("zstd", path="/opt/homebrew/bin:/usr/local/bin")
    if not exe:
        raise RuntimeError("No zstd support: use Python 3.14+ or install the zstd CLI")
    return subprocess.run([exe, *args], input=data, capture_output=True, check=True).stdout


# ----------------------------------------------------------------- protobuf wire

def _read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        if pos >= len(buf):
            raise ValueError("truncated varint")
        byte = buf[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, pos
        shift += 7
        if shift > 63:
            raise ValueError("varint too long")


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


@dataclass
class Field:
    number: int
    wire: int
    raw: bytes                   # raw payload for varint / fixed fields, or bytes for wire 2
    message: "Message | None" = None  # parsed payload when a wire-2 field is a sub-message


def _looks_like_text(data: bytes) -> bool:
    return bool(data) and all(32 <= b < 127 for b in data)


class Message(list):
    """Ordered list of Fields; re-serialises with freshly computed lengths."""

    def encode(self) -> bytes:
        out = bytearray()
        for f in self:
            out += _varint((f.number << 3) | f.wire)
            if f.wire == 2:
                payload = f.message.encode() if f.message is not None else f.raw
                out += _varint(len(payload)) + payload
            else:
                out += f.raw
        return bytes(out)

    def get(self, number: int) -> list[Field]:
        return [f for f in self if f.number == number]


def parse(buf: bytes) -> Message:
    msg = Message()
    pos = 0
    while pos < len(buf):
        key, pos = _read_varint(buf, pos)
        number, wire = key >> 3, key & 7
        if number == 0:
            raise ValueError("field number 0")
        if wire == 0:
            start = pos
            _, pos = _read_varint(buf, pos)
            msg.append(Field(number, wire, buf[start:pos]))
        elif wire == 1:
            msg.append(Field(number, wire, buf[pos:pos + 8]))
            pos += 8
        elif wire == 5:
            msg.append(Field(number, wire, buf[pos:pos + 4]))
            pos += 4
        elif wire == 2:
            length, pos = _read_varint(buf, pos)
            payload = buf[pos:pos + length]
            if len(payload) != length:
                raise ValueError("truncated field")
            pos += length
            msg.append(Field(number, wire, payload, _try_parse(payload)))
        else:
            raise ValueError(f"unsupported wire type {wire}")
        if pos > len(buf):
            raise ValueError("overrun")
    return msg


def _try_parse(payload: bytes) -> Message | None:
    if not payload or _looks_like_text(payload):
        return None
    try:
        return parse(payload)
    except ValueError:
        return None


# ------------------------------------------------------------------- CST editing

def _walk(msg: Message):
    for f in msg:
        yield msg, f
        if f.message is not None:
            yield from _walk(f.message)


def _find_cst_param_lists(root: Message) -> list[Message]:
    """Messages that hold the CST OFX parameter entries (field 5 entries with names)."""
    found = []
    for parent, f in _walk(root):
        if f.message is None:
            continue
        names = {_entry_name(e) for e in f.message.get(5) if e.message is not None}
        if {"inputColorSpace", "outputGamma"} <= names:
            found.append(f.message)
    return found


def _entry_name(entry: Field) -> str | None:
    if entry.message is None:
        return None
    name = entry.message.get(1)
    return name[0].raw.decode() if name and _looks_like_text(name[0].raw) else None


def _string_entry(name: str, value: str) -> Field:
    inner = Message([Field(5, 2, value.encode())])
    entry = Message([Field(1, 2, name.encode()), Field(2, 2, inner.encode(), inner)])
    return Field(5, 2, entry.encode(), entry)


def set_cst_params(params_list: Message, values: dict[str, str | None]) -> None:
    """Set enum-string parameters; a value of None removes the entry (= Resolve default)."""
    for name, value in values.items():
        entries = [e for e in params_list if e.number == 5 and _entry_name(e) == name]
        if value is None:
            for e in entries:
                params_list.remove(e)
            continue
        if entries:
            inner = Message([Field(5, 2, value.encode())])
            entries[0].message[:] = [f for f in entries[0].message if f.number != 2]
            entries[0].message.append(Field(2, 2, inner.encode(), inner))
        else:
            # keep entries alphabetical like Resolve writes them
            new = _string_entry(name, value)
            idx = next((i for i, e in enumerate(params_list)
                        if e.number == 5 and (_entry_name(e) or "") > name), len(params_list))
            params_list.insert(idx, new)


def read_cst_params(params_list: Message) -> dict[str, str]:
    out = {}
    for e in params_list.get(5):
        name = _entry_name(e)
        if not name or e.message is None:
            continue
        val = e.message.get(2)
        if val and val[0].message is not None:
            s = val[0].message.get(5)
            if s and _looks_like_text(s[0].raw):
                out[name] = s[0].raw.decode()
    return out


# ---------------------------------------------------------------------- DRX files

_BODY_RE = re.compile(r"<Body>([0-9a-f]+)</Body>")


def decode_body(hex_body: str) -> bytes:
    raw = bytes.fromhex(hex_body)
    if not raw.startswith(BODY_PREFIX):
        raise ValueError("unexpected DRX body prefix")
    return _decompress(raw[1:])


def encode_body(payload: bytes) -> str:
    return (BODY_PREFIX + _compress(payload)).hex()


def read_cst(drx_text: str) -> dict[str, str]:
    for hex_body in _BODY_RE.findall(drx_text):
        root = parse(decode_body(hex_body))
        lists = _find_cst_param_lists(root)
        if lists:
            return read_cst_params(lists[0])
    raise ValueError("no Color Space Transform found in DRX")


def make_cst_drx(template_text: str, values: dict[str, str | None]) -> str:
    """Return DRX text whose (single) CST node uses the given parameter values."""
    changed = False

    def replace(match: re.Match) -> str:
        nonlocal changed
        payload = decode_body(match.group(1))
        if CST_PLUGIN_ID not in payload:
            return match.group(0)
        root = parse(payload)
        for params_list in _find_cst_param_lists(root):
            set_cst_params(params_list, values)
            changed = True
        # parent lengths are recomputed by Message.encode()
        return f"<Body>{encode_body(root.encode())}</Body>"

    out = _BODY_RE.sub(replace, template_text)
    if not changed:
        raise ValueError("template contains no Color Space Transform")
    return out


def write_cst_drx(template: Path, target: Path, values: dict[str, str | None]) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(make_cst_drx(template.read_text(encoding="utf-8"), values))
    return target


# ------------------------------------------------------------------ keyframed primaries

# Found by exporting stills from Resolve 21 and rendering crafted grades (concept BASIC_CORRECTION §12).
# A node block (field 7 of the grade) carries its label in field 6 and its keyframe tracks in field 9; track 1 holds
# the primaries. A track's entries (field 6) are the untimed base value, then one entry per keyframe whose field 1
# is the time: twice the absolute source frame, in the clip's own frame rate. Every parameter is
# {1: id, 2: {1: float}}, and Resolve ignores parameters that aren't sorted by id.
KEYFRAME_TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "drx" / "KEYFRAME_BASE.drx"
PRIMARIES_TRACK = 1
P_SATURATION = 100663301
P_OFFSET = (100663421, 100663422, 100663423)
P_LUM_MIX = 2248146955
P_PIVOT = 2248147136
P_CONTRAST = 2248147137
OFFSET_SCALE = 0.18328          # one unit of the Offset parameter moves a DaVinci Intermediate code value by this
TIME_PER_FRAME = 2


def _int(fields, number: int) -> int | None:
    return next((_read_varint(f.raw, 0)[0] for f in fields if f.number == number and f.wire == 0), None)


def _f32(value: float) -> bytes:
    return struct.pack("<f", float(value))


def _param(pid: int, value: float) -> Field:
    inner = Message([Field(1, 5, _f32(value))])
    msg = Message([Field(1, 0, _varint(pid)), Field(2, 2, b"", inner)])
    return Field(3, 2, b"", msg)


def _set_params(entry: Field, values: dict[int, float]) -> None:
    """Set float parameters in a track entry; kept sorted by id."""
    body = next(f for f in entry.message if f.number == 2).message
    params = {_int(f.message, 1): f for f in body if f.number == 3}
    for pid, value in values.items():
        params[pid] = _param(pid, value)
    body[:] = [f for f in body if f.number != 3] + [params[k] for k in sorted(params)]


def _node_blocks(root: Message) -> dict[str, Message]:
    out = {}
    for blk in (f for f in root[0].message if f.number == 7):
        label = next((x.raw for x in blk.message if x.number == 6), b"")
        out[label.decode("utf-8", "replace")] = blk.message
    return out


def _retime(track: Message, times: list[int]) -> list[Field]:
    """A track's entries for `times`: the base entry, then a copy of the template's first timed entry per time."""
    entries = [f for f in track if f.number == 6]
    base = next(e for e in entries if _int(e.message, 1) is None)
    proto = next(e for e in entries if _int(e.message, 1) is not None)
    timed = []
    for t in times:
        e = copy.deepcopy(proto)
        next(x for x in e.message if x.number == 1).raw = _varint(t * TIME_PER_FRAME)
        timed.append(e)
    rest = [f for f in track if f.number != 6]
    track[:] = rest + [base] + timed
    return [base] + timed


def make_keyframe_drx(template_text: str, frames: list[int], nodes: dict[str, list[dict[int, float]]]) -> str:
    """A grade whose nodes carry primaries keyframes at the given source frames.

    nodes: label → one {parameter id: value} per frame (the same ids each time). The base value is the first
    keyframe's. Resolve keyframes every node of a grade together, so nodes not named get the same times with
    their template values.
    """
    frames = [int(f) for f in frames]
    if not frames or sorted(set(frames)) != frames:
        raise ValueError("keyframes need increasing source frames")
    for label, values in nodes.items():
        if len(values) != len(frames):
            raise ValueError(f"{label}: {len(values)} values for {len(frames)} keyframes")
    hex_body = _BODY_RE.findall(template_text)[0]
    root = parse(decode_body(hex_body))
    blocks = _node_blocks(root)
    missing = set(nodes) - set(blocks)
    if missing:
        raise ValueError(f"template has no node {sorted(missing)}")
    for label, blk in blocks.items():
        track9 = next(x for x in blk if x.number == 9).message
        for track in (x for x in track9 if x.number == 1 and x.message is not None):
            entries = _retime(track.message, frames)
            if label in nodes and _int(track.message, 1) == PRIMARIES_TRACK:
                _set_params(entries[0], nodes[label][0])
                for e, values in zip(entries[1:], nodes[label]):
                    _set_params(e, values)
    return template_text.replace(hex_body, encode_body(root.encode()), 1)


def read_keyframes(drx_text: str) -> dict[str, list[tuple[int | None, dict[int, float]]]]:
    """label → [(source frame or None for the base value, {parameter id: float})] of the primaries track."""
    root = parse(decode_body(_BODY_RE.findall(drx_text)[0]))
    out = {}
    for label, blk in _node_blocks(root).items():
        track9 = next((x for x in blk if x.number == 9), None)
        rows = []
        for track in (x for x in (track9.message if track9 is not None and track9.message is not None else [])
                      if x.number == 1 and x.message is not None):
            if _int(track.message, 1) != PRIMARIES_TRACK:
                continue
            for e in (f for f in track.message if f.number == 6):
                t = _int(e.message, 1)
                body = next(f for f in e.message if f.number == 2).message
                values = {}
                for prm in (f for f in body if f.number == 3):
                    val = next((x for x in prm.message if x.number == 2), None)
                    num = next((x for x in val.message if x.number == 1 and x.wire == 5), None) if val and val.message else None
                    if num is not None:
                        values[_int(prm.message, 1)] = struct.unpack("<f", num.raw)[0]
                rows.append((None if t is None else t // TIME_PER_FRAME, values))
        out[label] = rows
    return out
