"""The runner: turns chosen actions and assets into steps and runs them (concept §5, §6).

- Steps are built from the actions: ordered by `after`, and each asks `check` right before it runs, so only what
  is really left gets done (a step with nothing to do shows as skipped, with the reason).
- Every step reports n of m, the unit it works on, a fraction, and a time estimate that follows the real speed.
- An action with inputs that weren't given pauses the run at its step until the UI answers (or the run is stopped).
- Getting assets in (the `transactional` actions) is one transaction: if one of them fails or the run is stopped,
  every unit done so far is undone, newest first. Later actions keep what they finished.
- A journal is written before and after every unit, so a crash can be undone on the next start and the rest resumed.
"""

from __future__ import annotations

import json
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

from ..config import DATA_DIR
from .core import ASSET, DONE, PROJECT, STALE, TODO, Action, ActionError, Context, Progress, all_actions

PENDING = DATA_DIR / "pending_runs.json"
PENDING_STATES = ("pending", "input", "running")


class Stopped(Exception):
    """The user stopped the run."""


class Step:
    def __init__(self, action: Action):
        self.action = action
        self.state = "pending"               # pending | input | running | done | skipped | error | stopped
        self.done = self.total = 0
        self.already = 0                     # assets this action had done before (not redone)
        self.current = ""
        self.detail = ""
        self.fraction: float | None = None
        self.estimate = 0.0                  # seconds, first guess
        self.started = self.ended = 0.0
        self.errors: list[str] = []
        self.values: dict | None = None
        self.summary = ""

    def remaining(self) -> float:
        if self.state in ("done", "skipped", "error", "stopped"):
            return 0.0
        if self.state != "running" or not self.started:
            return self.estimate
        spent = time.time() - self.started
        if self.fraction:
            return max(0.0, spent / self.fraction - spent)
        if self.done and self.total:
            return max(0.0, spent / self.done * (self.total - self.done))
        return max(0.0, self.estimate - spent)

    def as_dict(self, ctx: Context) -> dict:
        a = self.action
        return {"id": a.id, "label": a.label, "about": a.about, "state": self.state, "done": self.done,
                "total": self.total, "already": self.already, "current": self.current,
                "detail": self.summary or self.detail, "fraction": self.fraction,
                "elapsed": round((self.ended or time.time()) - self.started, 1) if self.started else 0,
                "remaining": round(self.remaining(), 1), "errors": self.errors[-20:],
                "inputs": [i.as_dict() for i in a.inputs] if self.state == "input" else [], "form": a.form,
                "values": self.values if self.values is not None else (a.defaults(ctx) if a.inputs else {})}


class Run:
    """One run of the pipeline. `snapshot()` is what the UI polls; the rest runs in a thread."""

    def __init__(self, ctx: Context, action_ids: list[str], assets: list | None = None,
                 values: dict[str, dict] | None = None, redo: set[str] | None = None, title: str = ""):
        catalog = all_actions()
        self.ctx = ctx
        ctx.shared["run"] = self                     # actions with a live view (Basic correction) show through it
        self.title = title
        self.assets = list(assets or [])
        self.values = dict(values or {})
        self.redo = set(redo or ())
        self.steps = [Step(catalog[a]) for a in order(action_ids, catalog)]
        self.state = "pending"
        self.error = ""
        self.result: dict = {}
        self.started = time.time()
        self.ended = 0.0
        self.tx: list[tuple[Step, object, dict | None]] = []     # done units of the transaction, oldest first
        self.committed = False
        self.journal_file: Path | None = None
        self._stop = threading.Event()
        self._answer = threading.Event()
        self._thread: threading.Thread | None = None
        # the live pictures of Basic correction (same interface as creator.Reporter)
        self.live: list[dict] = []
        self.images: dict[int, bytes] = {}
        self._shown = 0

    # ----------------------------------------------------------------------------------------- the UI side
    @property
    def done(self) -> bool:
        return self.state not in ("pending", "running", "input")

    def snapshot(self) -> dict:
        return {"kind": "pipeline", "title": self.title, "state": self.state,
                "steps": [s.as_dict(self.ctx) for s in self.steps], "done": self.done, "error": self.error,
                "warnings": self.ctx.warnings, "manual": self.ctx.manual, "result": self.result, "live": self.live,
                "elapsed": round((self.ended or time.time()) - self.started, 1),
                "remaining": round(sum(s.remaining() for s in self.steps), 1),
                "assets": len(self.assets)}

    def provide(self, step_id: str, values: dict) -> bool:
        """The answer to a step waiting for input."""
        step = next((s for s in self.steps if s.action.id == step_id), None)
        if step is None or step.state != "input":
            return False
        known = {i.id for i in step.action.inputs}
        step.values = {**step.action.defaults(self.ctx), **{k: v for k, v in values.items() if k in known}}
        self.values[step_id] = step.values
        self._answer.set()
        return True

    def stop(self) -> None:
        self._stop.set()
        self._answer.set()

    def show(self, info: dict, png: bytes | None = None, keep: int = 12) -> None:
        self._shown += 1
        if png:
            self.images[self._shown] = png
        self.live = (self.live + [{**info, "n": self._shown, "image": bool(png)}])[-keep:]
        for n in [n for n in self.images if n <= self._shown - keep]:
            del self.images[n]

    def start(self) -> "Run":
        self._thread = threading.Thread(target=self.run, daemon=True)
        self._thread.start()
        return self

    def join(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    # ------------------------------------------------------------------------------------------- running
    def run(self) -> None:
        self.state = "running"
        try:
            self._estimate()
            for step in self.steps:
                if not step.action.transactional and self.tx and not self.committed:
                    self._commit()                   # everything is in: from here on, finished work stays
                self._step(step)
            if self.tx and not self.committed:
                self._commit()
            self.state = "done"
        except Stopped:
            self._fail("stopped", "Stopped. " + self._rollback_note())
        except ActionError as e:
            self._fail("failed", f"{e}" + self._rollback_note(prefix="\n\n"))
        except Exception as e:  # noqa: BLE001 - shown in the UI
            self._fail("failed", f"{e}\n\n{traceback.format_exc()}" + self._rollback_note(prefix="\n\n"))
        finally:
            if self.state in ("done", "failed", "stopped"):      # not after a crash: the journal stays open
                self.ended = time.time()
                for s in self.steps:
                    if s.state in ("pending", "input"):
                        s.state = "skipped" if self.state == "done" else "stopped"
                self._journal(final=True)

    def _fail(self, state: str, message: str) -> None:
        running = next((s for s in self.steps if s.state in ("running", "input")), None)
        if running:
            running.state = "stopped" if state == "stopped" else "error"
            running.ended = time.time()
        self.state = state
        self.error = message.strip()

    def _rollback_note(self, prefix: str = "") -> str:
        if not self.tx or self.committed:
            return ""
        undone, problems = self._rollback()
        note = f"Everything this run brought in was put back ({undone} steps undone)."
        if problems:
            note += " Needs a look: " + "; ".join(problems[:5])
        return prefix + note

    def _estimate(self) -> None:
        # an asset action that applies to none of this run's assets isn't a step at all
        self.steps = [s for s in self.steps if s.action.scope == PROJECT
                      or any(s.action.applies(self.ctx, a) for a in self.assets)]
        for step in self.steps:
            units, _ = self._units(step, planning=True)
            step.total = len(units)
            step.estimate = step.action.estimate(self.ctx, units) if units else 0.0

    def _units(self, step: Step, planning: bool = False) -> tuple[list, int]:
        """What this step still has to do, and how many assets it had done already."""
        a = step.action
        if a.scope == PROJECT:
            if not a.applies(self.ctx, None):
                return [], 0
            if a.id in self.redo:
                return [None], 0
            return ([None], 0) if a.check(self.ctx, None).needed else ([], 1)
        todo, already = [], 0
        for asset in self.assets:
            if not a.applies(self.ctx, asset):
                continue
            if a.id in self.redo or (planning and self.ctx.spec.get("new")):   # a new project: all of it
                todo.append(asset)
                continue
            state = a.check(self.ctx, asset).state
            if state in (TODO, STALE):
                todo.append(asset)
            elif state == DONE:
                already += 1
        return todo, already

    def _step(self, step: Step) -> None:
        a = step.action
        units, step.already = self._units(step)
        step.total = len(units)
        if not units:
            step.state = "skipped"
            step.summary = (f"already done ({step.already})" if step.already
                            else "already done" if a.scope == PROJECT and step.already else "nothing to do")
            return
        self._check_stop()
        values = self._values(step)
        step.state = "running"
        step.started = time.time()
        self._journal()
        a.prepare(self.ctx, units, values)
        progress = Progress(lambda d: self._progress(step, d))
        if a.batch:
            failed = a.run_batch(self.ctx, units, values, progress) or {}
            for asset in units:
                if asset is None:
                    continue
                if asset.id in failed:
                    step.errors.append(f"{asset.name}: {failed[asset.id]}")
                elif a.scope == ASSET:
                    self._mark(a, asset, values)
            step.done = len(units)
        else:
            for n, unit in enumerate(units):
                self._check_stop()
                step.current = unit.name if unit is not None else ""
                self._journal(step=a.id, unit=unit, phase="start")
                try:
                    info = a.run_one(self.ctx, unit, values, progress)
                except Stopped:
                    raise
                except Exception as e:  # noqa: BLE001 - decided by the action's on_error
                    if a.transactional or a.on_error == "stop" or unit is None:
                        raise
                    step.errors.append(f"{unit.name}: {e}")
                    self._journal(step=a.id, unit=unit, phase="failed")
                    step.done = n + 1
                    continue
                if a.transactional:
                    self.tx.append((step, unit, info))
                if unit is not None:
                    self._mark(a, unit, values)
                self._journal(step=a.id, unit=unit, phase="done", info=info)
                step.done = n + 1
        step.current = ""
        step.fraction = None
        step.summary = a.finish(self.ctx, units, values) or step.detail
        if step.errors:
            step.summary = (step.summary + " · " if step.summary else "") + f"{len(step.errors)} failed"
        step.state = "done"
        step.ended = time.time()

    def _values(self, step: Step) -> dict:
        a = step.action
        if not a.inputs:
            return {}
        if a.id in self.values:
            step.values = {**a.defaults(self.ctx), **self.values[a.id]}
            return step.values
        self._answer.clear()                         # before the UI can see the step waiting
        step.state = "input"
        self._journal()
        while not self._answer.wait(0.5) and not self._stop.is_set():
            pass
        self._check_stop()
        return step.values or a.defaults(self.ctx)

    def _mark(self, a: Action, asset, values: dict) -> None:
        asset.mark(a.id, values)
        if self.ctx.store is not None and a.scope == ASSET and self.ctx.store.get(asset.id) is not None:
            self.ctx.store.save()

    def _progress(self, step: Step, d: dict) -> None:
        if "done" in d:
            step.done = d["done"]
        if "current" in d:
            step.current = d["current"]
        if "fraction" in d:
            step.fraction = max(0.0, min(1.0, float(d["fraction"])))
        if "detail" in d:
            step.detail = d["detail"]

    def _check_stop(self) -> None:
        if self._stop.is_set():
            raise Stopped()

    # ------------------------------------------------------------------------------------- transaction
    def _commit(self) -> None:
        for action in {id(s.action): s.action for s, _, _ in self.tx}.values():
            commit = getattr(action, "commit", None)
            if commit:
                commit(self.ctx)
        self.committed = True
        self._journal()

    def _rollback(self) -> tuple[int, list[str]]:
        undone, problems = 0, []
        for step, unit, info in reversed(self.tx):
            try:
                step.action.undo_one(self.ctx, unit, info)
                undone += 1
                if unit is not None:
                    unit.unmark(step.action.id)
            except Exception as e:  # noqa: BLE001 - go on with the rest, report it
                problems.append(f"{step.action.label} · {getattr(unit, 'name', 'project')}: {e}")
        for action in {id(s.action): s.action for s, _, _ in self.tx}.values():
            abort = getattr(action, "abort", None)
            if abort:
                abort(self.ctx)
        self.tx = []
        self.committed = True
        return undone, problems

    # ----------------------------------------------------------------------------------------- journal
    def _journal(self, final: bool = False, **event) -> None:
        """The run's journal in the project (once the folder exists); pending runs are listed in data/."""
        base = self.ctx.base
        if base is None or not (base / "00_ADMIN").exists():
            return
        if self.journal_file is None:
            stamp = datetime.fromtimestamp(self.started).strftime("%Y%m%d_%H%M%S")
            self.journal_file = base / "00_ADMIN" / "PROJECT_INFO" / "runs" / f"run_{stamp}.json"
            _set_pending([*_pending(), str(self.journal_file)])
        data = {"title": self.title, "started": datetime.fromtimestamp(self.started).isoformat(timespec="seconds"),
                "state": self.state, "committed": self.committed or not self.tx,
                "actions": [s.action.id for s in self.steps], "assets": [a.id for a in self.assets],
                "values": self.values, "steps": {s.action.id: s.state for s in self.steps},
                "tx": [{"action": s.action.id, "asset": getattr(u, "id", None), "info": i} for s, u, i in self.tx],
                "error": self.error.split("\n\n")[0] if self.error else ""}
        if event:
            data["last"] = {k: (getattr(v, "id", None) if k == "unit" else v) for k, v in event.items()}
        from ..transfer import _write_json  # noqa: PLC0415
        _write_json(self.journal_file, data)
        if final:
            _set_pending([p for p in _pending() if p != str(self.journal_file)])


def order(action_ids: list[str], catalog: dict[str, Action]) -> list[str]:
    """Keep the given order, but every action after the ones it names in `after` (when they are in the run)."""
    chosen = [a for a in dict.fromkeys(action_ids) if a in catalog]
    out: list[str] = []
    visiting: set[str] = set()

    def visit(a: str) -> None:
        if a in out:
            return
        if a in visiting:
            raise ValueError(f"actions depend on each other in a circle: {a}")
        visiting.add(a)
        for dep in catalog[a].after:
            if dep in chosen:
                visit(dep)
        visiting.discard(a)
        out.append(a)

    for a in chosen:
        visit(a)
    return out


# --------------------------------------------------------------------------------------- after a crash

def _pending() -> list[str]:
    try:
        return json.loads(PENDING.read_text(encoding="utf-8")) if PENDING.exists() else []
    except (OSError, ValueError):
        return []


def _set_pending(paths: list[str]) -> None:
    from ..transfer import _write_json  # noqa: PLC0415
    _write_json(PENDING, sorted(set(paths)))


def recover(ctx_for) -> list[dict]:
    """Runs a crash left open: undo their transaction (newest first) and list them for the UI.

    ctx_for(base) returns a Context for the run's project. Files are also put back by the transfer journal
    (transfer.recover_pending runs first; both are idempotent)."""
    from .assets import AssetStore  # noqa: PLC0415
    catalog = all_actions()
    reports = []
    for p in _pending():
        path = Path(p)
        if not path.exists():
            _set_pending([x for x in _pending() if x != p])
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        base = path.parents[3]
        problems, undone = [], 0
        if not data.get("committed") and data.get("tx"):
            ctx = ctx_for(base)
            store = ctx.store or AssetStore(base)
            for entry in reversed(data["tx"]):
                action = catalog.get(entry["action"])
                asset = store.get(entry["asset"]) if entry.get("asset") else None
                if action is None:
                    continue
                try:
                    action.undo_one(ctx, asset, entry.get("info"))
                    undone += 1
                except Exception as e:  # noqa: BLE001
                    problems.append(f"{action.label}: {e}")
            for aid in {e["action"] for e in data["tx"]}:
                abort = getattr(catalog.get(aid), "abort", None)
                if abort:
                    abort(ctx)
            data["tx"], data["committed"] = [], True
        data["state"] = "interrupted"
        from ..transfer import _write_json  # noqa: PLC0415
        _write_json(path, data)
        _set_pending([x for x in _pending() if x != p])
        reports.append({"journal": str(path), "title": data.get("title", ""), "undone": undone,
                        "problems": problems, "folder": str(base)})
    return reports
