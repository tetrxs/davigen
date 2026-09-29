"""A small stand-in for the Resolve API, enough for Basic Correction's write and run flows.

It mirrors what step 01 verified in Resolve 21: AddVersion copies the grade and activates the copy, SetCDL writes
the active version, AppendToTimeline's endFrame is exclusive, renders are uncompressed TIFF sequences.
"""

from __future__ import annotations

import copy
from pathlib import Path

import numpy as np

from test_basic_sampling import write_tiff

LABELS = ["01_EXPOSURE", "02_WHITE_BALANCE", "03_CONTRAST", "04_SATURATION", "05_SECONDARIES", "06_FINISH"]


class Graph:
    def __init__(self, item):
        self.item = item

    def _grade(self):
        return self.item.versions[self.item.current]

    def GetNumNodes(self):
        return len(self._grade()["labels"])

    def GetNodeLabel(self, i):
        return self._grade()["labels"][i - 1]

    def GetToolsInNode(self, i):
        return []

    def GetLUT(self, i):
        return self._grade().get("lut", "")

    def ApplyGradeFromDRX(self, path, mode):
        from davigen import drx
        self.item.calls.append(f"ApplyGradeFromDRX:{mode}")
        grade = self._grade()
        grade["labels"], grade["cdl"] = list(LABELS), {}
        try:
            kf = drx.read_keyframes(Path(path).read_text(encoding="utf-8", errors="replace"))
        except (ValueError, IndexError, OSError):
            kf = {}
        timed = {label: [(t, v) for t, v in rows if t is not None] for label, rows in kf.items()}
        timed = {label: rows for label, rows in timed.items() if rows and any(v for _, v in rows)}
        if timed:                           # a grade without keyframes leaves old ones in place (verified)
            grade["keyframes"] = timed
        return True


def keyframed_cdl(label, rows, frame):
    """The CDL a keyframed node has at a source frame: linear between keyframes, held outside (as verified)."""
    from davigen import drx
    from davigen.basic import pipeline as p
    frames = [t for t, _ in rows]
    ids = sorted({k for _, v in rows for k in v})
    at = {k: float(np.interp(frame, frames, [v.get(k, 0.0) for _, v in rows])) for k in ids}
    if drx.P_OFFSET[0] in at:
        return p.Cdl(offset=tuple(at[k] * drx.OFFSET_SCALE for k in drx.P_OFFSET))
    k, pivot = at.get(drx.P_CONTRAST, 1.0), at.get(drx.P_PIVOT, 0.435)
    if k > 1.0:
        raise AssertionError("davigen must not keyframe a Contrast above 1 (an S-curve in Resolve)")
    return p.Cdl(slope=(k, k, k), offset=(pivot * (1 - k),) * 3, sat=at.get(drx.P_SATURATION, 1.0))


class GroupGraph:
    def __init__(self, lut):
        self.lut = lut

    def GetLUT(self, i):
        return self.lut


class Group:
    def __init__(self, name, in_lut, out_lut):
        self.name, self.pre, self.post = name, GroupGraph(in_lut), GroupGraph(out_lut)

    def GetName(self):
        return self.name

    def GetPreClipNodeGraph(self):
        return self.pre

    def GetPostClipNodeGraph(self):
        return self.post


class MediaPoolItem:
    def __init__(self, path, group, frames, log_frame, fps=25.0):
        self.path, self.group, self.frames, self.log_frame, self.fps = path, group, frames, log_frame, fps
        self.markers: dict[int, dict] = {}

    def AddMarker(self, frame, colour, name, note, duration, data=""):
        if frame in self.markers:
            return False
        self.markers[frame] = {"color": colour, "name": name, "note": note, "duration": duration, "customData": data}
        return True

    def DeleteMarkerByCustomData(self, data):
        self.markers = {f: m for f, m in self.markers.items() if m["customData"] != data}
        return True

    def AddFlag(self, colour):
        self.flags = getattr(self, "flags", set()) | {colour}
        return True

    def ClearFlags(self, colour):
        self.flags = getattr(self, "flags", set()) - {colour}
        return True

    def GetMetadata(self, key=None):
        return getattr(self, "metadata", {}).get(key, "") if key else getattr(self, "metadata", {})

    def SetMetadata(self, data):
        self.metadata = {**getattr(self, "metadata", {}), **data}
        return True

    def GetClipProperty(self, key):
        return {"File Path": self.path, "FPS": str(self.fps), "Frames": str(self.frames)}.get(key, "")

    def GetThirdPartyMetadata(self, key):
        return self.group if key == "davigen.group" else ""

    def GetName(self):
        return Path(self.path).name


class TimelineItem:
    _next = 0

    def __init__(self, mpi, start, duration, source_start, labels=None, group=None):
        TimelineItem._next += 1
        self.uid = f"uid{TimelineItem._next}"
        self.mpi, self.start, self.duration, self.source_start = mpi, start, duration, source_start
        self.versions = {"Version 1": {"labels": list(labels) if labels else [""], "cdl": {}}}
        self.current = "Version 1"
        self.group = group
        self.markers: dict[int, dict] = {}
        self.calls: list[str] = []
        self.refuse_cdl = False
        self.add_version_activates = True

    def GetUniqueId(self):
        return self.uid

    def GetName(self):
        return self.mpi.GetName() if self.mpi else "gap"

    def GetMediaPoolItem(self):
        return self.mpi

    def GetDuration(self):
        return self.duration

    def GetStart(self):
        return self.start

    def GetSourceStartFrame(self):
        return self.source_start

    def GetColorGroup(self):
        return self.group

    def AssignToColorGroup(self, group):
        self.group = group
        return True

    def GetNodeGraph(self, layer=1):
        return Graph(self)

    def GetVersionNameList(self, kind):
        return list(self.versions)

    def GetCurrentVersion(self):
        return {"versionName": self.current, "versionType": 0}

    def AddVersion(self, name, kind):
        self.calls.append("AddVersion")
        self.versions[name] = copy.deepcopy(self.versions[self.current])
        if self.add_version_activates:
            self.current = name
        return True

    def DeleteVersionByName(self, name, kind):
        self.calls.append("DeleteVersionByName")
        if name not in self.versions or name == self.current:
            return False
        del self.versions[name]
        return True

    def LoadVersionByName(self, name, kind):
        self.calls.append("LoadVersionByName")
        if name in self.versions and not getattr(self, "refuse_load", False):
            self.current = name
            return True
        return False

    def SetCDL(self, cdl):
        self.calls.append("SetCDL")
        if self.refuse_cdl:
            return False
        grade = self.versions[self.current]
        label = grade["labels"][int(cdl["NodeIndex"]) - 1] if int(cdl["NodeIndex"]) <= len(grade["labels"]) else ""
        if label in grade.get("keyframes", {}):
            self.calls.append("SetCDL into keyframes")
            return True                     # Resolve says yes but writes into one keyframe only (verified)
        self.versions[self.current]["cdl"][int(cdl["NodeIndex"])] = cdl
        return True

    def AddMarker(self, frame, colour, name, note, duration, data=""):
        self.markers[frame] = {"color": colour, "name": name, "note": note, "customData": data}
        return True

    def GetMarkers(self):
        return self.markers

    def DeleteMarkerByCustomData(self, data):
        self.markers = {f: m for f, m in self.markers.items() if m["customData"] != data}
        return True


class Timeline:
    stale = False

    def __getattribute__(self, name):
        if name.startswith("Get") and object.__getattribute__(self, "stale"):
            return None
        return object.__getattribute__(self, name)

    def __init__(self, name, items=None, fps=25.0):
        self.name, self.items, self.fps = name, list(items or []), fps
        self.settings = {"timelineResolutionWidth": "3240", "timelineResolutionHeight": "2160",
                         "timelineFrameRate": str(fps)}
        self.timecode = ""
        self.track_names = {"video": [], "audio": []}

    def GetName(self):
        return self.name

    def GetTrackCount(self, kind):
        return max(1, len(self.track_names["video"])) if kind == "video" else len(self.track_names.get(kind, []))

    def GetItemListInTrack(self, kind, idx):
        return [i for i in self.items if getattr(i, "kind", "video") == kind and getattr(i, "track", 1) == idx]

    def AddTrack(self, kind, sub=""):
        names = self.track_names[kind]
        if kind == "video" and not names:
            names.append("")                                    # a timeline starts with V1
        names.append("")
        return True

    def SetTrackName(self, kind, idx, name):
        self.track_names[kind][idx - 1] = name
        return True

    def GetTrackName(self, kind, idx):
        names = self.track_names.get(kind, [])
        return names[idx - 1] if idx <= len(names) else ""

    def GetSetting(self, key):
        return self.settings.get(key)

    def SetSetting(self, key, value):
        self.settings[key] = value
        return True

    def SetCurrentTimecode(self, tc):
        self.timecode = tc
        return True

    def GetStartFrame(self):
        return 90000

    def place(self, info):
        """Edit-assist timelines: honour recordFrame, else append after the last item of the same kind."""
        kind = "audio" if info.get("mediaType") == 2 else "video"
        length = info["endFrame"] - info["startFrame"]
        same = [i for i in self.items if getattr(i, "kind", "video") == kind]
        start = info.get("recordFrame", same[-1].start + same[-1].duration if same else 90000)
        ti = TimelineItem(info["mediaPoolItem"], start, length, info["startFrame"])
        ti.kind = kind
        ti.track = info.get("trackIndex", 1)
        self.items.append(ti)
        return ti


class Folder:
    def __init__(self, name, clips=None):
        self.name, self.clips, self.subs = name, list(clips or []), []

    def GetName(self):
        return self.name

    def GetClipList(self):
        return self.clips

    def GetSubFolderList(self):
        return self.subs


class MediaPool:
    def __init__(self, project):
        self.project = project
        self.root = Folder("Master")
        self.folder = self.root

    def GetRootFolder(self):
        return self.root

    def AddSubFolder(self, parent, name):
        sub = Folder(name)
        parent.subs.append(sub)
        return sub

    def SetCurrentFolder(self, folder):
        self.folder = folder
        return True

    def ImportMedia(self, paths):
        items = [MediaPoolItem(p, "", 1000, None) for p in paths]
        self.folder.clips += items
        return items

    def CreateEmptyTimeline(self, name):
        tl = Timeline(name)
        self.project.timelines.append(tl)
        return tl

    def DeleteTimelines(self, tls):
        """Like Resolve 21: deleting a timeline invalidates the API objects of the other timelines (their methods
        answer None); fresh objects must be fetched from the project again."""
        keep = []
        for t in self.project.timelines:
            if t in tls:
                continue
            fresh = copy.copy(t)
            t.stale = True
            if self.project.current is t:
                self.project.current = fresh
            keep.append(fresh)
        self.project.timelines = keep
        return True

    def AppendToTimeline(self, infos):
        out = []
        tl = self.project.current
        for info in infos:
            if tl.name.startswith("TL_"):                         # edit-assist timelines
                ti = tl.place(info)
                out.append(ti)
                continue
            span = info["endFrame"] - info["startFrame"]
            assert span >= 1                                        # (f, f) fails in Resolve (step 01)
            fps = info["mediaPoolItem"].fps
            length = max(1, round(span * 25.0 / fps)) if fps > 25 else span
            ti = TimelineItem(info["mediaPoolItem"], 0, length, info["startFrame"])
            self.project.current.items.append(ti)
            out.append(ti)
        return out


class Project:
    def __init__(self, name, timelines, groups):
        self.name, self.timelines, self.groups = name, timelines, groups
        self.current = timelines[0]
        self.renders = 0
        self.jobs: list[str] = []
        self.mp = MediaPool(self)
        self.preset = ""
        self.job_settings: dict[str, dict] = {}

    def GetName(self):
        return self.name

    def GetSetting(self, key):
        return {"timelineFrameRate": "25"}.get(key, "")

    def SaveProject(self):
        return True

    def GetMediaPool(self):
        return self.mp

    def GetTimelineCount(self):
        return len(self.timelines)

    def GetTimelineByIndex(self, i):
        return self.timelines[i - 1]

    def GetCurrentTimeline(self):
        return self.current

    def SetCurrentTimeline(self, tl):
        self.current = tl
        return True

    def GetColorGroupsList(self):
        return self.groups

    def GetRenderPresetList(self):
        return ["DAVIGEN_MASTER_3X2", "YouTube"]

    def LoadRenderPreset(self, name):
        self.preset = name
        return True

    def SetCurrentRenderFormatAndCodec(self, fmt, codec):
        return (fmt, codec) == ("tif", "RGB16")

    def SetRenderSettings(self, s):
        self.render_settings = s
        return True

    def AddRenderJob(self):
        job = f"job{len(self.job_settings) + 1}"
        self.jobs.append(job)
        self.job_settings[job] = dict(self.render_settings)
        return job

    def _graded(self, ti, frame):
        """What Resolve would render for an item: input LUT, the active version's CDLs, output LUT."""
        from davigen.basic import pipeline as p
        log = ti.mpi.log_frame(frame)
        group = ti.group or next(g for g in self.groups if g.name == ti.mpi.group)
        img = p.apply_lut(log, group.pre.lut)
        grade = ti.versions[ti.current]
        keyed = grade.get("keyframes", {})
        for n, label in enumerate(grade["labels"], 1):          # node by node, in order
            if label in keyed:
                img = p.apply_cdl(img, keyframed_cdl(label, keyed[label], frame))
            elif n in grade["cdl"]:
                cdl = grade["cdl"][n]
                nums = {k: [float(v) for v in cdl[k].split()] for k in ("Slope", "Offset", "Power", "Saturation")}
                img = p.apply_cdl(img, p.Cdl(tuple(nums["Slope"]), tuple(nums["Offset"]), tuple(nums["Power"]),
                                             nums["Saturation"][0]))
        return p.apply_lut(img, group.post.lut)

    def StartRendering(self, jobs):
        self.renders += 1
        settings = self.job_settings[jobs[0]]
        if not settings.get("SelectAllFrames", True):
            for job in jobs:                                         # evaluation: one graded frame per job
                st = self.job_settings[job]
                ti = next(t for t in self.current.items if t.start <= st["MarkIn"] < t.start + t.duration)
                frame = ti.source_start + (st["MarkIn"] - ti.start)
                img = self._graded(ti, frame)
                ys = np.linspace(0, img.shape[0] - 1, st["FormatHeight"]).astype(int)
                xs = np.linspace(0, img.shape[1] - 1, st["FormatWidth"]).astype(int)
                write_tiff(Path(st["TargetDir"]) / "e00090000.tif",
                           (np.clip(img[ys][:, xs], 0, 1) * 65535).astype("uint16"))
            return True
        self.render_settings = settings
        target = Path(self.render_settings["TargetDir"])
        w, h = self.render_settings["FormatWidth"], self.render_settings["FormatHeight"]
        n = 0
        for ti in self.current.items:
            for k in range(ti.duration):                           # a snippet may be longer than one frame
                frame = ti.mpi.log_frame(ti.source_start + k)      # camera log 0–1
                ys = np.linspace(0, frame.shape[0] - 1, h).astype(int)
                xs = np.linspace(0, frame.shape[1] - 1, w).astype(int)
                img = (np.clip(frame[ys][:, xs], 0, 1) * 65535).astype("uint16")
                write_tiff(target / f"a{90000 + n:08d}.tif", img)
                n += 1
        return True

    def IsRenderingInProgress(self):
        return False

    def GetRenderJobStatus(self, job):
        return {"JobStatus": "Complete", "CompletionPercentage": 100}

    def DeleteRenderJob(self, job):
        self.jobs.remove(job)
        return True


class ProjectManager:
    def __init__(self, project):
        self.project, self.saved = project, 0

    def GetCurrentProject(self):
        return self.project

    def SaveProject(self):
        self.saved += 1
        return True


class Resolve:
    def __init__(self, project):
        self.pm = ProjectManager(project)
        self.page = "edit"

    def GetProjectManager(self):
        return self.pm

    def OpenPage(self, page):
        self.page = page
        return True

    def GetProductName(self):
        return "DaVinci Resolve"

    def GetVersionString(self):
        return "21.0.0.48"
