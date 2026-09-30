"""New Resolve projects from a template davigen makes itself (concept §2).

Resolve's scripting API refuses the playback frame rate (SetSetting returns False, verified in Resolve 21) and has
no key for the proxy generation location. Both are ordinary project settings inside a project file, though: a .drp
is a zip whose project.xml carries the settings as a zstd-compressed protobuf (the same packing as a .drx).

davigen exports an empty project once per Resolve version (data/templates/), writes the two values into a copy and
creates the project with ProjectManager.ImportProject. Verified on 2026-09-30 in Resolve 21.0.0.48 Free: playback
frame rate 25 and 50, proxy location read back after a round trip. If a future Resolve changes the layout, the
fields aren't found and davigen falls back to CreateProject plus a checked manual step.

Layout of the setup blob (FieldsBlob of <CommonConfig>, entry 'SetupBA'):
    [0x0c type][0x00][outer length] [00000001][inner length] [0x81 + zstd(protobuf)]
    protobuf field 248, fixed32 float  – playback frame rate (absent: Resolve's default, 24)
    protobuf field 36, bytes           – [00000002][length] + protobuf: 37 gallery, 39 cache, 40 proxy location
"""

from __future__ import annotations

import re
import struct
import zipfile
from pathlib import Path

from .config import DATA_DIR
from .resolve_api import ResolveError, open_pm_folder

try:  # Python 3.14+ ships zstd in the standard library (davigen's runtime is 3.14)
    from compression import zstd
except ImportError:  # pragma: no cover
    zstd = None

TEMPLATES = DATA_DIR / "templates"
TEMPLATE_FOLDER = "DAVIGEN_TEMPLATES"          # Project Manager folder for the one short-lived empty project
BASE_NAME = "DAVIGEN_TEMPLATE_BASE"
F_PLAYBACK, F_WORKING, F_PROXY = 248, 36, 40
SETUP_KEY = "SetupBA".encode("utf-16-be").hex()


class TemplateError(RuntimeError):
    pass


# ------------------------------------------------------------------------------------------- protobuf bits

def _varint(b: bytes, i: int) -> tuple[int, int]:
    r = s = 0
    while True:
        c = b[i]
        i += 1
        r |= (c & 0x7F) << s
        s += 7
        if c < 0x80:
            return r, i


def _enc(v: int) -> bytes:
    out = bytearray()
    while True:
        c = v & 0x7F
        v >>= 7
        out.append(c | (0x80 if v else 0))
        if not v:
            return bytes(out)


def fields(b: bytes) -> list[tuple[int, int, int, int]]:
    """Top-level fields: (number, wire type, start, end) – no schema needed."""
    i, out = 0, []
    while i < len(b):
        start = i
        key, i = _varint(b, i)
        f, w = key >> 3, key & 7
        if w == 0:
            _, i = _varint(b, i)
        elif w == 1:
            i += 8
        elif w == 5:
            i += 4
        elif w == 2:
            n, i = _varint(b, i)
            i += n
        else:
            raise TemplateError(f"unexpected wire type {w} at {start}")
        if i > len(b):
            raise TemplateError("truncated protobuf")
        out.append((f, w, start, i))
    return out


def _payload(b: bytes, start: int, end: int, wire: int) -> bytes:
    _, i = _varint(b, start)
    if wire == 2:
        _, i = _varint(b, i)
    return b[i:end]


def get(b: bytes, number: int) -> tuple[int, bytes] | None:
    for f, w, s, e in fields(b):
        if f == number:
            return w, _payload(b, s, e, w)
    return None


def put(b: bytes, number: int, wire: int, payload: bytes) -> bytes:
    """Replace the field, or append it."""
    new = _enc(number << 3 | wire) + (_enc(len(payload)) + payload if wire == 2 else payload)
    for f, _, s, e in fields(b):
        if f == number:
            return b[:s] + new + b[e:]
    return b + new


# --------------------------------------------------------------------------------------------- the blob

def _setup_blob(xml: str) -> re.Match:
    m = next((m for m in re.finditer(r"<FieldsBlob>([0-9a-f]+)</FieldsBlob>", xml) if SETUP_KEY in m.group(1)), None)
    if m is None:
        raise TemplateError("no project settings in the project file")
    return m


def _unpack(blob: bytes) -> tuple[int, int, bytes]:
    i = blob.find(bytes.fromhex("8128b52ffd"))
    if i < 12:
        raise TemplateError("settings aren't zstd-packed as expected")
    inner = struct.unpack(">I", blob[i - 4:i])[0]
    outer = struct.unpack(">I", blob[i - 12:i - 8])[0]
    if outer != inner + 8:
        raise TemplateError("unexpected settings header")
    if zstd is None:
        raise TemplateError("this Python has no zstd (needs 3.14)")
    return i, inner, zstd.decompress(blob[i + 1:i + inner])


def read_settings(drp: Path) -> bytes:
    with zipfile.ZipFile(drp) as z:
        xml = z.read("project.xml").decode("utf-8")
    _, _, setup = _unpack(bytes.fromhex(_setup_blob(xml).group(1)))
    return setup


def playback_fps(setup: bytes) -> float | None:
    got = get(setup, F_PLAYBACK)
    return struct.unpack("<f", got[1])[0] if got and got[0] == 5 else None


def working_folders(setup: bytes) -> dict[str, str]:
    got = get(setup, F_WORKING)
    if not got or got[0] != 2 or len(got[1]) < 8:
        return {}
    body = got[1][8:]
    out = {}
    for name, number in (("gallery", 37), ("cache", 39), ("proxy", F_PROXY)):
        f = get(body, number)
        if f and f[0] == 2:
            out[name] = f[1].decode("utf-8", "replace")
    return out


def patch(setup: bytes, fps: float | None = None, proxy: str | None = None) -> bytes:
    if fps is not None:
        setup = put(setup, F_PLAYBACK, 5, struct.pack("<f", float(fps)))
    if proxy is not None:
        found = [x for x in fields(setup) if x[0] == F_WORKING]
        if not found:
            raise TemplateError("no working folders in the project settings")
        f, w, s, e = found[0]
        inner = _payload(setup, s, e, w)
        header, body = inner[:8], inner[8:]
        if struct.unpack(">I", header[4:8])[0] != len(body):
            raise TemplateError("unexpected working-folder header")
        body = put(body, F_PROXY, 2, proxy.encode("utf-8"))
        setup = put(setup, F_WORKING, 2, header[:4] + struct.pack(">I", len(body)) + body)
    return setup


def write(base_drp: Path, target: Path, fps: float | None = None, proxy: str | None = None) -> Path:
    """A copy of base_drp with the playback frame rate and proxy location set."""
    with zipfile.ZipFile(base_drp) as z:
        files = {n: z.read(n) for n in z.namelist()}
    xml = files["project.xml"].decode("utf-8")
    m = _setup_blob(xml)
    blob = bytes.fromhex(m.group(1))
    i, inner, setup = _unpack(blob)
    packed = b"\x81" + zstd.compress(patch(setup, fps, proxy))
    blob = blob[:i - 12] + struct.pack(">I", len(packed) + 8) + blob[i - 8:i - 4] + \
        struct.pack(">I", len(packed)) + packed + blob[i + inner:]
    files["project.xml"] = (xml[:m.start(1)] + blob.hex() + xml[m.end(1):]).encode("utf-8")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
        for name, data in files.items():
            out.writestr(name, data)
    tmp.replace(target)
    return target


# ------------------------------------------------------------------------------------------ with Resolve

def base_template(resolve) -> Path:
    """An empty project of this Resolve version, exported once (and checked that davigen can read it)."""
    version = re.sub(r"[^0-9.]", "", resolve.GetVersionString() or "0")
    target = TEMPLATES / f"base_{version}.drp"
    if target.exists():
        return target
    pm = resolve.GetProjectManager()
    current = pm.GetCurrentProject()
    previous = current.GetName() if current is not None else ""   # the object is stale once another project loads
    if current is not None:
        pm.SaveProject()
    open_pm_folder(pm, TEMPLATE_FOLDER)
    if BASE_NAME in (pm.GetProjectListInCurrentFolder() or []):
        pm.DeleteProject(BASE_NAME)
    project = pm.CreateProject(BASE_NAME)
    if project is None:
        raise ResolveError("Resolve couldn't create the empty project for the template")
    TEMPLATES.mkdir(parents=True, exist_ok=True)
    tmp = TEMPLATES / f"base_{version}.export.drp"
    try:
        pm.SaveProject()
        if not pm.ExportProject(BASE_NAME, str(tmp), False):
            raise ResolveError("Resolve couldn't export the template project")
    finally:
        pm.CloseProject(project)
        pm.DeleteProject(BASE_NAME)
        pm.GotoRootFolder()                          # leave no trace in the Project Manager
        pm.DeleteFolder(TEMPLATE_FOLDER)
        if previous:
            _reopen(pm, previous)
    read_settings(tmp)                          # raises TemplateError if the layout isn't the known one
    tmp.replace(target)
    return target


def _reopen(pm, name: str) -> None:
    """Best effort: the project that was open before (the new project is loaded right after anyway)."""
    from .resolve_api import find_project  # noqa: PLC0415
    try:
        from .project import PM_FOLDER  # noqa: PLC0415
        if find_project(pm, name, PM_FOLDER):
            pm.LoadProject(name)
    except Exception:  # noqa: BLE001
        pass


def create_project(resolve, name: str, pm_folder: str, fps: float, proxy: Path) -> tuple[object, list[str]]:
    """Create and load the project `name` in the Project Manager folder, with playback frame rate and proxy
    location already set. Falls back to an ordinary CreateProject (and says so) if the template doesn't work."""
    pm = resolve.GetProjectManager()
    notes: list[str] = []
    template = None
    try:
        base = base_template(resolve)
        template = write(base, TEMPLATES / "last_new_project.drp", fps=fps, proxy=str(proxy))
    except (TemplateError, ResolveError, OSError, zipfile.BadZipFile) as e:
        notes.append(f"Project template not usable ({e}) – playback frame rate and proxy folder need setting by hand")
    open_pm_folder(pm, pm_folder)
    if name in (pm.GetProjectListInCurrentFolder() or []):
        raise ResolveError(f"A project called '{name}' already exists in {pm_folder}")
    project = None
    if template is not None:
        if pm.ImportProject(str(template), name):
            project = pm.LoadProject(name)
        if project is None:
            notes.append("Resolve didn't import the project template – created an empty project instead")
    if project is None:
        project = pm.CreateProject(name)
    if project is None:
        raise ResolveError(f"Resolve couldn't create the project '{name}'")
    return project, notes
