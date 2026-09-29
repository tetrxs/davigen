"""Local HTTP server: serves the UI and a small JSON API. Runs inside the Resolve script process."""

from __future__ import annotations

import contextlib
import json
import mimetypes
import re
import secrets
import shutil
import socketserver
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__, catalog, creator, filesystem, formats, naming, providers, scanner, transfer, transforms
from .config import DATA_DIR, Camera, Config, save_settings, settings
from .formats import Format
from .project import PM_FOLDER
from .basic import settings as basic_settings
from .lut import Lut3D
from .resolve_api import capabilities, find_project, version

UI_DIR = Path(__file__).resolve().parent / "ui"
# Resolve's scripting API isn't thread-safe: requests that talk to Resolve run one at a time.
# (Background flows use Resolve from their own thread; the UI doesn't query Resolve while one runs.)
RESOLVE_ROUTES = {"/api/info", "/api/current", "/api/projects", "/api/open-project", "/api/vendor-lut",
                  "/api/preview", "/api/scan", "/api/basic/report", "/api/basic/goto", "/api/basic/learn",
                  "/api/basic/clip", "/api/basic/look"}
IDLE_TIMEOUT = 600         # seconds without any browser tab before davigen ends itself (no job running)


class App:
    def __init__(self, resolve, cfg: Config):
        self.resolve, self.cfg = resolve, cfg
        self.token = secrets.token_urlsafe(16)
        self.last_seen = time.time()
        self.scan_state: dict = {"running": False, "done": 0, "total": 0, "groups": [], "errors": []}
        self.clips: list[scanner.ClipInfo] = []
        self.reporter: creator.Reporter | None = None
        self.job: threading.Thread | None = None
        self.stop = threading.Event()
        self.catalog_state: dict = {"running": False, "done": 0, "total": 0, "what": "", "error": ""}
        self.recovery: list[dict] = []
        self.resolve_lock = threading.Lock()

    # ------------------------------------------------------------------ app info
    def info(self) -> dict:
        cfg = self.cfg
        caps = capabilities(self.resolve)
        studio = caps["studio"]
        default = formats.default_format(cfg, studio)
        user = settings()
        return {
            "version": __version__, "resolve": version(self.resolve), "studio": studio, "capabilities": caps,
            "format": {
                "default": default.as_dict(),
                "aspects": [{"id": a["id"], "label": a["label"], "presets": formats.presets(cfg, a["id"], studio)}
                            for a in formats.aspects(cfg)],
                "fps": cfg.workflow["project"]["fps_choices"],
                "free_max": cfg.workflow["project"]["free_max"],
                "deliveries": [{"id": d["id"], "label": d["label"], "default": bool(d.get("default")),
                                "resolution": d["resolution"]} for d in cfg.workflow["deliver"]],
            },
            "transfer": cfg.workflow["project"]["transfer"],
            "basic_default": basic_settings.load(cfg.workflow)["wizard_default"],
            "default_root": user.get("default_root") or cfg.workflow["project"]["default_root"],
            "profiles": [{"id": p.id, "label": p.label} for p in cfg.profiles.values()],
            "shorts": {p.id: p.short for p in cfg.profiles.values()},
            "settings": user,
            "home": str(Path.home()),
            "cameras": [{"key": c.key, "name": c.name, "brand": c.brand, "profiles": c.profiles, "user": c.user}
                        for c in cfg.cameras],
            "brands": sorted(cfg.brands),
            "catalog": self.catalog_info(),
            "recovery": self.recovery,
        }

    def preview(self, body: dict) -> dict:
        """Timeline names and deliveries for a format – shown live in the UI."""
        fmt = self._format(body)
        return {"timelines": [t["name"] for t in formats.timelines(self.cfg, fmt)],
                "deliveries": [d["label"] for d in formats.deliveries(self.cfg, fmt, "PROJECT")],
                "fits_free": formats.fits_free(self.cfg, fmt.width, fmt.height),
                "free_size": formats.clamp_free(self.cfg, fmt.width, fmt.height)}

    def validate(self, body: dict) -> dict:
        name = body.get("name", "")
        return {"normalized": naming.normalize(name), "problems": naming.validate_project_name(name)}

    def pick_folder(self, body: dict) -> dict:
        prompt = body.get("prompt", "Choose a folder").replace('"', "'")
        script = f'POSIX path of (choose folder with prompt "{prompt}")'
        res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, encoding="utf-8",
                             errors="replace")
        return {"path": res.stdout.strip().rstrip("/") if res.returncode == 0 else ""}

    # ------------------------------------------------------------------ scanning
    def start_scan(self, body: dict) -> dict:
        if self.scan_state["running"]:
            return {"ok": False, "error": "A scan is already running"}
        paths = [p for p in body.get("paths", []) if p]
        self.scan_state = {"running": True, "done": 0, "total": 0, "groups": [], "errors": [], "paths": paths}

        def work():
            try:
                def progress(i, n, _info):
                    self.scan_state.update(done=i, total=n)
                self.clips = scanner.scan(paths, self.cfg, progress=progress)
                self._regroup()
                self.scan_state["errors"] = [{"name": c.name, "error": c.error} for c in self.clips if c.error]
                self.scan_state["suggest"] = formats.suggest(self.cfg, self.clips, capabilities(self.resolve)["studio"])
                self.scan_state["size"] = sum(Path(c.path).stat().st_size for c in self.clips if not c.error)
            except Exception as e:  # noqa: BLE001
                self.scan_state["errors"] = [{"name": "Scan", "error": str(e)}]
            finally:
                self.scan_state["running"] = False

        threading.Thread(target=work, daemon=True).start()
        return {"ok": True}

    def _regroup(self):
        groups = scanner.group_clips(self.clips)
        cat = catalog.load()
        summaries = []
        for g in groups:
            s = g.summary(self.cfg)
            entry = catalog.match(self.cfg, g.camera_name, g.model, catalog=cat)
            s["thumb"] = entry.get("thumb", "") if entry else ""
            summaries.append(s)
        self.scan_state["groups"] = summaries
        self.scan_state["clip_count"] = sum(len(g.clips) for g in groups)

    # ------------------------------------------------------------------ flows
    def _start(self, steps, flow, *args) -> dict:
        if self.job and self.job.is_alive():
            return {"ok": False, "error": "Something is already running"}
        self.reporter = creator.Reporter(steps)
        self.job = threading.Thread(target=creator.run, args=(flow, self.resolve, self.cfg, *args),
                                    kwargs={"rep": self.reporter}, daemon=True)
        self.job.start()
        return {"ok": True}

    def start_create(self, body: dict) -> dict:
        return self._start(creator.NEW_PROJECT_STEPS, creator.new_project, self._plan(body))

    def start_add(self, body: dict) -> dict:
        return self._start(creator.ADD_FOOTAGE_STEPS, creator.add_footage, self._plan(body))

    def start_color(self, body: dict) -> dict:
        return self._start(creator.COLOR_STEPS, creator.refresh_color)

    def start_assign(self, body: dict) -> dict:
        return self._start(creator.ASSIGN_STEPS, creator.assign_all)

    def start_queue(self, body: dict) -> dict:
        return self._start(creator.QUEUE_STEPS, creator.queue_renders)

    def start_basic(self, body: dict) -> dict:
        from .basic import run as basic  # noqa: PLC0415 - numpy is only needed once Basic Correction runs
        options = {"dry_run": bool(body.get("dry_run")), "recompute": bool(body.get("recompute")),
                   "timeline": body.get("timeline", "")}
        return self._start(basic.STEPS, basic.flow, options)

    def start_edit(self, body: dict) -> dict:
        """Edit Assist; with pick_music the user chooses a music file for the rough cut first."""
        from .edit import decode, run as edit  # noqa: PLC0415
        if not decode.available():
            return {"ok": False, "error": "Edit Assist needs ffmpeg: install it with 'brew install ffmpeg' "
                                          "(https://brew.sh), then try again"}
        music = ""
        if body.get("pick_music"):
            res = subprocess.run(["osascript", "-e", 'POSIX path of (choose file with prompt "Music for the rough '
                                  'cut" of type {"public.audio"})'], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace")
            music = res.stdout.strip()
            if res.returncode != 0 or not music:
                return {"ok": False, "error": "No music chosen"}
        return self._start(edit.STEPS, edit.flow, {"music": music, "transcribe": bool(body.get("transcribe"))})

    def start_basic_reset(self, body: dict) -> dict:
        from .basic import run as basic  # noqa: PLC0415
        return self._start(basic.RESET_STEPS, basic.reset_flow, {"timeline": body.get("timeline", "")})

    def start_evaluate(self, body: dict) -> dict:
        from .basic import run as basic  # noqa: PLC0415
        return self._start(basic.EVALUATE_STEPS, basic.evaluate_flow, {"user_version": body.get("user_version", "")})

    def basic_report(self, q: dict) -> dict:
        """The last Basic Correction record of the current timeline, as report rows."""
        from .basic import run as basic, write  # noqa: PLC0415
        proj = self.resolve.GetProjectManager().GetCurrentProject()
        timeline = proj.GetCurrentTimeline() if proj else None
        if timeline is None:
            return {"timeline": "", "rows": []}
        record = write.load_record(creator.project_base(proj), timeline.GetName())
        return {"timeline": timeline.GetName(), "date": record.get("date", ""), "dry_run": record.get("dry_run"),
                "rows": basic.rows(record)}

    def _basic_entry(self, item_id: str) -> tuple[dict, object]:
        from .basic import write  # noqa: PLC0415
        proj = self.resolve.GetProjectManager().GetCurrentProject()
        timeline = proj.GetCurrentTimeline() if proj else None
        if timeline is None:
            return {}, None
        base = creator.project_base(proj)
        record = write.load_record(base, timeline.GetName())
        return next((e for e in record.get("items", []) if e.get("id") == item_id), {}), base

    def basic_clip(self, q: dict) -> dict:
        """Everything the record knows about one clip: what was measured, what was decided and why."""
        entry, _ = self._basic_entry((q.get("id") or [""])[0])
        if not entry:
            return {"ok": False, "error": "That clip isn't in the last report of this timeline"}
        keep = ("id", "name", "group", "source_start", "source_frames", "clip_fps", "frames", "meta", "measurement",
                "correction", "scene", "hero", "scene_notes", "outcome", "keyframes", "samples_over_time")
        return {"ok": True, **{k: entry.get(k) for k in keep}}

    def basic_preview(self, q: dict) -> bytes:
        """PNG: one frame of a clip before | after DAVIGEN_AUTO."""
        from .basic import preview  # noqa: PLC0415
        entry, base = self._basic_entry((q.get("id") or [""])[0])
        if not entry:
            raise FileNotFoundError("unknown clip")
        frames = entry.get("frames") or [entry.get("source_start", 0)]
        frame = int((q.get("frame") or [frames[len(frames) // 2]])[0])
        return preview.png(preview.before_after(entry, frame, base / "03_WORK" / "ANALYSIS"))

    def basic_look(self, q: dict) -> dict:
        """The project's look, the options with what each does, and sample clips of the last report to show them."""
        from .basic import settings as basic_settings, write  # noqa: PLC0415
        proj = self.resolve.GetProjectManager().GetCurrentProject()
        base = creator.project_base(proj)
        s = basic_settings.load(self.cfg.workflow, look=write.load_look(base))
        options = {dim: [{"name": k, "label": v["label"], "about": v["about"]} for k, v in s["looks"][dim].items()]
                   for dim in basic_settings.LOOK_DIMENSIONS}
        timeline = proj.GetCurrentTimeline()
        record = write.load_record(base, timeline.GetName()) if timeline else {}
        return {"look": s["look_applied"], "defaults": s["look"], "options": options,
                "samples": _look_samples(record)}

    def basic_look_save(self, body: dict) -> dict:
        from .basic import settings as basic_settings, write  # noqa: PLC0415
        proj = self.resolve.GetProjectManager().GetCurrentProject()
        base = creator.project_base(proj)
        look = {k: v for k, v in (body.get("look") or {}).items() if k in basic_settings.LOOK_DIMENSIONS}
        write.save_look(base, {**write.load_look(base), **look})
        return {"ok": True, "look": basic_settings.load(self.cfg.workflow, look=write.load_look(base))["look_applied"]}

    def basic_look_preview(self, q: dict) -> bytes:
        """PNG: a sample clip with one option of one look dimension (the others as the project has them)."""
        from .basic import preview, settings as basic_settings, write  # noqa: PLC0415
        with self.resolve_lock:
            entry, base = self._basic_entry((q.get("id") or [""])[0])
            look = write.load_look(base) if base else {}
        if not entry:
            raise FileNotFoundError("unknown clip")
        # the setup passes its choices so far (nothing is saved before the run starts)
        look = {**look, **{d: (q.get(d) or [""])[0] for d in basic_settings.LOOK_DIMENSIONS if q.get(d)}}
        dim, option = (q.get("dim") or [""])[0], (q.get("option") or [""])[0]
        width = min(max(int((q.get("w") or ["360"])[0] or 360), 120), 960)
        if option == "before":
            img = preview.look_preview(entry, base / "03_WORK" / "ANALYSIS", self.cfg.workflow, None, width)
        else:
            if dim in basic_settings.LOOK_DIMENSIONS:
                look = {**look, dim: option}
            img = preview.look_preview(entry, base / "03_WORK" / "ANALYSIS", self.cfg.workflow, look, width)
        return preview.png(img)

    def basic_learn(self, body: dict) -> dict:
        """Take the last 'Compare with my grade' of the current timeline over into the learned offsets."""
        from .basic import run as basic  # noqa: PLC0415
        proj = self.resolve.GetProjectManager().GetCurrentProject()
        learned = basic.learn_from_evaluation(creator.project_base(proj), proj.GetCurrentTimeline().GetName(),
                                              self.cfg)
        return {"ok": True, "learned": learned}

    def basic_goto(self, body: dict) -> dict:
        from .basic import run as basic  # noqa: PLC0415
        return {"ok": basic.goto(self.resolve, body.get("id", ""), body.get("timeline", ""))}

    def progress(self) -> dict:
        return self.reporter.snapshot() if self.reporter else {"steps": [], "done": False}

    def _format(self, body: dict) -> Format:
        f = body.get("format") or {}
        default = formats.default_format(self.cfg, capabilities(self.resolve)["studio"])
        width, height = int(f.get("width") or default.width), int(f.get("height") or default.height)
        return Format(width, height, float(f.get("fps") or default.fps),
                      f.get("aspect") or formats.aspect_of(self.cfg, width, height),
                      [d for d in f.get("deliveries", default.deliveries)])

    def _plan(self, body: dict) -> creator.Plan:
        """Build the plan from the scan result plus the user's choices (profiles, extras, format, transfer)."""
        chosen = {g["id"]: g for g in body.get("groups", []) if g.get("include", True)}
        groups: dict[tuple[str, str], creator.GroupPlan] = {}
        for g in scanner.group_clips(self.clips):
            if g.id not in chosen:
                continue
            profile = chosen[g.id].get("profile") or g.profile
            key = (g.camera_key, profile)
            plan_g = groups.setdefault(key, creator.GroupPlan(g.camera_key, g.camera_name, profile,
                                                              make=g.make, model=g.model))
            plan_g.clips += g.clips
        for extra in body.get("extra", []):
            key = (extra["camera_key"], extra["profile"])
            groups.setdefault(key, creator.GroupPlan(extra["camera_key"], extra["camera_name"], extra["profile"]))
        mode = body.get("transfer") if body.get("transfer") in (transfer.MOVE, transfer.COPY, transfer.LEAVE) \
            else self.cfg.workflow["project"]["transfer"]
        return creator.Plan(project=naming.normalize(body.get("project", "")),
                            root=body.get("root") or self.cfg.workflow["project"]["default_root"],
                            groups=list(groups.values()), fmt=self._format(body), transfer=mode,
                            basic_correction=bool(body.get("basic_correction")))

    # ------------------------------------------------------------------ open project
    def current(self) -> dict:
        """The project open in Resolve right now, and whether davigen manages it."""
        proj = self.resolve.GetProjectManager().GetCurrentProject()
        if proj is None:
            return {"name": "", "managed": False}
        try:
            base = creator.project_base(proj)
        except Exception:  # noqa: BLE001 - not a davigen project
            base = None
        info = filesystem.read_project_info(base) if base else {}
        if base and str(base) not in {p["folder"] for p in filesystem.recent_projects(check=False)}:
            # made by an older davigen or on another Mac: list it with the others
            filesystem.register_project(proj.GetName(), base, PM_FOLDER)
        specs = creator.project_groups(self.cfg, proj) if base else {}
        groups = []
        for g in proj.GetColorGroupsList() or []:
            pre = g.GetPreClipNodeGraph() if hasattr(g, "GetPreClipNodeGraph") else None
            spec = specs.get(g.GetName())
            entry = {"name": g.GetName(), "input_lut": bool(pre and pre.GetLUT(1))}
            if spec:
                profile = self.cfg.profiles[spec["profile"]]
                source = transforms.source_for(self.cfg, profile, spec["camera_key"], spec["camera_name"])
                entry.update(profile=profile.label, camera=spec["camera_name"], source=source.as_dict())
            groups.append(entry)
        return {"name": proj.GetName(), "managed": bool(base), "folder": str(base or ""), "groups": groups,
                "format": info.get("format"), "created": info.get("created", ""),
                "timelines": proj.GetTimelineCount(),
                "clips": len(creator.media_pool.davigen_clips(proj.GetMediaPool().GetRootFolder())) if base else 0}

    def projects(self) -> dict:
        current = self.resolve.GetProjectManager().GetCurrentProject()
        name = current.GetName() if current else ""
        return {"projects": [{**p, "open": p["name"] == name} for p in filesystem.recent_projects()]}

    def open_project(self, body: dict) -> dict:
        entry = next((p for p in filesystem.recent_projects() if p["folder"] == body.get("folder")), None)
        if entry is None:
            return {"ok": False, "error": "Unknown project"}
        pm = self.resolve.GetProjectManager()
        pm.SaveProject()
        if not find_project(pm, entry["name"], entry.get("pm_folder", "")):
            return {"ok": False, "error": f"'{entry['name']}' isn't in Resolve's project manager anymore"}
        ok = bool(pm.LoadProject(entry["name"]))
        return {"ok": ok, "error": "" if ok else "Resolve couldn't open the project"}

    def reveal(self, body: dict) -> dict:
        path = body.get("path", "")
        allowed = {p["folder"] for p in filesystem.recent_projects()} | {self.current().get("folder", "")}
        if path not in allowed or not Path(path).is_dir():
            return {"ok": False, "error": "Unknown folder"}
        creator.reveal(path)
        return {"ok": True}

    def source(self, q: dict) -> dict:
        profile = self.cfg.profiles.get((q.get("profile") or [""])[0])
        if profile is None:
            return {}
        return transforms.source_for(self.cfg, profile, (q.get("camera_key") or [""])[0],
                                     (q.get("camera_name") or [""])[0]).as_dict()

    def set_settings(self, body: dict) -> dict:
        allowed = {k: v for k, v in body.items() if k in ("online_sources", "default_root")}
        return save_settings(**allowed)

    def vendor_lut(self, body: dict) -> dict:
        """Let the user pick a LUT file for one group (last-resort source)."""
        specs = creator.project_groups(self.cfg, self.resolve.GetProjectManager().GetCurrentProject())
        spec = specs.get(body.get("group", ""))
        if spec is None:
            return {"ok": False, "error": "Unknown group"}
        profile = self.cfg.profiles[spec["profile"]]
        prompt = f"LUT file for {spec['camera_name']} · {profile.label} (log to Rec.709)".replace('"', "'")
        res = subprocess.run(["osascript", "-e", f'POSIX path of (choose file with prompt "{prompt}")'],
                             capture_output=True, text=True, encoding="utf-8", errors="replace")
        path = res.stdout.strip()
        if res.returncode != 0 or not path:
            return {"ok": False, "error": "No file chosen"}
        try:
            Lut3D.read(path)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"Not a valid 3D LUT: {e}"}
        providers.VENDOR_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, providers.VENDOR_DIR / f"{transforms.vendor_key(profile, spec['camera_key'])}.cube")
        return {"ok": True, "file": Path(path).name}

    # ------------------------------------------------------------------ camera catalog
    def catalog_info(self) -> dict:
        cat = catalog.load()
        return {"updated": cat.get("updated", ""), "count": len(cat["cameras"]),
                "photos": sum(1 for e in cat["cameras"] if e.get("thumb")),
                "needs_refresh": catalog.needs_refresh(), "state": self.catalog_state}

    def catalog_search(self, q: dict) -> dict:
        return {"results": catalog.search(self.cfg, (q.get("q") or [""])[0])}

    def catalog_refresh(self, body: dict) -> dict:
        if self.catalog_state["running"]:
            return {"ok": True}
        if not settings()["online_sources"]:
            return {"ok": False, "error": "Online sources are switched off"}
        self.catalog_state = {"running": True, "done": 0, "total": 0, "what": "starting", "error": ""}

        def work():
            try:
                catalog.refresh(lambda i, n, what: self.catalog_state.update(done=i, total=n, what=what))
            except Exception as e:  # noqa: BLE001
                self.catalog_state["error"] = str(e)
            finally:
                self.catalog_state["running"] = False

        threading.Thread(target=work, daemon=True).start()
        return {"ok": True}

    def add_camera(self, body: dict) -> dict:
        """Remember a camera (from the catalog or typed in) so its clips are recognised next time."""
        name = (body.get("name") or "").strip()
        if not name:
            return {"ok": False, "error": "Name missing"}
        brand = body.get("brand") or self.cfg.brand_of(name) or ""
        profiles = [p for p in body.get("profiles", []) if p in self.cfg.profiles] or \
            catalog.sort_profiles(self.cfg.profiles_for_brand(brand) if brand else list(self.cfg.profiles))
        model = [t for t in providers.tokens(name) if t not in catalog.brand_words(self.cfg)] or providers.tokens(name)
        cam = Camera(key=naming.normalize(name), name=name, brand=brand,
                     match=r"\W*".join(re.escape(t) for t in model) + r"(?![a-z0-9])",
                     profiles=profiles, image=body.get("thumb", ""))
        self.cfg.save_user_camera(cam)
        return {"ok": True, "camera": {"key": cam.key, "name": cam.name, "brand": cam.brand,
                                       "profiles": cam.profiles, "thumb": cam.image}}

    def busy(self) -> bool:
        return bool(self.scan_state.get("running") or (self.job and self.job.is_alive())
                    or self.catalog_state.get("running"))


def make_handler(app: App):
    routes_get = {
        "/api/info": lambda q: app.info(),
        "/api/scan": lambda q: app.scan_state,
        "/api/progress": lambda q: app.progress(),
        "/api/current": lambda q: app.current(),
        "/api/projects": lambda q: app.projects(),
        "/api/source": app.source,
        "/api/catalog": app.catalog_search,
        "/api/catalog/status": lambda q: app.catalog_info(),
        "/api/basic/report": app.basic_report,
        "/api/basic/clip": app.basic_clip,
        "/api/basic/look": app.basic_look,
    }
    routes_post = {
        "/api/validate": app.validate,
        "/api/preview": app.preview,
        "/api/pick-folder": app.pick_folder,
        "/api/scan": app.start_scan,
        "/api/create": app.start_create,
        "/api/add": app.start_add,
        "/api/color": app.start_color,
        "/api/assign": app.start_assign,
        "/api/queue": app.start_queue,
        "/api/basic": app.start_basic,
        "/api/basic/goto": app.basic_goto,
        "/api/basic/evaluate": app.start_evaluate,
        "/api/edit": app.start_edit,
        "/api/basic/learn": app.basic_learn,
        "/api/basic/look": app.basic_look_save,
        "/api/basic/reset": app.start_basic_reset,
        "/api/open-project": app.open_project,
        "/api/reveal": app.reveal,
        "/api/vendor-lut": app.vendor_lut,
        "/api/settings": app.set_settings,
        "/api/catalog/refresh": app.catalog_refresh,
        "/api/cameras": app.add_camera,
        "/api/heartbeat": lambda b: {"ok": True},
        "/api/quit": lambda b: (app.stop.set(), {"ok": True})[1],
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code: int, body: bytes, ctype: str = "application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, code: int = 200):
            self._send(code, json.dumps(data, ensure_ascii=False, default=str).encode())

        def _authorized(self) -> bool:
            return self.headers.get("X-Davigen-Token") == app.token

        def do_GET(self):
            url = urlparse(self.path)
            app.last_seen = time.time()
            if url.path in routes_get:
                if not self._authorized():
                    return self._json({"error": "forbidden"}, 403)
                try:
                    with app.resolve_lock if url.path in RESOLVE_ROUTES else _NOLOCK:
                        data = routes_get[url.path](parse_qs(url.query))
                    return self._json(data)
                except Exception as e:  # noqa: BLE001 - report instead of dropping the connection
                    return self._json({"error": f"{type(e).__name__}: {e}"}, 500)
            if url.path == "/basic/preview.png":
                # an <img> can't send the token header, so it comes as a query parameter here
                query = parse_qs(url.query)
                if (query.get("t") or [""])[0] != app.token:
                    return self._send(403, b"forbidden", "text/plain")
                try:
                    with app.resolve_lock:
                        return self._send(200, app.basic_preview(query), "image/png")
                except Exception as e:  # noqa: BLE001
                    return self._send(404, f"{type(e).__name__}: {e}".encode(), "text/plain")
            if url.path == "/basic/live.png":
                query = parse_qs(url.query)
                if (query.get("t") or [""])[0] != app.token:
                    return self._send(403, b"forbidden", "text/plain")
                png = app.reporter.images.get(int((query.get("n") or ["0"])[0] or 0)) if app.reporter else None
                return self._send(200, png, "image/png") if png else self._send(404, b"gone", "text/plain")
            if url.path == "/basic/look.png":
                query = parse_qs(url.query)
                if (query.get("t") or [""])[0] != app.token:
                    return self._send(403, b"forbidden", "text/plain")
                try:
                    return self._send(200, app.basic_look_preview(query), "image/png")
                except Exception as e:  # noqa: BLE001
                    return self._send(404, f"{type(e).__name__}: {e}".encode(), "text/plain")
            if url.path.startswith("/catalog/thumbs/"):
                thumb = catalog.thumb_path(url.path.rsplit("/", 1)[-1])
                if thumb is None:
                    return self._send(404, b"not found", "text/plain")
                return self._send(200, thumb.read_bytes(), "image/jpeg")
            name = "index.html" if url.path in ("/", "") else url.path.lstrip("/")
            target = (UI_DIR / name).resolve()
            if UI_DIR not in target.parents or not target.is_file():
                return self._send(404, b"not found", "text/plain")
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self._send(200, target.read_bytes(), ctype)

        def do_POST(self):
            url = urlparse(self.path)
            app.last_seen = time.time()
            if url.path not in routes_post:
                return self._json({"error": "unknown"}, 404)
            if not self._authorized():
                return self._json({"error": "forbidden"}, 403)
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            try:
                with app.resolve_lock if url.path in RESOLVE_ROUTES else _NOLOCK:
                    data = routes_post[url.path](body)
                self._json(data)
            except Exception as e:  # noqa: BLE001
                self._json({"ok": False, "error": str(e)}, 500)

    return Handler


_NOLOCK = contextlib.nullcontext()


class _LocalServer(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self):
        # HTTPServer.server_bind does a reverse DNS lookup (getfqdn) that can hang for seconds.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


def serve(resolve, cfg: Config | None = None, open_browser: bool = True, start: str = "") -> None:
    """start: a view to open right away ("basic" starts Basic Correction on the current timeline)."""
    app = App(resolve, cfg or Config())
    app.recovery = transfer.recover_pending()        # undo transfers a crash left unfinished
    httpd = _LocalServer(("127.0.0.1", 0), make_handler(app))
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/?token={app.token}" + (f"#{start}" if start else "")
    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "last_session.txt").write_text(url + "\n", encoding="utf-8")
    if open_browser:
        subprocess.run(["open", url], check=False)
    print(f"davigen is running at {url}")
    try:
        while not app.stop.is_set():
            time.sleep(1)
            if not app.busy() and time.time() - app.last_seen > IDLE_TIMEOUT:
                break
    finally:
        httpd.shutdown()


def _look_samples(record: dict, count: int = 4) -> list[dict]:
    """Clips that show a look well: the longest confident shot of the biggest scenes, one per camera group first."""
    items = [e for e in record.get("items", []) if e.get("correction") and e.get("frames")]
    items.sort(key=lambda e: (-(e.get("source_frames") or 0) / max(e.get("clip_fps") or 25.0, 1.0)))
    out, groups, scenes = [], set(), set()
    for rule in ("group", "scene", "any"):
        for e in items:
            if len(out) >= count or e in out:
                continue
            if rule == "group" and e.get("group") in groups:
                continue
            if rule == "scene" and e.get("scene") in scenes:
                continue
            out.append(e)
            groups.add(e.get("group"))
            scenes.add(e.get("scene"))
    return [{"id": e["id"], "name": e["name"], "group": e.get("group", "")} for e in out]
