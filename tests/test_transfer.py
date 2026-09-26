"""Crash-safety of moving footage: whatever happens, files end up where they were."""

import os
from pathlib import Path

import pytest

from davigen import transfer
from davigen.transfer import COPY, MOVE, Journal


@pytest.fixture(autouse=True)
def isolated_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(transfer, "PENDING", tmp_path / "pending.json")


def make_files(folder: Path, n: int = 5) -> list[Path]:
    folder.mkdir(parents=True)
    files = []
    for i in range(n):
        f = folder / f"C{i:04d}.MP4"
        f.write_bytes(os.urandom(1000 + i))
        files.append(f)
    (folder / "C0001M01.XML").write_text("<sidecar/>")
    return files


def snapshot(folder: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in folder.rglob("*") if p.is_file()}


def test_move_then_commit(tmp_path):
    src = tmp_path / "card"
    files = make_files(src)
    before = snapshot(src)
    j = Journal(tmp_path / "project" / "journal.json", MOVE)
    j.begin()
    for f in files:
        j.transfer(f, tmp_path / "project" / "media" / f.name)
    j.commit()
    assert not list(src.glob("*.MP4"))
    assert snapshot(tmp_path / "project" / "media") == before       # incl. the sidecar
    assert transfer.recover_pending() == []


def test_crash_mid_move_is_rolled_back_on_next_start(tmp_path, monkeypatch):
    src = tmp_path / "card"
    files = make_files(src)
    before = snapshot(src)
    j = Journal(tmp_path / "project" / "journal.json", MOVE)
    j.begin()
    for f in files[:3]:
        j.transfer(f, tmp_path / "project" / "media" / f.name)
    # the 4th file dies half-way: journal says "working", a .part file is lying around
    boom = RuntimeError("power cut")
    monkeypatch.setattr(transfer.os, "rename", lambda *a: (_ for _ in ()).throw(boom))
    monkeypatch.setattr(transfer, "same_volume", lambda *a: False)
    orig_copy = transfer._copy_hashed

    def half_copy(s, d):
        d.with_name(d.name + ".part").write_bytes(s.read_bytes()[:10])
        raise boom
    monkeypatch.setattr(transfer, "_copy_hashed", half_copy)
    with pytest.raises(RuntimeError):
        j.transfer(files[3], tmp_path / "project" / "media" / files[3].name)
    monkeypatch.undo()
    monkeypatch.setattr(transfer, "PENDING", tmp_path / "pending.json")
    assert orig_copy  # (restore real functions for the recovery)

    reports = transfer.recover_pending()                              # "next start"
    assert reports and not reports[0]["problems"]
    assert snapshot(src) == before
    assert not [p for p in (tmp_path / "project" / "media").rglob("*") if p.is_file()]
    assert transfer.recover_pending() == []                           # nothing pending anymore


def test_cross_volume_move_rollback_restores_deleted_originals(tmp_path, monkeypatch):
    monkeypatch.setattr(transfer, "same_volume", lambda *a: False)   # force copy + delete path
    src = tmp_path / "card"
    files = make_files(src, 3)
    before = snapshot(src)
    j = Journal(tmp_path / "project" / "journal.json", MOVE)
    j.begin()
    for f in files:
        j.transfer(f, tmp_path / "project" / "media" / f.name)
    assert not list(src.glob("*.MP4"))                                # originals deleted after verify
    result = j.rollback()
    assert result["problems"] == [] and snapshot(src) == before


def test_copy_rollback_only_removes_copies(tmp_path):
    src = tmp_path / "card"
    files = make_files(src, 2)
    before = snapshot(src)
    j = Journal(tmp_path / "project" / "journal.json", COPY)
    j.begin()
    for f in files:
        j.transfer(f, tmp_path / "project" / "media" / f.name)
    j.rollback()
    assert snapshot(src) == before
    assert not [p for p in (tmp_path / "project" / "media").rglob("*") if p.is_file()]


def test_never_overwrites(tmp_path):
    src = tmp_path / "card"
    files = make_files(src, 1)
    dst = tmp_path / "project" / files[0].name
    dst.parent.mkdir(parents=True)
    dst.write_bytes(b"existing")
    j = Journal(tmp_path / "project" / "journal.json", MOVE)
    j.begin()
    with pytest.raises(transfer.TransferError):
        j.transfer(files[0], dst)
    assert dst.read_bytes() == b"existing" and files[0].exists()
