"""Bring files into the project folder – move (default), copy, link, or leave in place – crash-safe.

Every step is written to a journal on disk *before* it happens. If davigen, Resolve or the Mac
stops half-way, the next start finds the unfinished journal and rolls everything back: each file
ends up where it was before, nothing is left at the destination.

Move on the same volume = rename (instant, no extra space). Move across volumes = copy one file,
verify it by checksum, then delete the original – extra space needed is at most one file.
Link = a symbolic link at the file's place in the project, pointing to the original (e.g. on the card): the folder
shows every file, Resolve keeps the path inside the project (checked 2026-09-30), and the files can be collected
later without Resolve noticing.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

from .config import DATA_DIR

MOVE, COPY, LINK, LEAVE = "move", "copy", "link", "leave"
MODES = (MOVE, COPY, LINK, LEAVE)
CHUNK = 8 * 1024 * 1024
PENDING = DATA_DIR / "pending_transfers.json"      # pointers to journals that are not committed yet
SIDECAR_SUFFIXES = ("M01.XML", "M01.xml", ".SRT", ".srt", ".LRF", ".lrf", ".XML", ".xml", ".THM", ".thm")


class TransferError(RuntimeError):
    pass


# ------------------------------------------------------------------------------ helpers

def _hash_file(path: Path) -> str:
    h = hashlib.blake2b(digest_size=16)
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _copy_hashed(src: Path, dst: Path) -> str:
    """Copy src → dst.part while hashing, fsync, verify by re-reading, rename to dst. Returns hash."""
    part = dst.with_name(dst.name + ".part")
    h = hashlib.blake2b(digest_size=16)
    with open(src, "rb") as fi, open(part, "wb") as fo:
        while chunk := fi.read(CHUNK):
            h.update(chunk)
            fo.write(chunk)
        fo.flush()
        os.fsync(fo.fileno())
    digest = h.hexdigest()
    if _hash_file(part) != digest:
        part.unlink(missing_ok=True)
        raise TransferError(f"Checksum mismatch while copying {src.name}")
    shutil.copystat(src, part)
    os.replace(part, dst)
    return digest


def same_volume(a: Path, b: Path) -> bool:
    b = b if b.exists() else next((p for p in b.parents if p.exists()), b)
    return os.stat(a).st_dev == os.stat(b).st_dev


def sidecars(src: Path) -> list[Path]:
    """Files that belong to a clip (Sony XML, DJI SRT/LRF …). Deduplicated by inode, because macOS
    volumes are usually case-insensitive ('M01.XML' and 'M01.xml' are the same file)."""
    seen = {(src.stat().st_dev, src.stat().st_ino)}
    out = []
    for cand in (src.with_name(src.stem + suf) for suf in SIDECAR_SUFFIXES):
        if cand.exists():
            key = (cand.stat().st_dev, cand.stat().st_ino)
            if key not in seen:
                seen.add(key)
                out.append(cand)
    return out


def _write_json(path: Path, data) -> None:
    """Atomic write + fsync, so a journal is never half-written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _pending() -> list[str]:
    return json.loads(PENDING.read_text(encoding="utf-8")) if PENDING.exists() else []


def _set_pending(paths: list[str]) -> None:
    _write_json(PENDING, sorted(set(paths)))


# ------------------------------------------------------------------------------ journal

class Journal:
    """One transfer run. States per file: planned → working → placed → done (move) / done (copy)."""

    def __init__(self, path: Path, mode: str):
        self.path, self.mode = path, mode
        self.entries: list[dict] = []
        self.committed = False

    @classmethod
    def load(cls, path: Path) -> "Journal":
        data = json.loads(path.read_text(encoding="utf-8"))
        j = cls(path, data["mode"])
        j.entries, j.committed = data["entries"], data.get("committed", False)
        return j

    def save(self) -> None:
        _write_json(self.path, {"mode": self.mode, "committed": self.committed, "entries": self.entries})

    # -- running ---------------------------------------------------------------------
    def begin(self) -> None:
        self.save()
        _set_pending([*_pending(), str(self.path)])

    def transfer(self, src: Path, dst: Path) -> Path:
        """Move/copy one file (and its sidecars). Returns the path the clip now lives at."""
        if self.mode == LEAVE:
            return src
        dst.parent.mkdir(parents=True, exist_ok=True)
        for s, d in [(src, dst), *[(sc, dst.with_name(dst.stem + sc.name[len(src.stem):])) for sc in sidecars(src)]]:
            self._one(s, d)
        return dst

    def _one(self, src: Path, dst: Path) -> None:
        if dst.exists() or dst.is_symlink():
            raise TransferError(f"{dst} already exists – not overwriting")
        entry = {"src": str(src), "dst": str(dst), "state": "working",
                 "rename": self.mode == MOVE and same_volume(src, dst.parent), "size": src.stat().st_size}
        if self.mode == LINK:
            entry["link"] = True
        self.entries.append(entry)
        self.save()                                   # intent is on disk before anything happens
        if self.mode == LINK:
            os.symlink(src.resolve(), dst)
            entry["state"] = "done"
        elif entry["rename"]:
            os.rename(src, dst)
            entry["state"] = "done"
        else:
            entry["hash"] = _copy_hashed(src, dst)
            entry["state"] = "placed"                 # verified copy exists, original untouched
            self.save()
            if self.mode == MOVE:
                os.remove(src)
            entry["state"] = "done"
        self.save()

    def commit(self) -> None:
        self.committed = True
        self.save()
        _set_pending([p for p in _pending() if p != str(self.path)])

    # -- undo ------------------------------------------------------------------------
    def rollback(self) -> dict:
        """Undo every step, newest first. Safe to run repeatedly (idempotent)."""
        restored = removed = 0
        problems: list[str] = []
        for e in reversed(self.entries):
            if e["state"] == "rolled_back":
                continue
            src, dst = Path(e["src"]), Path(e["dst"])
            try:
                if e.get("link"):
                    if dst.is_symlink():                  # only ever the link davigen made, never a real file
                        dst.unlink()
                        removed += 1
                elif self.mode == COPY or src.exists():
                    # original is still in place: just remove whatever was created at the destination
                    for p in (dst, dst.with_name(dst.name + ".part")):
                        if p.exists():
                            p.unlink()
                            removed += 1
                elif dst.exists():
                    # original is gone, the (verified) file sits at the destination: bring it back
                    src.parent.mkdir(parents=True, exist_ok=True)
                    if e.get("rename") or same_volume(dst, src.parent):
                        os.rename(dst, src)
                    else:
                        digest = _copy_hashed(dst, src)
                        if e.get("hash") and digest != e["hash"]:
                            raise TransferError(f"{src.name}: checksum differs from the original")
                        os.remove(dst)
                    restored += 1
                else:
                    problems.append(f"{src.name}: neither original nor copy found")
                    continue
                e["state"] = "rolled_back"
                self.save()
            except OSError as err:
                problems.append(f"{src.name}: {err}")
        if not problems:
            self.commit()                              # nothing left to undo
        return {"restored": restored, "removed": removed, "problems": problems}


def recover_pending() -> list[dict]:
    """Roll back every transfer a crash left unfinished. Called when davigen starts."""
    reports = []
    for p in _pending():
        path = Path(p)
        if not path.exists():
            _set_pending([x for x in _pending() if x != p])
            continue
        journal = Journal.load(path)
        if journal.committed:
            _set_pending([x for x in _pending() if x != p])
            continue
        result = journal.rollback()
        reports.append({"journal": p, **result, "files": len(journal.entries)})
    return reports


def space_needed(files: list[Path], dest: Path, mode: str) -> int:
    """Bytes the destination volume must be able to take."""
    if mode in (LEAVE, LINK):
        return 0
    return sum(f.stat().st_size + sum(s.stat().st_size for s in sidecars(f))
               for f in files if not (mode == MOVE and same_volume(f, dest)))
