"""The building blocks of every davigen run (docs/concepts/PROJECT_PIPELINE.md §4).

An Action is one thing that can happen to the project or to its assets – create the project folder, move a clip
in, set its colour group, give it a Basic correction, put markers on a song. Every action exists once and is used
the same way in the project setup and in any later import. The runner (runner.py) puts the chosen actions into
order, asks each one what is still to do, and runs them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

DONE, TODO, NA, STALE = "done", "todo", "n/a", "stale"
PROJECT, ASSET = "project", "asset"


class ActionError(RuntimeError):
    """A problem an action reports in plain words (shown to the user without a traceback)."""


@dataclass
class Option:
    value: str
    label: str
    about: str = ""


@dataclass
class Input:
    """One thing an action needs to know. The UI draws it from this description, or with a named custom form."""
    id: str
    label: str
    kind: str = "choice"            # choice | multi | toggle | number | text | custom
    options: list[Option] = field(default_factory=list)
    default: Any = None
    about: str = ""
    widget: str = ""                # kind == "custom": the name of the form in the UI (web/src/pipeline/widgets)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Check:
    state: str                      # done | todo | n/a | stale
    reason: str = ""

    @property
    def needed(self) -> bool:
        return self.state in (TODO, STALE)


class Progress:
    """What a running action reports: units done, the one it works on, a fraction for batch actions."""

    def __init__(self, sink: Callable[[dict], None] | None = None):
        self.sink = sink or (lambda _: None)

    def __call__(self, done: int | None = None, current: str | None = None, fraction: float | None = None,
                 detail: str | None = None) -> None:
        self.sink({k: v for k, v in {"done": done, "current": current, "fraction": fraction,
                                     "detail": detail}.items() if v is not None})


class Action:
    """Base class. Subclasses set the class attributes and override what they need."""

    id: str = ""
    label: str = ""
    about: str = ""
    scope: str = ASSET                              # project: runs once; asset: runs for assets of `kinds`
    kinds: frozenset[str] = frozenset()
    mandatory: bool = True                          # part of every import, or chosen by the user
    after: tuple[str, ...] = ()                     # actions that must run first (when they are in the run)
    inputs: tuple[Input, ...] = ()
    form: str = ""                                  # a custom form for all inputs in the UI (else drawn generically)
    transactional: bool = False                     # part of getting assets in: undone together if one fails
    on_error: str = "stop"                          # stop | skip (one asset fails, the others go on)
    batch: bool = False                             # run_batch once for all assets instead of run_one each
    seconds_per_unit: float = 0.5                   # first guess for the time estimate

    # --- what to do ------------------------------------------------------------------------------------------
    def applies(self, ctx: "Context", asset) -> bool:
        return asset is None if self.scope == PROJECT else asset.kind in self.kinds and not asset.removed

    def check(self, ctx: "Context", asset) -> Check:
        return Check(TODO)

    def defaults(self, ctx: "Context") -> dict:
        return {i.id: i.default for i in self.inputs}

    def estimate(self, ctx: "Context", assets: list) -> float:
        return self.seconds_per_unit * max(1, len(assets))

    def prepare(self, ctx: "Context", assets: list, values: dict) -> None:
        """Checks before anything is changed (free space, the right project is open). Raises ActionError."""

    # --- doing it --------------------------------------------------------------------------------------------
    def run_one(self, ctx: "Context", asset, values: dict, progress: Progress) -> dict | None:
        """Do the work for one asset (or the project). Returns what undo_one needs, or None."""
        raise NotImplementedError

    def undo_one(self, ctx: "Context", asset, info: dict | None) -> None:
        """Put one unit back after a failure (only what run_one did)."""

    def run_batch(self, ctx: "Context", assets: list, values: dict, progress: Progress) -> None:
        raise NotImplementedError

    def finish(self, ctx: "Context", assets: list, values: dict) -> str:
        """After all units: a one-line summary for the step."""
        return ""

    def describe(self) -> dict:
        return {"id": self.id, "label": self.label, "about": self.about, "scope": self.scope,
                "kinds": sorted(self.kinds), "mandatory": self.mandatory, "form": self.form,
                "inputs": [i.as_dict() for i in self.inputs]}


REGISTRY: dict[str, Action] = {}


def register(cls: type[Action]) -> type[Action]:
    """Class decorator: one instance per action id."""
    REGISTRY[cls.id] = cls()
    return cls


def _load() -> None:
    import importlib  # noqa: PLC0415
    importlib.import_module(f"{__package__}.actions")      # importing the package registers every action


def action(action_id: str) -> Action:
    _load()
    return REGISTRY[action_id]


def all_actions() -> dict[str, Action]:
    _load()
    return dict(REGISTRY)


@dataclass
class Context:
    """Everything an action may use. Project-level actions fill in base, project and fmt as they create them."""
    resolve: Any
    cfg: Any
    settings: dict = field(default_factory=dict)
    base: Path | None = None
    fmt: Any = None
    store: Any = None                               # assets.AssetStore of the project
    spec: dict = field(default_factory=dict)        # what the user chose: name, folder, camera groups …
    shared: dict = field(default_factory=dict)      # hand-over between actions of one run (Media Pool items …)
    warnings: list[str] = field(default_factory=list)
    manual: list[str] = field(default_factory=list)

    @property
    def project(self):
        return self.resolve.GetProjectManager().GetCurrentProject()

    def cached(self, key: str, build):
        """A value about the open Resolve project, kept for the run – but never across projects, and a missing
        thing (None) is looked up again next time (the setup creates it later in the same run)."""
        proj = self.project
        pid = (proj.GetUniqueId() if hasattr(proj, "GetUniqueId") else id(proj)) if proj is not None else None
        hit = self.shared.get(key)
        if hit is not None and hit[0] == pid:
            return hit[1]
        value = build() if proj is not None else None
        if value is not None:
            self.shared[key] = (pid, value)
        return value

    def forget(self, *keys: str) -> None:
        for key in keys:
            self.shared.pop(key, None)

    def warn(self, *items: str) -> None:
        for w in items:
            target = self.manual if w.startswith("MANUAL: ") else self.warnings
            text = w.removeprefix("MANUAL: ")
            if text and text not in target:
                target.append(text)
