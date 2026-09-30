# 02 · Settings model and config

**Goal:** one function computes the target settings of a project from the config, its format and its deliveries
(concept §4). Creating a project (03) and checking one (04) both use it.

**References:**

- concept §4 and §10
- [`davigen/formats.py`](../../../davigen/formats.py): `Format`, `deliveries`, `RESOLVE_FPS`
- [`davigen/project.py`](../../../davigen/project.py): `apply_settings`, `_same_number`
- [`davigen/config.py`](../../../davigen/config.py), [`config/workflow.toml`](../../../config/workflow.toml)
- the spike's result table (01)

## How it is developed

Test-first, no Resolve:

1. `tests/test_project_settings.py`: the four presets of concept §4.3 as parametrised cases; each asserts the full
   list of `(id, key, value, route)`.
2. `davigen/project_settings.py`:
   - `Setting` dataclass (id, key, value, source, route, compare, manual)
   - `desired(cfg, fmt, deliveries, studio, base) -> list[Setting]`
   - `compare(setting, actual) -> bool`: text, number (`"25"` = `"25.0"`), path (normalised, trailing slash)
   - the manual click path per setting, as text in the config next to the value, so it can be corrected without
     code
3. `config/workflow.toml`: `[project.derived]`, `[proxy]`, `[media_checks]` (proxy and media sections are read in 05
   and 07, but their defaults are defined here once). The routes per key come from the spike and live in the
   config too (`routes = { timelinePlaybackFrameRate = "template", … }`), so a later Resolve that accepts a key
   needs only a config change.
4. `config.py`: defaults for every new key, so a user config without them keeps working.

## How it is checked

- `pytest tests/test_project_settings.py` green, plus the whole suite.
- No value in `project_settings.py` that isn't from the config, the format or the deliveries (review: grep for
  literals).
- A test that the default config and the code defaults agree.
