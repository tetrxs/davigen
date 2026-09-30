# Plan: Project settings, proxies and media checks

Implements [docs/concepts/PROJECT_SETTINGS.md](../../concepts/PROJECT_SETTINGS.md). Each step is a separate,
reviewable change that ends with tests or a documented check in Resolve and leaves davigen working.

**Status:** the concept waits for review (open questions in §12). No step has started. Steps 02 onward are written
against the concept as it stands and are updated after the spike and your answers.

| # | Step | Needs Resolve | Depends on | Status |
|---|---|---|---|---|
| 01 | [API spike in Resolve](01_SPIKE.md) | yes | – | open |
| 02 | [Settings model and config](02_SETTINGS_MODEL.md) | no | 01 (key names) | open |
| 03 | [Applying them to a new project](03_NEW_PROJECT.md) | yes | 01, 02 | open |
| 04 | [Check & repair](04_CHECK_REPAIR.md) | yes | 02, 03 | open |
| 05 | [Making and linking proxies](05_PROXY_MAKE.md) | yes (linking) | 01 | open |
| 06 | [Proxy check: new, changed, orphaned](06_PROXY_RECONCILE.md) | yes (links) | 05 | open |
| 07 | [Media checks on import](07_MEDIA_CHECKS.md) | yes (notes) | 01 | open |
| 08 | [UI and menu entry](08_UI.md) | yes | 03–07 | open |
| 09 | [Run on MARSEILLE_2026](09_MARSEILLE.md) | yes | 01–08 | open |

Steps 02, 05 (planning, paths, guard, encode), 06 (the comparison itself) and 07 (the rules) are pure Python plus
ffmpeg and are built test-first without Resolve, with `tests/fake_resolve.py`. Steps 05 and 07 can start while 02–04
are in review, once the spike has answered the proxy questions.

**New module layout:**

```
davigen/
├── project_settings.py    target settings (desired) and applying them: API, template, preset     (02, 03)
├── templates.py           one-time project templates per frame rate                              (03)
├── health.py              Check & repair: read, report, diff, backup, journal, undo              (04)
├── trash.py               move files to the Trash through Finder (never delete)                  (04, 06)
├── media_checks.py        ffprobe pass, audio conversion, frame-rate notes, heavy codecs          (07)
└── proxy/
    ├── __init__.py
    ├── plan.py            which clips, ratio, paths, estimate, size guard, free space              (05)
    ├── make.py            ffmpeg encode, progress, stop, resume, check after encoding             (05)
    ├── record.py          00_ADMIN/PROJECT_INFO/proxies.json                                      (05)
    ├── link.py            LinkProxyMedia, reading Resolve's proxy state                           (05, 06)
    └── reconcile.py       ok / missing / stale / old spec / not linked / foreign / broken / orphan (06)
```

`project.py` keeps creating the project; it calls `project_settings` instead of looping over
`[project.settings]` itself. `creator.py` gets the new steps and loses the `MANUAL` lines that step 03 replaces.

**Conventions**, as in the rest of the repo:

- Commit messages: one plain sentence of what changed and why, no prefix (see `git log`).
- Every threshold and value lives in `config/workflow.toml`; missing keys fall back to defaults in code, so older
  user configs keep working.
- `README.md` and `docs/WORKFLOW.md` are updated in the step that changes what the user sees (03, 04, 05, 07, 08).
- Each session ends with a handover file in this folder (`HANDOVER_<date>.md`).
