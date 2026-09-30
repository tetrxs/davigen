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

from . import __version__, catalog, creator, filesystem, formats, naming, posters, providers, scanner, transfer, transforms
from . import delete, update
from .pipeline import assets as pl_assets, flows as pl_flows, runner as pl_runner, state as pl_state
from .pipeline.core import Context
from .config import DATA_DIR, Camera, Config, save_settings, settings
from .formats import Format
from .project import PM_FOLDER
from .basic import settings as basic_settings
from .lut import Lut3D
from .resolve_api import capabilities, find_project, version

UI_DIR = Path(__file__).resolve().parent / "ui"
# Resolve's scripting API isn't thread-safe: requests that talk to Resolve run one at a time.
# (Background flows use Resolve from their own thread; the UI doesn't query Resolve while one runs.)
RESOLVE_ROUTES = {"/api/timeline/open", "/api/info", "/api/current", "/api/projects", "/api/open-project",
                  "/api/vendor-lut", "/api/preview", "/api/scan", "/api/basic/report", "/api/basic/goto",
                  "/api/basic/learn", "/api/basic/clip", "/api/basic/look", "/api/assets", "/api/create", "/api/add",
                  "/api/apply", "/api/project/delete"}
TRANSFER_MODES = (transfer.MOVE, transfer.COPY, transfer.LINK, transfer.LEAVE)
IDLE_TIMEOUT = 600         # seconds without any browser tab before davigen ends itself (no job running)


class App:
    def __init__(self, resolve, cfg: Config):
        self.resolve, self.cfg = resolve, cfg
        self.token = secrets.token_urlsafe(16)
        self.last_seen = time.time()
        self.scan_state: dict = {"running": False, "done": 0, "total": 0, "groups": [], "errors": []}
        self.clips: list[scanner.ClipInfo] = []
        self.assets: list[pl_assets.Asset] = []          # the last scan, classified
        self.reporter: creator.Reporter | pl_runner.Run | None = None
        self.job: threading.Thread | None = None
        self.stop = threading.Event()
        self.catalog_state: dict = {"running": False, "done": 0, "total": 0, "what": "", "error": ""}
        self.recovery: list[dict] = []
        self.run_recovery: list[dict] = []
        self.resolve_lock = threading.RLock()       # re-entrant: a route may call a helper that locks too
        self.updater = update.Updater()
        self._records: dict[Path, tuple[float, dict]] = {}
        # before | after pictures: made one at a time, outside the Resolve lock, the newest request first
        self._render_lock = threading.Lock()
        self._render_gen = 0
        self._pictures: dict[tuple, bytes] = {}

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
            "transfer": self.transfer_mode(),
            "basic_default": basic_settings.load(cfg.workflow)["wizard_default"],
            "actions": pl_flows.optional(),
            "defaults": {"actions": self.default_actions(), "song_markers": user.get("song_markers")},
            "kinds": {k: v["label"] for k, v in cfg.workflow["assets"]["kinds"].items()},
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
            "run_recovery": self.run_recovery,
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

    def pick_files(self, body: dict) -> dict:
        """Several video files at once (Finder's multiple selection)."""
        prompt = body.get("prompt", "Choose clips").replace('"', "'")
        script = (f'set fs to choose file with prompt "{prompt}" with multiple selections allowed\n'
                  'set out to ""\nrepeat with f in fs\nset out to out & POSIX path of f & linefeed\nend repeat\n'
                  'return out')
        res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, encoding="utf-8",
                             errors="replace")
        return {"paths": [line for line in res.stdout.splitlines() if line.strip()] if res.returncode == 0 else []}

    # ------------------------------------------------------------------ scanning
    def start_scan(self, body: dict) -> dict:
        """Look at every file under the chosen files and folders: camera clips grouped by camera and profile, every
        other kind listed. Files the open project already has are marked (an import skips them)."""
        if self.scan_state["running"]:
            return {"ok": False, "error": "A scan is already running"}
        paths = [p for p in body.get("paths", []) if p]
        self.scan_state = {"running": True, "done": 0, "total": 0, "groups": [], "kinds": [], "errors": [],
                           "paths": paths}
        known = self._known_ids() if body.get("mode") == "add" else set()

        def work():
            try:
                def progress(i, n):
                    self.scan_state.update(done=i, total=n)
                self.assets, self.clips, errors = pl_assets.classify(paths, self.cfg, progress=progress)
                self._regroup()
                self.scan_state["kinds"] = pl_assets.summary([a for a in self.assets if a.kind != "camera"],
                                                             self.cfg)
                self.scan_state["errors"] = errors
                cams = [c for c in self.clips if c.camera_key != "UNKNOWN_CAMERA"]
                self.scan_state["suggest"] = formats.suggest(self.cfg, cams or self.clips,
                                                             capabilities(self.resolve)["studio"])
                self.scan_state["size"] = sum(a.size for a in self.assets)
                self.scan_state["count"] = len(self.assets)
                self.scan_state["known"] = sorted(a.id for a in self.assets if a.id in known)
            except Exception as e:  # noqa: BLE001
                self.scan_state["errors"] = [{"name": "Scan", "error": str(e)}]
            finally:
                self.scan_state["running"] = False

        threading.Thread(target=work, daemon=True).start()
        return {"ok": True}

    def _known_ids(self) -> set[str]:
        """Assets the open project has (called from /api/scan, which holds the Resolve lock)."""
        try:
            base = creator.project_base(self.resolve.GetProjectManager().GetCurrentProject())
            return {a.id for a in pl_assets.AssetStore(base) if not a.removed}
        except Exception:  # noqa: BLE001 - no davigen project open
            return set()

    def _regroup(self):
        clips = [c for c in self.clips if c.camera_key != "UNKNOWN_CAMERA"]
        groups = scanner.group_clips(clips)
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
        if self.busy_job():
            return {"ok": False, "error": "Something is already running"}
        self.reporter = creator.Reporter(steps)
        self.job = threading.Thread(target=creator.run, args=(flow, self.resolve, self.cfg, *args),
                                    kwargs={"rep": self.reporter}, daemon=True)
        self.job.start()
        return {"ok": True}

    def _run(self, ctx: Context, actions: list[str], assets: list, values: dict, redo=(), title: str = "") -> dict:
        """Start a pipeline run in the background (the UI polls /api/progress)."""
        if self.busy_job():
            return {"ok": False, "error": "Something is already running"}
        run = pl_runner.Run(ctx, actions, assets, values=values, redo=set(redo), title=title)
        self.reporter = run
        self.job = run.start()._thread
        return {"ok": True}

    def busy_job(self) -> bool:
        return bool(self.job and self.job.is_alive())

    def transfer_mode(self) -> str:
        mode = settings().get("transfer") or self.cfg.workflow["project"]["transfer"]
        return mode if mode in TRANSFER_MODES else transfer.MOVE

    def default_actions(self) -> list[str]:
        chosen = settings().get("default_actions")
        if isinstance(chosen, list):
            return chosen
        return ["basic_correction"] if basic_settings.load(self.cfg.workflow)["wizard_default"] else []

    def _context(self, base: Path | None = None, fmt: Format | None = None, spec: dict | None = None) -> Context:
        """A pipeline context for the open project (or, with base/fmt/spec, for a new one)."""
        user = {**settings(), "transfer": self.transfer_mode()}
        if base is None:
            proj = self.resolve.GetProjectManager().GetCurrentProject()
            base = creator.project_base(proj)
            fmt = creator.project_format(self.cfg, proj, base)
        return Context(resolve=self.resolve, cfg=self.cfg, settings=user, base=base, fmt=fmt,
                       store=pl_assets.AssetStore(base), spec=spec or {})

    def _chosen(self, body: dict) -> tuple[list[str], dict]:
        """The optional actions the user ticked, and every input given for them."""
        optional = {a["id"] for a in pl_flows.optional()}
        chosen = [a for a in body.get("actions", []) if a in optional]
        values = {k: v for k, v in (body.get("values") or {}).items() if k in chosen and isinstance(v, dict)}
        return chosen, values

    def _scanned(self, body: dict) -> list[pl_assets.Asset]:
        """The scanned assets as the user left them: kinds changed, files or camera groups left out, profiles."""
        groups = {g["id"]: g for g in body.get("groups", [])}
        kinds = {k: v for k, v in (body.get("kinds") or {}).items() if v in pl_assets.KINDS}
        skip = set(body.get("exclude") or [])
        out = []
        for a in self.assets:
            if a.id in skip:
                continue
            asset = pl_assets.Asset.from_dict(a.as_dict())
            asset.kind = kinds.get(a.id, a.kind)
            if asset.kind == "camera":
                g = groups.get(f"{a.camera_key}:{a.profile}", {})
                if g and not g.get("include", True):
                    continue
                profile = g.get("profile") or a.profile
                if profile not in self.cfg.profiles:
                    profile = "REC709"
                asset.profile = profile
                if not asset.camera_key or asset.camera_key == "UNKNOWN_CAMERA":
                    asset.camera_key, asset.camera_name = "UNKNOWN_CAMERA", asset.camera_name or "Unknown camera"
                asset.group = naming.group_name(asset.camera_key, self.cfg.profiles[profile].short)
            out.append(asset)
        return out

    def start_create(self, body: dict) -> dict:
        name = naming.normalize(body.get("project", ""))
        if not name:
            return {"ok": False, "error": "Project name missing"}
        root = body.get("root") or settings().get("default_root") or self.cfg.workflow["project"]["default_root"]
        base = filesystem.project_dir(root, name)
        if filesystem.info_file(base).exists():
            return {"ok": False, "error": f"{base} already contains a davigen project"}
        studio = capabilities(self.resolve)["studio"]
        fmt = self._format(body)
        if not studio and not formats.fits_free(self.cfg, fmt.width, fmt.height):
            fmt.width, fmt.height = formats.clamp_free(self.cfg, fmt.width, fmt.height)
        chosen, values = self._chosen(body)
        ctx = self._context(base, fmt, {"new": True, "name": name})
        return self._run(ctx, pl_flows.new_project(chosen), self._scanned(body), values, title=f"New project {name}")

    def start_add(self, body: dict) -> dict:
        """Import into the open project: only what isn't there yet; known files keep what was done to them."""
        chosen, values = self._chosen(body)
        ctx = self._context()
        pl_state.adopt(ctx)
        pl_state.reconcile(ctx)
        assets = []
        for a in self._scanned(body):
            known = ctx.store.get(a.id)
            if known is not None:
                known.removed = False                     # brought in again after it was removed in Resolve
                assets.append(known)
            else:
                assets.append(a)
        return self._run(ctx, pl_flows.add(chosen), assets, values, title="Add to the project")

    def start_apply(self, body: dict) -> dict:
        """One or more actions on assets already in the project (the assets page), e.g. Basic correction for all."""
        known = {a["id"] for a in pl_flows.optional()} | {"colour", "import_media", "make_importable", "assembly"}
        chosen = [a for a in body.get("actions", []) if a in known]
        if not chosen:
            return {"ok": False, "error": "No action chosen"}
        values = {k: v for k, v in (body.get("values") or {}).items() if k in chosen and isinstance(v, dict)}
        ctx = self._context()
        pl_state.adopt(ctx)
        pl_state.reconcile(ctx)
        wanted = set(body.get("assets") or [])
        assets = [a for a in ctx.store if not a.removed and (not wanted or a.id in wanted)]
        redo = [a for a in body.get("redo", []) if a in chosen]
        return self._run(ctx, pl_flows.apply(chosen), assets, values, redo=redo,
                         title=", ".join(pl_runner.all_actions()[a].label for a in chosen))

    def run_input(self, body: dict) -> dict:
        run = self.reporter
        if not isinstance(run, pl_runner.Run):
            return {"ok": False, "error": "Nothing is waiting"}
        return {"ok": run.provide(body.get("step", ""), body.get("values") or {})}

    def run_stop(self, body: dict) -> dict:
        run = self.reporter
        if isinstance(run, pl_runner.Run) and not run.done:
            run.stop()
            return {"ok": True}
        return {"ok": False, "error": "Nothing to stop"}

    def assets_list(self, q: dict) -> dict:
        """Every asset of the open project with a status per action (the assets page)."""
        try:
            ctx = self._context()
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e), "assets": [], "actions": []}
        live = not self.busy_job()                     # while a run works in Resolve: from the record only
        if live:
            pl_state.adopt(ctx)
            pl_state.reconcile(ctx)
        return {"ok": True, "live": live, **pl_state.table(ctx, live=live)}

    def delete_project(self, body: dict) -> dict:
        entry = next((p for p in filesystem.recent_projects(check=False) if p["folder"] == body.get("folder")), None)
        if entry is None:
            return {"ok": False, "error": "Unknown project"}
        if body.get("confirm") != entry["name"]:
            return {"ok": False, "error": "Type the project name to confirm"}
        return self._start(delete.STEPS, delete.flow, {"entry": entry})

    def start_color(self, body: dict) -> dict:
        return self._start(creator.COLOR_STEPS, creator.refresh_color)

    def start_assign(self, body: dict) -> dict:
        return self._start(creator.ASSIGN_STEPS, creator.assign_all)

    def start_queue(self, body: dict) -> dict:
        return self._start(creator.QUEUE_STEPS, creator.queue_renders)

    def start_basic(self, body: dict) -> dict:
        from .basic import run as basic  # noqa: PLC0415 - numpy is only needed once Basic Correction runs
        options = {"dry_run": bool(body.get("dry_run")), "recompute": bool(body.get("recompute")),
                   "timeline": body.get("timeline", ""), "spread": bool(body.get("spread"))}
        return self._start(basic.STEPS, basic.flow, options)

    def open_timeline(self, body: dict) -> dict:
        """Show one of davigen's timelines on Resolve's Edit page."""
        from .resolve_api import find_timeline  # noqa: PLC0415
        name = body.get("name", "")
        if not re.match(r"^TL_\w+$", name):
            return {"ok": False, "error": "Not a davigen timeline"}
        proj = self.resolve.GetProjectManager().GetCurrentProject()
        tl = find_timeline(proj, name)
        if tl is None:
            return {"ok": False, "error": f"{name} isn't in this project anymore"}
        ok = bool(proj.SetCurrentTimeline(tl))
        self.resolve.OpenPage("edit")
        return {"ok": ok}

    def start_basic_carry(self, body: dict) -> dict:
        from .basic import carry  # noqa: PLC0415
        return self._start(carry.STEPS, carry.flow, {"refresh": bool(body.get("refresh"))})

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
        proj = self.resolve.GetProjectManager().GetCurrentProject()
        timeline = proj.GetCurrentTimeline() if proj else None
        if timeline is None:
            return {}, None
        base = creator.project_base(proj)
        record = self._record(base, timeline.GetName())
        return next((e for e in record.get("items", []) if e.get("id") == item_id), {}), base

    def _record(self, base: Path, timeline_name: str) -> dict:
        """The Basic correction record of a timeline, read again only when the file changed."""
        from .basic import write  # noqa: PLC0415
        target = base / "00_ADMIN" / "PROJECT_INFO" / "basic_correction" / f"{timeline_name}.json"
        stamp = target.stat().st_mtime if target.exists() else 0.0
        if self._records.get(target, (None,))[0] != stamp:
            self._records = {target: (stamp, write.load_record(base, timeline_name))}
        return self._records[target][1]

    def basic_thumb(self, q: dict) -> bytes:
        """Small PNG of a clip of the last report, graded, from the analysis cache (the report's film strip)."""
        with self.resolve_lock:
            entry, base = self._basic_entry((q.get("id") or [""])[0])
        if not entry:
            raise FileNotFoundError("unknown clip")
        return posters.thumb(entry, base)

    # ------------------------------------------------------------------ pictures and updates
    def _known_folder(self, folder: str) -> Path | None:
        known = {p["folder"] for p in filesystem.recent_projects(check=False)}
        return Path(folder) if folder in known and Path(folder).is_dir() else None

    def project_posters(self, q: dict) -> dict:
        base = self._known_folder((q.get("folder") or [""])[0])
        if base is None:
            return {"posters": []}
        return {"posters": [{"index": i, "name": s["name"], "group": s["group"]}
                            for i, s in enumerate(posters.sources(base))]}

    def project_poster(self, q: dict) -> bytes:
        base = self._known_folder((q.get("folder") or [""])[0])
        if base is None:
            raise FileNotFoundError("unknown project")
        width = min(max(int((q.get("w") or ["640"])[0] or 640), 160), 1280)
        return posters.poster(base, int((q.get("i") or ["0"])[0] or 0), width)

    def update_run(self, body: dict) -> dict:
        if self.busy():
            return {"ok": False, "error": "Wait until the running job is finished"}
        return self.updater.run()

    def basic_clip(self, q: dict) -> dict:
        """Everything the record knows about one clip: what was measured, what was decided and why."""
        entry, _ = self._basic_entry((q.get("id") or [""])[0])
        if not entry:
            return {"ok": False, "error": "That clip isn't in the last report of this timeline"}
        keep = ("id", "name", "group", "source_start", "source_frames", "clip_fps", "frames", "meta", "measurement",
                "correction", "scene", "hero", "scene_notes", "outcome", "keyframes", "samples_over_time")
        return {"ok": True, **{k: entry.get(k) for k in keep}}

    def basic_preview(self, q: dict) -> bytes:
        """PNG: one frame of a clip before | after DAVIGEN_AUTO.

        Scrubbing asks for many frames quickly. Each picture decodes a camera frame, so they are made one at a
        time and outside the Resolve lock (the rest of the UI keeps answering); a request that a newer one has
        overtaken while it waited gives up (Superseded), and finished pictures are kept for going back."""
        from .basic import preview  # noqa: PLC0415
        with self.resolve_lock:
            entry, base = self._basic_entry((q.get("id") or [""])[0])
        if not entry:
            raise FileNotFoundError("unknown clip")
        frames = entry.get("frames") or [entry.get("source_start", 0)]
        frame = int((q.get("frame") or [frames[len(frames) // 2]])[0])
        key = (entry.get("path"), frame, json.dumps(entry.get("correction", {}).get("nodes"), sort_keys=True))
        if key in self._pictures:
            return self._pictures[key]
        self._render_gen += 1
        mine = self._render_gen
        with self._render_lock:
            if mine != self._render_gen:
                raise Superseded("a newer picture was asked for")
            png = preview.png(preview.before_after(entry, frame, base / "03_WORK" / "ANALYSIS"))
        if len(self._pictures) > 48:
            self._pictures.pop(next(iter(self._pictures)))
        self._pictures[key] = png
        return png

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
                "samples": _look_samples(record), "status": _basic_status(timeline, record)}

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
        cat = catalog.load()
        groups = []
        for g in proj.GetColorGroupsList() or []:
            pre = g.GetPreClipNodeGraph() if hasattr(g, "GetPreClipNodeGraph") else None
            spec = specs.get(g.GetName())
            entry = {"name": g.GetName(), "input_lut": bool(pre and pre.GetLUT(1))}
            if spec:
                profile = self.cfg.profiles[spec["profile"]]
                source = transforms.source_for(self.cfg, profile, spec["camera_key"], spec["camera_name"])
                photo = catalog.match(self.cfg, spec["camera_name"], catalog=cat) or {}
                entry.update(profile=profile.label, camera=spec["camera_name"], source=source.as_dict(),
                             thumb=photo.get("thumb", ""), brand=self.cfg.brand_of(spec["camera_name"]) or "")
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
        allowed = {k: v for k, v in body.items()
                   if k in ("online_sources", "default_root", "transfer", "default_actions", "song_markers")}
        if "transfer" in allowed and allowed["transfer"] not in TRANSFER_MODES:
            allowed.pop("transfer")
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
        "/api/project/posters": app.project_posters,
        "/api/update": lambda q: app.updater.snapshot(),
        "/api/assets": app.assets_list,
    }
    routes_post = {
        "/api/validate": app.validate,
        "/api/preview": app.preview,
        "/api/pick-folder": app.pick_folder,
        "/api/pick-files": app.pick_files,
        "/api/scan": app.start_scan,
        "/api/create": app.start_create,
        "/api/add": app.start_add,
        "/api/color": app.start_color,
        "/api/assign": app.start_assign,
        "/api/queue": app.start_queue,
        "/api/basic": app.start_basic,
        "/api/basic/goto": app.basic_goto,
        "/api/basic/evaluate": app.start_evaluate,
        "/api/apply": app.start_apply,
        "/api/run/input": app.run_input,
        "/api/run/stop": app.run_stop,
        "/api/project/delete": app.delete_project,
        "/api/basic/learn": app.basic_learn,
        "/api/basic/look": app.basic_look_save,
        "/api/basic/reset": app.start_basic_reset,
        "/api/basic/carry": app.start_basic_carry,
        "/api/open-project": app.open_project,
        "/api/reveal": app.reveal,
        "/api/vendor-lut": app.vendor_lut,
        "/api/settings": app.set_settings,
        "/api/catalog/refresh": app.catalog_refresh,
        "/api/cameras": app.add_camera,
        "/api/update/check": lambda b: app.updater.check(),
        "/api/timeline/open": app.open_timeline,
        "/api/update/run": app.update_run,
        "/api/heartbeat": lambda b: {"ok": True},
        "/api/quit": lambda b: (app.stop.set(), {"ok": True})[1],
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def handle(self):
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                super().handle()

        def _send(self, code: int, body: bytes, ctype: str = "application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):   # the page moved on
                self.wfile.write(body)

        def _send_file(self, path: Path, ctype: str):
            """A file with byte ranges, so a <video> can seek."""
            size = path.stat().st_size
            match = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range") or "")
            start, end = 0, size - 1
            if match and (match.group(1) or match.group(2)):
                if match.group(1):
                    start = int(match.group(1))
                    end = int(match.group(2)) if match.group(2) else size - 1
                else:
                    start = max(0, size - int(match.group(2)))
                end = min(end, size - 1)
            self.send_response(206 if match else 200)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            if match:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            with path.open("rb") as fh, contextlib.suppress(BrokenPipeError, ConnectionResetError):
                fh.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = fh.read(min(1 << 20, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)

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
                    return self._send(200, app.basic_preview(query), "image/png")
                except Superseded:
                    return self._send(409, b"superseded", "text/plain")
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
            if url.path in ("/project/poster.png", "/basic/thumb.png"):
                query = parse_qs(url.query)
                if (query.get("t") or [""])[0] != app.token:
                    return self._send(403, b"forbidden", "text/plain")
                make = {"/project/poster.png": app.project_poster, "/basic/thumb.png": app.basic_thumb}[url.path]
                try:
                    return self._send(200, make(query), "image/png")
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


class Superseded(Exception):
    """A picture nobody waits for anymore."""


class _LocalServer(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self):
        # HTTPServer.server_bind does a reverse DNS lookup (getfqdn) that can hang for seconds.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


def serve(resolve, cfg: Config | None = None, open_browser: bool = True, start: str = "") -> None:
    """start: a view to open right away ("basic" starts Basic Correction on the current timeline)."""
    app = App(resolve, cfg or Config())
    app.recovery = transfer.recover_pending()        # undo transfers a crash left unfinished …
    try:                                               # … and imports of pipeline runs (Resolve side)
        app.run_recovery = pl_runner.recover(lambda base: Context(resolve=resolve, cfg=app.cfg, base=base,
                                                                  store=pl_assets.AssetStore(base)))
    except Exception as e:  # noqa: BLE001 - never keep davigen from starting
        app.run_recovery = [{"title": "Recovery", "undone": 0, "problems": [str(e)], "folder": ""}]
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
    """Clips that show a look well: the longest shot of the biggest scenes, one per camera group first."""
    return [{"id": e["id"], "name": e["name"], "group": e.get("group", "")} for e in posters.pick(record, count)]


def _basic_status(timeline, record: dict) -> dict:
    """How far the current timeline is: clips, clips with DAVIGEN_AUTO, and the look of the last run."""
    from .basic import run as basic, write  # noqa: PLC0415
    if timeline is None:
        return {"timeline": "", "clips": 0, "corrected": 0, "last_look": {}}
    items = [ti for ti in basic._video_items(timeline) if ti.GetMediaPoolItem()]
    corrected = sum(1 for ti in items if write.AUTO in (ti.GetVersionNameList(0) or []))
    return {"timeline": timeline.GetName(), "clips": len(items), "corrected": corrected,
            "last_look": record.get("look", {}), "last_run": record.get("date", "")}
