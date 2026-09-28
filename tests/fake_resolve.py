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
        self._grade()["labels"] = list(LABELS)
        return True


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
    def __init__(self, name, items=None, fps=25.0):
        self.name, self.items, self.fps = name, list(items or []), fps
        self.settings = {"timelineResolutionWidth": "3240", "timelineResolutionHeight": "2160",
                         "timelineFrameRate": str(fps)}
        self.timecode = ""

    def GetName(self):
        return self.name

    def GetTrackCount(self, kind):
        return 1 if kind == "video" else 0

    def GetItemListInTrack(self, kind, idx):
        return self.items

    def GetSetting(self, key):
        return self.settings.get(key)

    def SetSetting(self, key, value):
        self.settings[key] = value
        return True

    def SetCurrentTimecode(self, tc):
        self.timecode = tc
        return True


class MediaPool:
    def __init__(self, project):
        self.project = project

    def CreateEmptyTimeline(self, name):
        tl = Timeline(name)
        self.project.timelines.append(tl)
        return tl

    def DeleteTimelines(self, tls):
        self.project.timelines = [t for t in self.project.timelines if t not in tls]
        return True

    def AppendToTimeline(self, infos):
        out = []
        for info in infos:
            assert info["endFrame"] == info["startFrame"] + 1
            ti = TimelineItem(info["mediaPoolItem"], 0, 1, info["startFrame"])
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

    def GetName(self):
        return self.name

    def GetSetting(self, key):
        return {"timelineFrameRate": "25"}.get(key, "")

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
        self.jobs.append("job")
        return "job"

    def StartRendering(self, jobs):
        self.renders += 1
        target = Path(self.render_settings["TargetDir"])
        w, h = self.render_settings["FormatWidth"], self.render_settings["FormatHeight"]
        for n, ti in enumerate(self.current.items):
            frame = ti.mpi.log_frame(ti.source_start)            # camera log 0–1
            ys = np.linspace(0, frame.shape[0] - 1, h).astype(int)
            xs = np.linspace(0, frame.shape[1] - 1, w).astype(int)
            img = (np.clip(frame[ys][:, xs], 0, 1) * 65535).astype("uint16")
            write_tiff(target / f"a{90000 + n:08d}.tif", img)
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
