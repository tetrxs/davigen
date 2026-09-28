"""Basic Correction · step 01: check the Resolve API on the real thing.

Run from Resolve: copy to …/Fusion/Scripts/Utility/, then Workspace → Scripts → spike_basic_correction.
Watch Workspace → Console. The report is also written to ~/Movies/davigen_spike/report.txt.

Touches only its own timelines (ZZ_DAVIGEN_SPIKE*), which it deletes on the next run. The spike timeline is
kept so the grades can be inspected on the Color page. Render settings on the Deliver page are changed for
this project (the API can't read them back); the render job is removed again.
See docs/plans/basic-correction/01_SPIKE.md.

Items on ZZ_DAVIGEN_SPIKE (all the same source frame, then TIMING_FRAMES more for the render timing):
  1 base (no grade)   2 offset +1 stop   3 slope 1.2   4 saturation 0.5
  5 version test, user version active   6 version test, DAVIGEN_AUTO active   7 offset +1 stop on node 2
"""

from __future__ import annotations

import struct
import time
import traceback
from pathlib import Path

OUT = Path.home() / "Movies" / "davigen_spike"
SPIKE_TL = "ZZ_DAVIGEN_SPIKE"
RANGE_TL = "ZZ_DAVIGEN_SPIKE_RANGE"
CLIP_TEMPLATE = Path.home() / "Applications" / "davigen" / "templates" / "drx" / "CLIP_STRUCTURE.drx"
STOP = 0.07329248
SAME_FRAME_ITEMS = 7
TIMING_FRAMES = 20
ANALYSIS_WIDTH = 480
MARKER_DATA = "davigen-basic"
AUTO = "DAVIGEN_AUTO"

IDENTITY = {"Slope": "1 1 1", "Offset": "0 0 0", "Power": "1 1 1", "Saturation": "1"}
PLUS_STOP = {**IDENTITY, "Offset": f"{STOP:.6f} {STOP:.6f} {STOP:.6f}"}
MINUS_STOP = {**IDENTITY, "Offset": f"{-STOP:.6f} {-STOP:.6f} {-STOP:.6f}"}

lines: list[str] = []


def log(msg: str = "") -> None:
    lines.append(msg)
    try:
        print(msg)
    except UnicodeEncodeError:      # fuscript may run Python with an ASCII locale
        print(msg.encode("ascii", "replace").decode("ascii"))


def check(number: int, title: str):
    """Decorator: run one check, log OK/FAIL, never let it stop the others."""
    def wrap(fn):
        def run(*args, **kwargs):
            log(f"\n[{number}] {title}")
            try:
                result = fn(*args, **kwargs)
                log("    -> OK" if result is not False else "    -> FAIL")
                return result
            except Exception as e:  # noqa: BLE001 - a spike reports everything
                log(f"    -> FAIL: {type(e).__name__}: {e}")
                for line in traceback.format_exc().splitlines()[-4:]:
                    log(f"       {line}")
                return None
        return run
    return wrap


# --------------------------------------------------------------------------- helpers

def find_timeline(project, name: str):
    for i in range(1, project.GetTimelineCount() + 1):
        tl = project.GetTimelineByIndex(i)
        if tl and tl.GetName() == name:
            return tl
    return None


def delete_timeline(project, name: str) -> None:
    tl = find_timeline(project, name)
    if tl is not None:
        project.GetMediaPool().DeleteTimelines([tl])


def video_items(timeline) -> list:
    items = []
    for idx in range(1, timeline.GetTrackCount("video") + 1):
        items += timeline.GetItemListInTrack("video", idx) or []
    return items


def append(media_pool, mpi, start: int, end: int) -> list:
    info = {"mediaPoolItem": mpi, "startFrame": start, "endFrame": end}
    result = media_pool.AppendToTimeline([{**info, "mediaType": 1}])     # video only, if supported
    if not result:
        result = media_pool.AppendToTimeline([info])
    return list(result or [])


def labels(graph) -> list:
    return [graph.GetNodeLabel(i) for i in range(1, graph.GetNumNodes() + 1)]


def structure(item) -> bool:
    return bool(CLIP_TEMPLATE.exists() and item.GetNodeGraph().ApplyGradeFromDRX(str(CLIP_TEMPLATE), 0))


class Cdl:
    """SetCDL, retried once on the Color page if it fails elsewhere (logs which page worked)."""

    def __init__(self, resolve):
        self.resolve = resolve
        self.page = resolve.GetCurrentPage() if hasattr(resolve, "GetCurrentPage") else "?"

    def set(self, item, node: int, cdl: dict) -> bool:
        result = item.SetCDL({"NodeIndex": str(node), **cdl})
        if not result and self.resolve.GetCurrentPage() != "color":
            log(f"    SetCDL on page '{self.resolve.GetCurrentPage()}' -> {result}; retrying on the Color page")
            self.resolve.OpenPage("color")
            result = item.SetCDL({"NodeIndex": str(node), **cdl})
        return bool(result)


def read_tiff(path: Path):
    """Minimal reader for uncompressed RGB TIFFs (8/16 bit, strips). Returns (float array 0–1, bits)."""
    import numpy as np  # noqa: PLC0415

    try:
        import tifffile  # noqa: PLC0415
        data = tifffile.imread(str(path))
        bits = 16 if data.dtype == np.uint16 else 8
        return data[..., :3].astype("float64") / (65535.0 if bits == 16 else 255.0), bits
    except ImportError:
        pass
    raw = path.read_bytes()
    endian = "<" if raw[:2] == b"II" else ">"
    (ifd,) = struct.unpack(endian + "I", raw[4:8])
    (count,) = struct.unpack(endian + "H", raw[ifd:ifd + 2])
    tags = {}
    for i in range(count):
        entry = raw[ifd + 2 + 12 * i: ifd + 14 + 12 * i]
        tag, typ, n = struct.unpack(endian + "HHI", entry[:8])
        size = {3: 2, 4: 4}.get(typ, 1)
        fmt = {3: "H", 4: "I"}.get(typ, "B")
        if n * size <= 4:
            vals = struct.unpack(endian + fmt * n, entry[8:8 + n * size])
        else:
            (off,) = struct.unpack(endian + "I", entry[8:12])
            vals = struct.unpack(endian + fmt * n, raw[off:off + n * size])
        tags[tag] = vals
    width, height = tags[256][0], tags[257][0]
    bits = tags[258][0]
    compression = tags.get(259, (1,))[0]
    spp = tags.get(277, (1,))[0]
    if compression != 1:
        raise ValueError(f"TIFF compression {compression} - install tifffile to read it")
    data = b"".join(raw[o:o + c] for o, c in zip(tags[273], tags[279]))
    dtype = np.dtype(endian + ("u2" if bits == 16 else "u1"))
    img = np.frombuffer(data, dtype=dtype)[:width * height * spp].reshape(height, width, spp)[..., :3]
    return img.astype("float64") / (65535.0 if bits == 16 else 255.0), bits


def pick_codec(codecs: dict) -> str:
    """Prefer uncompressed 16-bit RGB: our fallback reader can't decompress."""
    def score(item):
        desc, codec = item
        text = f"{desc} {codec}".lower()
        return ("16" in text) * 2 + ("lzw" not in text and "zip" not in text and "compress" not in text) \
            - ("rgba" in text or "alpha" in text)
    return max(codecs.items(), key=score)[1] if codecs else ""


# ---------------------------------------------------------------------------- checks

@check(1, "Resolve version and project colour settings")
def check_version(resolve, project):
    log(f"    product: {resolve.GetProductName()}  version: {resolve.GetVersionString()}")
    for key in ("colorScienceMode", "colorSpaceTimeline", "colorSpaceOutput", "colorSpaceInput",
                "timelineWorkingLuminanceMode", "videoDataLevels", "videoMonitorUse444SDI",
                "timelineResolutionWidth", "timelineResolutionHeight", "timelineFrameRate"):
        log(f"    {key} = {project.GetSetting(key)!r}")


@check(2, "Source range of a timeline item (GetSourceStartFrame / GetSourceEndFrame)")
def check_source_range(item):
    log(f"    item: {item.GetName()}  start/end on timeline: {item.GetStart()}-{item.GetEnd()}  "
        f"duration {item.GetDuration()}")
    for name in ("GetSourceStartFrame", "GetSourceEndFrame", "GetSourceStartTime", "GetSourceEndTime",
                 "GetLeftOffset", "GetRightOffset"):
        try:
            log(f"    {name}() = {getattr(item, name)()}")
        except Exception as e:  # noqa: BLE001 - missing methods show up as None or an error
            log(f"    {name}: {type(e).__name__}: {e}")
    try:
        return item.GetSourceStartFrame() is not None and item.GetSourceEndFrame() is not None
    except Exception:  # noqa: BLE001
        return False


@check(3, "One-frame snippets via AppendToTimeline(startFrame, endFrame)")
def check_snippets(project, mpi, frame: int):
    mp = project.GetMediaPool()
    delete_timeline(project, RANGE_TL)
    tl = mp.CreateEmptyTimeline(RANGE_TL)
    project.SetCurrentTimeline(tl)
    same = append(mp, mpi, frame, frame)
    plus = append(mp, mpi, frame, frame + 1)
    d_same = same[0].GetDuration() if same else None
    d_plus = plus[0].GetDuration() if plus else None
    log(f"    (f, f) -> {d_same} frame(s);  (f, f+1) -> {d_plus} frame(s)")
    for label, got in (("(f, f)", same), ("(f, f+1)", plus)):
        if got:
            try:
                log(f"    {label}: source {got[0].GetSourceStartFrame()}-{got[0].GetSourceEndFrame()} "
                    f"(asked from {frame})")
            except Exception:  # noqa: BLE001
                pass
    mp.DeleteTimelines([tl])
    if d_same == 1:
        log("    endFrame is inclusive")
        return (0, True)
    if d_plus == 1:
        log("    endFrame is exclusive")
        return (1, True)
    log("    endFrame semantics unclear")
    return False


@check(4, "Node graph + CLIP_STRUCTURE.drx, labels")
def check_structure(items):
    if not CLIP_TEMPLATE.exists():
        log(f"    {CLIP_TEMPLATE} not found - node structure not applied")
    for n, item in enumerate(items, start=2):
        before = item.GetNodeGraph().GetNumNodes()
        applied = structure(item)
        graph = item.GetNodeGraph()
        log(f"    item {n}: {before} node(s) before, ApplyGradeFromDRX -> {applied}, "
            f"now {graph.GetNumNodes()} nodes {labels(graph)}")
    return all(i.GetNodeGraph().GetNumNodes() >= 4 for i in items)


@check(5, "SetCDL (node 1 on items 2-4, node 2 on item 7)")
def check_setcdl(cdl: Cdl, items, node2_item):
    ok = True
    cdls = [("item 2, offset +1 stop", PLUS_STOP),
            ("item 3, slope 1.2", {**IDENTITY, "Slope": "1.2 1.2 1.2"}),
            ("item 4, saturation 0.5", {**IDENTITY, "Saturation": "0.5"})]
    for item, (label, values) in zip(items, cdls):
        result = cdl.set(item, 1, values)
        log(f"    {label}: SetCDL -> {result}   values {values}")
        ok = ok and result
    result = cdl.set(node2_item, 2, PLUS_STOP)
    log(f"    item 7, node 2 ({node2_item.GetNodeGraph().GetNodeLabel(2)}), offset +1 stop: SetCDL -> {result}")
    log("    LOOK: on the Color page, open items 2-4 on ZZ_DAVIGEN_SPIKE and note what")
    log("          Primaries -> Offset / Gain / Gamma / Sat show for the values above (node 1).")
    return ok and result


@check(6, "Grade versions: AddVersion / GetVersionNameList / LoadVersionByName / SetCDL inside it")
def check_versions(cdl: Cdl, keep_user, keep_auto):
    """Both items: +1 stop in the user's version, then DAVIGEN_AUTO gets -1 stop.
    keep_user ends on the user's version (render must show +1 stop: user grade untouched),
    keep_auto ends on DAVIGEN_AUTO (render must show -1 stop: the write went into the new version)."""
    ok = True
    for n, item, final in ((5, keep_user, "user"), (6, keep_auto, AUTO)):
        log(f"    item {n}:")
        cdl.set(item, 1, PLUS_STOP)
        before = item.GetVersionNameList(0) or []
        log(f"      local versions before: {before}   current: {item.GetCurrentVersion()}")
        added = item.AddVersion(AUTO, 0)
        log(f"      AddVersion('{AUTO}', 0) -> {added}")
        log(f"      local versions after:  {item.GetVersionNameList(0)}   current: {item.GetCurrentVersion()}")
        graph = item.GetNodeGraph()
        log(f"      [7] right after AddVersion: {graph.GetNumNodes()} nodes {labels(graph)}")
        loaded = item.LoadVersionByName(AUTO, 0)
        graph = item.GetNodeGraph()
        log(f"      LoadVersionByName('{AUTO}', 0) -> {loaded}   current: {item.GetCurrentVersion()}   "
            f"{graph.GetNumNodes()} nodes {labels(graph)}")
        wrote = cdl.set(item, 1, MINUS_STOP)
        log(f"      SetCDL -1 stop inside {AUTO} -> {wrote}")
        ok = ok and bool(added) and bool(loaded) and wrote
        if final == "user" and before:
            back = item.LoadVersionByName(before[0], 0)
            graph = item.GetNodeGraph()
            log(f"      LoadVersionByName('{before[0]}', 0) -> {back}   current: {item.GetCurrentVersion()}   "
                f"{graph.GetNumNodes()} nodes {labels(graph)}")
        elif final == "user":
            log("      no named user version to go back to")
    log(f"    LOOK: item 5 and 6, Color page -> right-click the clip -> Local Versions: is {AUTO} a copy of")
    log("          the previous grade (6 labelled nodes, +1 stop) or empty?")
    return ok


@check(8, "Markers with custom data")
def check_markers(item):
    try:
        added = item.AddMarker(0, "Yellow", "davigen: test", "spike marker", 1, MARKER_DATA)
    except TypeError:
        added = item.AddMarker(0, "Yellow", "davigen: test", "spike marker", 1)
        log("    AddMarker doesn't take custom data")
    log(f"    AddMarker -> {added}   markers: {item.GetMarkers()}")
    if hasattr(item, "GetMarkerByCustomData"):
        log(f"    GetMarkerByCustomData -> {item.GetMarkerByCustomData(MARKER_DATA)}")
    user = item.AddMarker(1, "Blue", "user marker", "must survive", 1)
    log(f"    second marker without custom data -> {user}")
    if hasattr(item, "DeleteMarkerByCustomData"):
        log(f"    DeleteMarkerByCustomData -> {item.DeleteMarkerByCustomData(MARKER_DATA)}   "
            f"markers left: {item.GetMarkers()}")
    else:
        log("    DeleteMarkerByCustomData: missing")
    return bool(added)


@check(9, "Analysis render: 16-bit TIFF sequence, small size, timed")
def check_render(project, timeline, frames: int):
    project.SetCurrentTimeline(timeline)
    codecs = project.GetRenderCodecs("tif") or {}
    log(f"    TIFF codecs: {codecs}")
    codec = pick_codec(codecs)
    log(f"    SetCurrentRenderFormatAndCodec('tif', '{codec}') -> "
        f"{project.SetCurrentRenderFormatAndCodec('tif', codec)}")
    w = int(timeline.GetSetting("timelineResolutionWidth") or 1920)
    h = int(timeline.GetSetting("timelineResolutionHeight") or 1080)
    height = round(ANALYSIS_WIDTH * h / w / 2) * 2
    target = OUT / "render"
    target.mkdir(parents=True, exist_ok=True)
    for old in target.glob("*.tif*"):
        old.unlink()
    settings = {"SelectAllFrames": True, "TargetDir": str(target), "CustomName": "spike",
                "ExportVideo": True, "ExportAudio": False, "FormatWidth": ANALYSIS_WIDTH, "FormatHeight": height}
    log(f"    SetRenderSettings -> {project.SetRenderSettings(settings)}   {settings}")
    job = project.AddRenderJob()
    log(f"    AddRenderJob -> {job}")
    if not job:
        return False
    start = time.time()
    project.StartRendering([job])
    while project.IsRenderingInProgress():
        time.sleep(0.5)
    took = time.time() - start
    status = project.GetRenderJobStatus(job)
    project.DeleteRenderJob(job)
    files = sorted(target.glob("*.tif*"))
    log(f"    status: {status}")
    log(f"    {len(files)} files for {frames} frames in {took:.1f} s  ->  {took / max(frames, 1):.2f} s/frame")
    if files:
        log(f"    first file: {files[0].name}  ({files[0].stat().st_size} bytes)")
    return files if len(files) >= SAME_FRAME_ITEMS else False


@check(10, "CDL on pixels: compare the rendered frames")
def check_pixels(files):
    import numpy as np  # noqa: PLC0415

    frames = [read_tiff(f) for f in files[:SAME_FRAME_ITEMS]]
    base, off, slope, sat, v_user, v_auto, node2 = (f[0] for f in frames)
    log(f"    frame size {base.shape[1]}x{base.shape[0]}, {frames[0][1]} bit, "
        f"value range {base.min():.4f}-{base.max():.4f}, mean {base.mean(axis=(0, 1)).round(4)}")
    mask = (base > 0.1).all(-1) & (base < 0.75).all(-1)       # room for +-1 stop and slope 1.2 without clipping
    log(f"    midtone pixels used: {mask.sum()}")
    if not mask.any():
        return False

    def added(img, name, expected):
        d = (img - base)[mask]
        log(f"    {name}: mean added {d.mean(0).round(5)}  (expected {expected:+.5f})  std {d.std(0).round(5)}")
        return abs(float(d.mean()) - expected) < 0.005

    ok = added(off, "offset +1 stop  (item 2)", STOP)
    ratio = slope[mask] / np.maximum(base[mask], 1e-6)
    log(f"    slope:   mean ratio {ratio.mean(0).round(4)}  (expected 1.2)")
    # sat 0.5: out = luma + 0.5 (in - luma)  ->  luma = 2·out - in, fit luma = in · w
    luma = (2 * sat - base)[mask]
    log(f"    saturation: channel spread of 2*out-in {luma.std(-1).mean():.5f} (0 = a pure luma mix)")
    w, *_ = np.linalg.lstsq(base[mask], luma.mean(-1), rcond=None)
    log(f"    saturation: fitted luma weights {w.round(4)}  (Rec.709 = [0.2126 0.7152 0.0722])")
    user_ok = added(v_user, "user version   (item 5)", STOP)
    auto_ok = added(v_auto, f"{AUTO}   (item 6)", -STOP)
    node2_ok = added(node2, "node 2 offset  (item 7)", STOP)
    log(f"    user grade untouched by writing {AUTO}: {'YES' if user_ok else 'NO'};  "
        f"{AUTO} renders its own values: {'YES' if auto_ok else 'NO'};  node 2 writable: "
        f"{'YES' if node2_ok else 'NO'}")
    return ok and user_ok and auto_ok and node2_ok


# ------------------------------------------------------------------------------ main

def main(resolve) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    log(f"davigen · Basic Correction spike · {time.strftime('%Y-%m-%d %H:%M')}")
    project = resolve.GetProjectManager().GetCurrentProject()
    original = project.GetCurrentTimeline() if project else None
    if original is None:
        log("No project or timeline open - open a davigen project with a timeline first.")
        return
    check_version(resolve, project)
    source = next((i for i in video_items(original) if i.GetMediaPoolItem()), None)
    if source is None:
        log(f"The timeline '{original.GetName()}' has no video clips.")
        return
    mpi = source.GetMediaPoolItem()
    total = int(mpi.GetClipProperty("Frames") or 0)
    frame = max(total // 2, 0)
    log(f"project: {project.GetName()}  timeline: {original.GetName()}  clip: {mpi.GetName()} ({total} frames, "
        f"{mpi.GetClipProperty('Resolution')}, {mpi.GetClipProperty('Video Codec')}, "
        f"{mpi.GetClipProperty('FPS')} fps)")
    log(f"    file: {mpi.GetClipProperty('File Path')}")
    log(f"    sample frame (0-based source frame): {frame}")
    cdl = Cdl(resolve)
    log(f"    page at start: {cdl.page}")

    check_source_range(source)
    semantics = check_snippets(project, mpi, frame)
    end_extra = semantics[0] if semantics else 0

    mp = project.GetMediaPool()
    delete_timeline(project, SPIKE_TL)
    spike = mp.CreateEmptyTimeline(SPIKE_TL)
    try:
        project.SetCurrentTimeline(spike)
        for _ in range(SAME_FRAME_ITEMS):
            append(mp, mpi, frame, frame + end_extra)
        step = max(total // (TIMING_FRAMES + 1), 1)
        for k in range(1, TIMING_FRAMES + 1):               # spread over the clip, for timing
            f = min(k * step, max(total - 1 - end_extra, 0))
            append(mp, mpi, f, f + end_extra)
        items = video_items(spike)
        log(f"\nspike timeline: {len(items)} items, durations {[i.GetDuration() for i in items[:8]]} ...")

        if len(items) >= SAME_FRAME_ITEMS:
            check_structure(items[1:SAME_FRAME_ITEMS])
            check_setcdl(cdl, items[1:4], items[6])
            check_versions(cdl, items[4], items[5])
            check_markers(items[1])
            files = check_render(project, spike, len(items))
            if files:
                check_pixels(files)
    finally:
        project.SetCurrentTimeline(original)
        if cdl.page and cdl.page != "?" and resolve.GetCurrentPage() != cdl.page:
            resolve.OpenPage(cdl.page)
        report = OUT / "report.txt"
        report.write_text("\n".join(lines) + "\n", encoding="utf-8")
        log(f"\nReport: {report}\nThe timeline {SPIKE_TL} is kept for inspection; the next run replaces it.")


if globals().get("resolve") is not None:
    main(globals()["resolve"])              # injected by Resolve's Scripts menu
elif __name__ == "__main__":
    import DaVinciResolveScript as dvr  # type: ignore  # Studio: external run
    main(dvr.scriptapp("Resolve"))
