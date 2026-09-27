"""Basic Correction · step 01: check the Resolve API on the real thing.

Run from Resolve: copy to …/Fusion/Scripts/Utility/, then Workspace → Scripts → spike_basic_correction.
Watch Workspace → Console. The report is also written to ~/Movies/davigen_spike/report.txt.

Touches only its own timelines (ZZ_DAVIGEN_SPIKE*), which it deletes on the next run. The spike timeline is
kept so the grades can be inspected on the Color page. Render settings on the Deliver page are changed for
this project (the API can't read them back); the render job is removed again.
See docs/plans/basic-correction/01_SPIKE.md.
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
TIMING_FRAMES = 20
ANALYSIS_WIDTH = 480
MARKER_DATA = "davigen-basic"

lines: list[str] = []


def log(msg: str = "") -> None:
    print(msg)
    lines.append(msg)


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


def append(media_pool, mpi, start: int, end: int):
    info = {"mediaPoolItem": mpi, "startFrame": start, "endFrame": end}
    result = media_pool.AppendToTimeline([{**info, "mediaType": 1}])     # video only, if supported
    if not result:
        result = media_pool.AppendToTimeline([info])
    return result


def read_tiff(path: Path):
    """Minimal reader for uncompressed RGB TIFFs (8/16 bit, strips). Returns a float array 0–1."""
    import numpy as np  # noqa: PLC0415

    try:
        import tifffile  # noqa: PLC0415
        data = tifffile.imread(str(path))
        return data.astype("float64") / (65535.0 if data.dtype == np.uint16 else 255.0)
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
        raise ValueError(f"TIFF compression {compression} – install tifffile to read it")
    data = b"".join(raw[o:o + c] for o, c in zip(tags[273], tags[279]))
    dtype = np.dtype(endian + ("u2" if bits == 16 else "u1"))
    img = np.frombuffer(data, dtype=dtype)[:width * height * spp].reshape(height, width, spp)[..., :3]
    return img.astype("float64") / (65535.0 if bits == 16 else 255.0)


# ---------------------------------------------------------------------------- checks

@check(1, "Resolve version")
def check_version(resolve):
    log(f"    product: {resolve.GetProductName()}  version: {resolve.GetVersionString()}")


@check(2, "Source range of a timeline item (GetSourceStartFrame / GetSourceEndFrame)")
def check_source_range(item):
    log(f"    item: {item.GetName()}  start/end on timeline: {item.GetStart()}–{item.GetEnd()}  "
        f"duration {item.GetDuration()}")
    for name in ("GetSourceStartFrame", "GetSourceEndFrame", "GetLeftOffset", "GetRightOffset"):
        if hasattr(item, name):
            log(f"    {name}() = {getattr(item, name)()}")
        else:
            log(f"    {name}: missing")
    return hasattr(item, "GetSourceStartFrame") and hasattr(item, "GetSourceEndFrame")


@check(3, "One-frame snippets via AppendToTimeline(startFrame, endFrame)")
def check_snippets(project, mpi, frame: int):
    mp = project.GetMediaPool()
    delete_timeline(project, RANGE_TL)
    tl = mp.CreateEmptyTimeline(RANGE_TL)
    project.SetCurrentTimeline(tl)
    append(mp, mpi, frame, frame)
    append(mp, mpi, frame, frame + 1)
    durations = [i.GetDuration() for i in video_items(tl)]
    log(f"    (f, f) -> {durations[0] if durations else '?'} frame(s);  "
        f"(f, f+1) -> {durations[1] if len(durations) > 1 else '?'} frame(s)")
    inclusive = bool(durations) and durations[0] == 1
    exclusive = len(durations) > 1 and durations[1] == 1
    log(f"    endFrame is {'inclusive' if inclusive else 'exclusive' if exclusive else 'unclear'}")
    mp.DeleteTimelines([tl])
    if inclusive:
        return (0, True)
    if exclusive:
        return (1, True)
    return False


@check(4, "Node graph + CLIP_STRUCTURE.drx, labels")
def check_structure(item):
    graph = item.GetNodeGraph()
    if CLIP_TEMPLATE.exists():
        log(f"    ApplyGradeFromDRX: {graph.ApplyGradeFromDRX(str(CLIP_TEMPLATE), 0)}")
    else:
        log(f"    {CLIP_TEMPLATE} not found – node structure not applied")
    graph = item.GetNodeGraph()
    n = graph.GetNumNodes()
    labels = [graph.GetNodeLabel(i) for i in range(1, n + 1)]
    log(f"    nodes: {n}  labels: {labels}")
    return n >= 1


@check(5, "SetCDL on node 1")
def check_setcdl(items):
    ok = True
    cdls = [
        ("offset +1 stop", {"Slope": "1 1 1", "Offset": f"{STOP:.6f} {STOP:.6f} {STOP:.6f}",
                            "Power": "1 1 1", "Saturation": "1"}),
        ("slope 1.2", {"Slope": "1.2 1.2 1.2", "Offset": "0 0 0", "Power": "1 1 1", "Saturation": "1"}),
        ("saturation 0.5", {"Slope": "1 1 1", "Offset": "0 0 0", "Power": "1 1 1", "Saturation": "0.5"}),
    ]
    for item, (label, cdl) in zip(items, cdls):
        result = item.SetCDL({"NodeIndex": "1", **cdl})
        log(f"    {label}: SetCDL -> {result}   values {cdl}")
        ok = ok and bool(result)
    log("    LOOK: on the Color page, open these clips on ZZ_DAVIGEN_SPIKE (2nd–4th) and note what")
    log("          Primaries → Offset / Gain / Gamma / Sat show for the values above.")
    return ok


@check(9, "Analysis render: 16-bit TIFF sequence, small size, timed")
def check_render(project, timeline, frames: int):
    project.SetCurrentTimeline(timeline)
    codecs = project.GetRenderCodecs("tif") or {}
    log(f"    TIFF codecs: {codecs}")
    codec = next((c for d, c in codecs.items() if "16" in d or "16" in c), None) or next(iter(codecs.values()), "")
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
        log(f"    first file: {files[0].name}")
    return files if len(files) >= 4 else False


@check(10, "CDL on pixels: compare the rendered frames")
def check_pixels(files):
    import numpy as np  # noqa: PLC0415

    base, off, slope, sat = (read_tiff(f) for f in files[:4])
    log(f"    frame size {base.shape[1]}×{base.shape[0]}, value range {base.min():.4f}–{base.max():.4f}")
    mask = (base > 0.05).all(-1) & (base < 0.8).all(-1)       # room for slope 1.2 without clipping
    log(f"    midtone pixels used: {mask.sum()}")
    d = (off - base)[mask]
    log(f"    offset:  mean added {d.mean(0).round(5)}  (expected {STOP:.5f})  std {d.std(0).round(5)}")
    ratio = (slope[mask] / np.maximum(base[mask], 1e-6))
    log(f"    slope:   mean ratio {ratio.mean(0).round(4)}  (expected 1.2)")
    # sat 0.5: out = luma + 0.5 (in − luma)  ->  luma = 2·out − in, fit luma = in · w
    luma = (2 * sat - base)[mask].mean(-1)
    w, *_ = np.linalg.lstsq(base[mask], luma, rcond=None)
    log(f"    saturation: fitted luma weights {w.round(4)}  (Rec.709 = [0.2126 0.7152 0.0722])")
    return True


@check(6, "Grade versions: AddVersion / GetVersionNameList / LoadVersionByName")
def check_versions(item):
    before = item.GetVersionNameList(0)
    current = item.GetCurrentVersion() if hasattr(item, "GetCurrentVersion") else "?"
    log(f"    local versions before: {before}   current: {current}")
    added = item.AddVersion("DAVIGEN_AUTO", 0)
    log(f"    AddVersion('DAVIGEN_AUTO', 0) -> {added}")
    after = item.GetVersionNameList(0)
    current = item.GetCurrentVersion() if hasattr(item, "GetCurrentVersion") else "?"
    log(f"    local versions after:  {after}   current: {current}")
    graph = item.GetNodeGraph()
    n = graph.GetNumNodes()
    log(f"    [7] in the new version: {n} nodes, labels {[graph.GetNodeLabel(i) for i in range(1, n + 1)]}")
    log("    LOOK: is this version a copy of the previous grade or empty? (Color page → clip → Versions)")
    if before:
        log(f"    LoadVersionByName('{before[0]}', 0) -> {item.LoadVersionByName(before[0], 0)}")
        graph = item.GetNodeGraph()
        log(f"    back in '{before[0]}': {graph.GetNumNodes()} nodes")
        log(f"    LoadVersionByName('DAVIGEN_AUTO', 0) -> {item.LoadVersionByName('DAVIGEN_AUTO', 0)}")
    return bool(added) and "DAVIGEN_AUTO" in (after or [])


@check(8, "Markers with custom data")
def check_markers(item):
    try:
        added = item.AddMarker(0, "Yellow", "davigen: test", "spike marker", 1, MARKER_DATA)
    except TypeError:
        added = item.AddMarker(0, "Yellow", "davigen: test", "spike marker", 1)
        log("    AddMarker doesn't take custom data")
    log(f"    AddMarker -> {added}   markers: {item.GetMarkers()}")
    if hasattr(item, "DeleteMarkerByCustomData"):
        log(f"    DeleteMarkerByCustomData -> {item.DeleteMarkerByCustomData(MARKER_DATA)}   "
            f"markers: {item.GetMarkers()}")
    else:
        log("    DeleteMarkerByCustomData: missing")
    return bool(added)


# ------------------------------------------------------------------------------ main

def main(resolve) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    log(f"davigen · Basic Correction spike · {time.strftime('%Y-%m-%d %H:%M')}")
    check_version(resolve)
    project = resolve.GetProjectManager().GetCurrentProject()
    original = project.GetCurrentTimeline() if project else None
    if original is None:
        log("No project or timeline open – open a davigen project with a timeline first.")
        return
    source = next((i for i in video_items(original) if i.GetMediaPoolItem()), None)
    if source is None:
        log(f"The timeline '{original.GetName()}' has no video clips.")
        return
    mpi = source.GetMediaPoolItem()
    total = int(mpi.GetClipProperty("Frames") or 0)
    log(f"project: {project.GetName()}  timeline: {original.GetName()}  clip: {mpi.GetName()} ({total} frames, "
        f"{mpi.GetClipProperty('Resolution')}, {mpi.GetClipProperty('Video Codec')})")
    frame = max(total // 2, 0)

    check_source_range(source)
    semantics = check_snippets(project, mpi, frame)
    end_extra = semantics[0] if semantics else 0

    mp = project.GetMediaPool()
    delete_timeline(project, SPIKE_TL)
    spike = mp.CreateEmptyTimeline(SPIKE_TL)
    try:
        project.SetCurrentTimeline(spike)
        for _ in range(4):                                  # base, offset, slope, saturation – same frame
            append(mp, mpi, frame, frame + end_extra)
        step = max(total // (TIMING_FRAMES + 1), 1)
        for k in range(1, TIMING_FRAMES + 1):               # spread over the clip, for timing
            f = min(k * step, max(total - 1 - end_extra, 0))
            append(mp, mpi, f, f + end_extra)
        items = video_items(spike)
        log(f"\nspike timeline: {len(items)} items, durations {[i.GetDuration() for i in items[:6]]} …")

        if len(items) >= 4:
            check_structure(items[1])
            check_setcdl(items[1:4])
            files = check_render(project, spike, len(items))
            if files:
                check_pixels(files)
            check_versions(items[1])
            check_markers(items[1])
    finally:
        project.SetCurrentTimeline(original)
        report = OUT / "report.txt"
        report.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\nReport: {report}\nThe timeline {SPIKE_TL} is kept for inspection; the next run replaces it.")


if globals().get("resolve") is not None:
    main(globals()["resolve"])              # injected by Resolve's Scripts menu
elif __name__ == "__main__":
    import DaVinciResolveScript as dvr  # type: ignore  # Studio: external run
    main(dvr.scriptapp("Resolve"))
