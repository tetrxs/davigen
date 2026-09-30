"""A stand-in for Resolve that is enough for the whole pipeline: Project Manager with folders, project import and
export, project settings (the playback frame rate refused, as in Resolve 21), Media Pool with bins, clips, metadata
and markers, timelines with items, colour groups with LUTs and grade versions."""

from __future__ import annotations

import itertools
import shutil
from pathlib import Path

from davigen import template

_ids = itertools.count(1)


def uid() -> str:
    return f"u{next(_ids):05d}"


class Clip:
    def __init__(self, path: str, fps: float = 25.0):
        self.path, self.fps, self.id = path, fps, uid()
        self.meta: dict = {}
        self.third: dict = {}
        self.markers: dict[int, dict] = {}
        self.colour = ""

    def GetUniqueId(self):
        return self.id

    def GetName(self):
        return Path(self.path).name

    def GetClipProperty(self, key=None):
        props = {"File Path": self.path, "FPS": str(self.fps), "Frames": "250"}
        return props if key is None else props.get(key, "")

    def SetMetadata(self, data):
        self.meta.update(data)
        return True

    def GetMetadata(self, key=None):
        return self.meta.get(key, "") if key else dict(self.meta)

    def SetThirdPartyMetadata(self, data):
        self.third.update(data)
        return True

    def GetThirdPartyMetadata(self, key=None):
        return self.third.get(key, "") if key else dict(self.third)

    def SetClipColor(self, c):
        self.colour = c
        return True

    def AddMarker(self, frame, color, name, note, duration, data=""):
        if frame in self.markers:
            return False
        self.markers[frame] = {"color": color, "name": name, "note": note, "duration": duration, "customData": data}
        return True

    def GetMarkers(self):
        return dict(self.markers)

    def DeleteMarkerByCustomData(self, data):
        self.markers = {f: m for f, m in self.markers.items() if m["customData"] != data}
        return True


class Folder:
    def __init__(self, name):
        self.name, self.clips, self.subs = name, [], []

    def GetName(self):
        return self.name

    def GetClipList(self):
        return list(self.clips)

    def GetSubFolderList(self):
        return list(self.subs)


class Graph:
    def __init__(self, item):
        self.item = item

    def GetNumNodes(self):
        return len(self.item.nodes)

    def GetNodeLabel(self, i):
        return self.item.nodes[i - 1]

    def GetToolsInNode(self, i):
        return []

    def GetLUT(self, i):
        return ""

    def ApplyGradeFromDRX(self, path, mode):
        self.item.nodes = ["01_EXPOSURE", "02_WHITE_BALANCE", "03_CONTRAST", "04_SATURATION", "05_SECONDARIES",
                           "06_FINISH"]
        return True


class Item:
    def __init__(self, clip, start):
        self.clip, self.start, self.id = clip, start, uid()
        self.group = None
        self.nodes = [""]
        self.versions = ["Version 1"]

    def GetUniqueId(self):
        return self.id

    def GetName(self):
        return self.clip.GetName()

    def GetMediaPoolItem(self):
        return self.clip

    def GetColorGroup(self):
        return self.group

    def AssignToColorGroup(self, g):
        self.group = g
        return True

    def GetNodeGraph(self, layer=1):
        return Graph(self)

    def GetVersionNameList(self, kind):
        return list(self.versions)


class Timeline:
    def __init__(self, name):
        self.name, self.items = name, []
        self.settings: dict = {}
        self.tracks = {"video": ["V1"], "audio": ["A1"]}

    def GetName(self):
        return self.name

    def GetTrackCount(self, kind):
        return len(self.tracks[kind])

    def AddTrack(self, kind, sub=""):
        self.tracks[kind].append("")
        return True

    def SetTrackName(self, kind, idx, name):
        self.tracks[kind][idx - 1] = name
        return True

    def GetItemListInTrack(self, kind, idx):
        return list(self.items) if kind == "video" and idx == 1 else []

    def SetSetting(self, k, v):
        self.settings[k] = v
        return True

    def GetSetting(self, k):
        return self.settings.get(k) or self.project.settings.get(k, "")      # a timeline follows the project


class GroupGraph:
    def __init__(self):
        self.lut = ""

    def SetLUT(self, i, path):
        self.lut = path
        return True

    def GetLUT(self, i):
        return self.lut


class Group:
    def __init__(self, name):
        self.name, self.pre, self.post = name, GroupGraph(), GroupGraph()

    def GetName(self):
        return self.name

    def GetPreClipNodeGraph(self):
        return self.pre

    def GetPostClipNodeGraph(self):
        return self.post


class MediaPool:
    def __init__(self, project):
        self.project = project
        self.root = Folder("Master")
        self.current = self.root
        self.refuse: set[str] = set()                    # file names Resolve "can't import"

    def GetRootFolder(self):
        return self.root

    def AddSubFolder(self, parent, name):
        f = Folder(name)
        parent.subs.append(f)
        return f

    def SetCurrentFolder(self, f):
        self.current = f
        return True

    def ImportMedia(self, paths):
        out = []
        for p in paths:
            if Path(p).name in self.refuse or not Path(p).exists():
                continue
            c = Clip(p)
            self.current.clips.append(c)
            out.append(c)
        return out

    def _all(self, folder=None):
        folder = folder or self.root
        for c in folder.clips:
            yield folder, c
        for s in folder.subs:
            yield from self._all(s)

    def DeleteClips(self, clips):
        for folder, c in list(self._all()):
            if c in clips:
                folder.clips.remove(c)
        return True

    def CreateEmptyTimeline(self, name):
        tl = Timeline(name)
        tl.project = self.project
        self.project.timelines.append(tl)
        self.project.current = tl
        return tl

    def CreateTimelineFromClips(self, name, clips):
        tl = self.CreateEmptyTimeline(name)
        self.AppendToTimeline(clips)
        return tl

    def AppendToTimeline(self, clips):
        tl = self.project.current
        out = []
        for c in clips:
            c = c["mediaPoolItem"] if isinstance(c, dict) else c
            it = Item(c, len(tl.items))
            tl.items.append(it)
            out.append(it)
        return out

    def DeleteTimelines(self, tls):
        self.project.timelines = [t for t in self.project.timelines if t not in tls]
        return True


class Project:
    def __init__(self, name):
        self.name, self.id = name, uid()
        self.settings = {"timelineFrameRate": "24", "timelinePlaybackFrameRate": "24"}
        self.timelines: list[Timeline] = []
        self.current = None
        self.groups: list[Group] = []
        self.mp = MediaPool(self)
        self.presets: list[str] = []

    def GetUniqueId(self):
        return self.id

    def GetName(self):
        return self.name

    def GetSetting(self, key=None):
        return dict(self.settings) if key is None else self.settings.get(key, "")

    def SetSetting(self, key, value):
        if key == "timelinePlaybackFrameRate":
            return False                                   # Resolve 21: never through the API
        self.settings[key] = str(value)
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
        return list(self.groups)

    def AddColorGroup(self, name):
        g = Group(name)
        self.groups.append(g)
        return g

    def RefreshLUTList(self):
        return True

    def GetRenderPresetList(self):
        return list(self.presets)

    def SetCurrentRenderFormatAndCodec(self, f, c):
        return True

    def SetRenderSettings(self, s):
        return True

    def SaveAsNewRenderPreset(self, name):
        self.presets.append(name)
        return True

    def DeleteRenderPreset(self, name):
        self.presets.remove(name)
        return True


class ProjectManager:
    def __init__(self):
        self.folders: dict[str, dict[str, Project]] = {"": {}}
        self.path = ""
        self.current: Project | None = None
        self.saved = 0
        self.imported: list[str] = []

    def _here(self):
        return self.folders.setdefault(self.path, {})

    def GotoRootFolder(self):
        self.path = ""
        return True

    def GetFolderListInCurrentFolder(self):
        prefix = self.path + "/" if self.path else ""
        return sorted({k[len(prefix):].split("/")[0] for k in self.folders if k.startswith(prefix) and k != self.path
                       and k[len(prefix):]})

    def CreateFolder(self, name):
        self.folders[(self.path + "/" if self.path else "") + name] = {}
        return True

    def OpenFolder(self, name):
        target = (self.path + "/" if self.path else "") + name
        if target not in self.folders:
            return False
        self.path = target
        return True

    def GetProjectListInCurrentFolder(self):
        return list(self._here())

    def GetCurrentProject(self):
        return self.current

    def SaveProject(self):
        self.saved += 1
        return True

    def CreateProject(self, name):
        if name in self._here():
            return None
        p = Project(name)
        self._here()[name] = p
        self.current = p
        return p

    def LoadProject(self, name):
        p = self._here().get(name)
        if p is not None:
            self.current = p
        return p

    def CloseProject(self, p):
        if self.current is p:
            self.current = None
        return True

    def DeleteProject(self, name):
        if name not in self._here() or (self.current is not None and self.current.name == name):
            return False
        del self._here()[name]
        return True

    def ExportProject(self, name, path, with_stills=False):
        shutil.copy(Path(__file__).parent / "fixtures" / "empty_project_r21.drp", path)
        return True

    def ImportProject(self, path, name=None):
        setup = template.read_settings(Path(path))
        p = Project(name)
        fps = template.playback_fps(setup)
        if fps:
            p.settings["timelinePlaybackFrameRate"] = f"{fps:g}"
        p.proxy_location = template.working_folders(setup).get("proxy", "")
        self._here()[name] = p
        self.imported.append(name)
        return True


class Resolve:
    EXPORT_LUT_65PTCUBE = 2

    def __init__(self):
        self.pm = ProjectManager()
        self.page = ""

    def GetProjectManager(self):
        return self.pm

    def GetProductName(self):
        return "DaVinci Resolve"

    def GetVersionString(self):
        return "21.0.0.48"

    def OpenPage(self, page):
        self.page = page
        return True
