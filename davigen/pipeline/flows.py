"""Which actions a run is made of. The UI asks for a new project, an import into the open project, or one action
on assets that are already there; all three are the same pipeline with other building blocks."""

from __future__ import annotations

from .core import ASSET, all_actions

# the order a run lists its steps in (the runner still puts every action after the ones it depends on)
ORDER = ["project_folder", "resolve_project", "render_presets",
         "bring_in", "make_importable", "import_media",
         "assembly", "colour", "basic_correction", "song_markers", "collect",
         "save"]
SETUP = ["project_folder", "resolve_project", "render_presets"]


def _ordered(ids) -> list[str]:
    known = set(all_actions())
    ids = [i for i in dict.fromkeys(ids) if i in known]
    return sorted(ids, key=lambda i: ORDER.index(i) if i in ORDER else len(ORDER) - 1)


def mandatory() -> list[str]:
    return [a.id for a in all_actions().values() if a.scope == ASSET and a.mandatory]


def optional() -> list[dict]:
    """The actions a user can choose, with what they need to know (for the import screen and the assets page)."""
    return [a.describe() for a in (all_actions()[i] for i in ORDER if i in all_actions())
            if a.scope == ASSET and not a.mandatory]


def new_project(chosen: list[str]) -> list[str]:
    return _ordered([*SETUP, *mandatory(), *chosen, "save"])


def add(chosen: list[str]) -> list[str]:
    return _ordered([*mandatory(), *chosen, "save"])


def apply(chosen: list[str]) -> list[str]:
    """Actions on assets already in the project (the assets page): only these, then save."""
    return _ordered([*chosen, "save"])
