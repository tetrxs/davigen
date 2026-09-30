# 03 · Applying them to a new project

**Goal:** a new project gets every setting of step 02 through its route (API, template or preset). The `MANUAL`
lines for frame rate, working folders and proxies disappear from `creator.new_project` (concept §9).

**References:**

- concept §5, §9
- [`davigen/project.py`](../../../davigen/project.py) `create`, `apply_settings`
- [`davigen/creator.py`](../../../davigen/creator.py) `new_project`, `Reporter.warn`
- [`davigen/resolve_api.py`](../../../davigen/resolve_api.py) `open_pm_folder`
- the spike's answers on `ImportProject`, presets and the cache location (01)

## How it is developed

1. **Fake Resolve** (`tests/fake_resolve.py`): `ProjectManager.CreateProject`, `ImportProject(path, name)`,
   `ExportProject`, `GetProjectListInCurrentFolder`, folders; `Project.GetSetting()` without an argument returning
   all settings; `SetSetting` that refuses the keys the spike found refused; `GetPresetList`, `SetPreset`.
2. **Templates** (`davigen/templates.py`), test-first:
   - `template_path(fps, resolve_version)` → `data/templates/project_<fps>fps_r<major>.drp`
   - `status(fps)`: present, missing, from another Resolve version
   - `prepare(resolve, fps)`: creates `DAVIGEN_TEMPLATE_<fps>` in `DAVIGEN_TEMPLATES`, applies the API settings,
     returns the manual instructions
   - `finish(resolve, fps)`: reads the project; if the manual values are right, exports the template
3. **Creating:** `project.create` uses `ImportProject(template, name)` when a template exists, otherwise
   `CreateProject` (and presets, if the spike says so), then `project_settings.apply(project, desired)`: every
   API setting, read back, a structured result per setting instead of warning strings.
4. **Wizard:** the Review step asks the server whether the chosen frame rate has a template; if not, it offers the
   one-time setup before creating.
5. **Cache location:** as the spike decides; if the value doesn't survive a reload, `server.py` sets it again when
   davigen starts with a davigen project open.
6. **`creator.new_project`:** the three `MANUAL` lines go; the look-node and *Assign groups* hints move to a new
   `rep.tips` list. A read-only run of the check (step 04's reader) ends the flow.
7. **Docs:** README ("Left to do in Resolve", Troubleshooting row *Playback frame rate is wrong*), WORKFLOW §4 step 1
   and §9.

## How it is checked

- Tests: template status and paths; `create` with and without a template; every API setting read back; the
  manual list empty when everything took; refused keys reported with their route.
- **In Resolve:** create a project at 25 fps without a template (the one-time setup appears), finish the setup,
  create another 25 fps project (no manual step at all), save, quit Resolve, open it again, and check every setting
  in Project Settings by eye against the list in `report.txt`. Written down in this file under *Result*.
