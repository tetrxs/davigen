"""Generate Resolve grade files (.drx) for Color Space Transform nodes.

A .drx is XML whose <Body> holds ``0x81 + zstd(protobuf)``. The protobuf carries the
CST parameters as plain enum strings (e.g. ``VLOG_COLORSPACE``), so a template
exported once from Resolve can be rewritten for any input/output combination.

The XML is edited as text (tag names like ``Gallery::GyStill`` are not valid XML
for ElementTree) so everything outside the changed <Body> stays byte-identical.
"""

from __future__ import annotations

import re
import shutil
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
