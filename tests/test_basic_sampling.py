import os
import struct
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

from davigen.basic import sampling  # noqa: E402

SAMPLES = {"min": 5, "per_seconds": 4, "max": 12}


def write_tiff(path: Path, img: np.ndarray, endian: str = "<") -> Path:
    """A minimal uncompressed RGB TIFF, written by hand (no tifffile needed)."""
    h, w, _ = img.shape
    bits = 16 if img.dtype == np.uint16 else 8
    pixels = img.astype(np.dtype(endian + ("u2" if bits == 16 else "u1"))).tobytes()
    entries = [(256, 4, 1, w), (257, 4, 1, h), (258, 3, 1, bits), (259, 3, 1, 1), (262, 3, 1, 2),
               (273, 4, 1, 0), (277, 3, 1, 3), (278, 4, 1, h), (279, 4, 1, len(pixels))]
    ifd_size = 2 + 12 * len(entries) + 4
    data_offset = 8 + ifd_size
    out = (b"II" if endian == "<" else b"MM") + struct.pack(endian + "HI", 42, 8)
    out += struct.pack(endian + "H", len(entries))
    for tag, typ, n, value in entries:
        value = data_offset if tag == 273 else value
        if typ == 3:
            out += struct.pack(endian + "HHIHH", tag, typ, n, value, 0)
        else:
            out += struct.pack(endian + "HHII", tag, typ, n, value)
    out += struct.pack(endian + "I", 0) + pixels
    path.write_bytes(out)
    return path


def test_choose_frames_counts_and_spread():
    frames = sampling.choose_frames(100, 250, 25.0, SAMPLES)       # 10 s: 5 + 2
    assert len(frames) == 7
    assert frames[0] == 105 and frames[-1] == 344                  # away from the ends
    assert frames == sorted(set(frames))
    assert len(sampling.choose_frames(0, 25 * 120, 25.0, SAMPLES)) == 12       # capped
    assert sampling.choose_frames(0, 3, 25.0, SAMPLES) == [0, 1, 2]           # short shot: every frame once
    assert sampling.choose_frames(0, 1, 25.0, SAMPLES) == [0]
    assert sampling.choose_frames(0, 0, 25.0, SAMPLES) == []


def test_read_tiff_both_byte_orders(tmp_path):
    img = np.random.default_rng(0).integers(0, 65535, (6, 8, 3)).astype("uint16")
    for endian in "<>":
        f = write_tiff(tmp_path / f"x{endian == '<'}.tif", img, endian)
        assert np.array_equal(sampling.read_tiff(f), img)
    eight = write_tiff(tmp_path / "e.tif", (img >> 8).astype("uint8"))
    assert sampling.read_tiff(eight).max() <= 65535 and sampling.read_tiff(eight).dtype == np.uint16


def test_thumbnail_is_area_average():
    img = np.zeros((20, 40, 3), "uint16")
    img[:, :20] = 1000
    img[:, 20:] = 3000
    t = sampling.thumbnail(img, width=4)
    assert t.shape == (2, 4, 3)
    assert (t[:, :2] == 1000).all() and (t[:, 2:] == 3000).all()


def test_cache_roundtrip_and_invalidation(tmp_path):
    clip = tmp_path / "P1000070.MOV"
    clip.write_bytes(b"x" * 100)
    cache = sampling.Cache(tmp_path / "ANALYSIS")
    assert cache.load(str(clip)) == {}
    thumb = np.full((4, 6, 3), 1234, "uint16")
    cache.save(str(clip), {10: thumb})
    cache.save(str(clip), {20: thumb + 1})                        # merged, not replaced
    got = cache.load(str(clip))
    assert set(got) == {10, 20} and np.array_equal(got[10], thumb)
    st = clip.stat()
    os.utime(clip, (st.st_atime, st.st_mtime + 10))               # file changed: cache is stale
    assert cache.load(str(clip)) == {}


class FakeTimeline:
    def __init__(self, name):
        self.name, self.items, self.settings = name, [], {"timelineResolutionWidth": "3240",
                                                           "timelineResolutionHeight": "2160"}

    def GetName(self):
        return self.name

    def GetSetting(self, k):
        return self.settings.get(k)

    def SetSetting(self, k, v):
        self.settings[k] = v
        return True


class FakeProject:
    def __init__(self, fail_render=False):
        self.timelines = [FakeTimeline("TL_01_ASSEMBLY")]
        self.current = self.timelines[0]
        self.jobs, self.loaded_preset, self.fail_render = [], "", fail_render
        outer = self

        class MP:
            def CreateEmptyTimeline(self, name):
                t = FakeTimeline(name)
                outer.timelines.append(t)
                return t

            def DeleteTimelines(self, tls):
                outer.timelines = [t for t in outer.timelines if t not in tls]
                return True

            def AppendToTimeline(self, infos):
                assert all(i["endFrame"] == i["startFrame"] + 1 for i in infos)
                outer.current.items += infos
                return list(infos)
        self.mp = MP()

    def GetMediaPool(self):
        return self.mp

    def GetTimelineCount(self):
        return len(self.timelines)

    def GetTimelineByIndex(self, i):
        return self.timelines[i - 1]

    def GetCurrentTimeline(self):
        return self.current

    def SetCurrentTimeline(self, t):
        self.current = t
        return True

    def SetCurrentRenderFormatAndCodec(self, f, c):
        return (f, c) == ("tif", "RGB16")

    def SetRenderSettings(self, s):
        self.settings = s
        return True

    def AddRenderJob(self):
        self.jobs.append("job")
        return "job"

    def StartRendering(self, jobs):
        if self.fail_render:
            return True
        target = Path(self.settings["TargetDir"])
        for n, info in enumerate(self.current.items):
            value = 1000 + info["startFrame"]            # each frame renders a distinct grey
            img = np.full((self.settings["FormatHeight"], self.settings["FormatWidth"], 3), value, "uint16")
            write_tiff(target / f"a{90000 + n:08d}.tif", img)
        return True

    def IsRenderingInProgress(self):
        return False

    def GetRenderJobStatus(self, job):
        return {"JobStatus": "Complete", "CompletionPercentage": 100}

    def DeleteRenderJob(self, job):
        self.jobs.remove(job)
        return True

    def LoadRenderPreset(self, name):
        self.loaded_preset = name
        return True


def test_render_maps_frames_and_cleans_up():
    proj = FakeProject()
    reqs = [sampling.Request(("a.mov", f), object(), f) for f in (10, 50, 90)]
    out = sampling.render(None, proj, reqs, 96, restore_preset="DAVIGEN_MASTER")
    assert [int(out[r.key][0, 0, 0]) for r in reqs] == [1010, 1050, 1090]
    assert out[reqs[0].key].shape == (64, 96, 3)
    assert [t.GetName() for t in proj.timelines] == ["TL_01_ASSEMBLY"]    # scratch timeline gone
    assert proj.current.GetName() == "TL_01_ASSEMBLY" and proj.jobs == []
    assert proj.loaded_preset == "DAVIGEN_MASTER"


def test_render_failure_still_cleans_up():
    proj = FakeProject(fail_render=True)
    with pytest.raises(sampling.ResolveError):
        sampling.render(None, proj, [sampling.Request(("a", 1), object(), 1)], 96)
    assert [t.GetName() for t in proj.timelines] == ["TL_01_ASSEMBLY"] and proj.jobs == []
