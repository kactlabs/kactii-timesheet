# Project

Python project using FastAPI.

## Setup

- Python environment: conda `test12`
- Entry point: `python app.py`

## Install Dependencies

```bash
/opt/miniconda3/envs/test12/bin/pip install -r requirements.txt
```

## Run

```bash
/opt/miniconda3/envs/test12/bin/python app.py
```

## Kiro Steering Rules

Steering files live in `.kiro/steering/` and provide persistent guidance to Kiro across all interactions.

| File | Inclusion | Summary |
|------|-----------|---------|
| `always.md` | always | General project rules — no hardcoded fallbacks, use `requirements.txt`, FastAPI entry point is `app.py`, use `randum` for random data. Also defines acronyms: SS, reqs, MOC, MON. |
| `python-environment.md` | always | Enforces conda `test12` environment for all Python/pip/CLI operations. System Python is off-limits. |
| `kimport.md` | always | KImport workflow — bring functional logic from a `/sample` folder into the project without importing its theme or framework. The `/sample` folder is deleted after import. |
| `ui-guidelines.md` | manual | Use modals instead of JS alerts for popups. Use top-right toaster notifications for user feedback (success/error/warning/info). |
| `modular-converter.md` | manual | Converts a monolithic app into separate `backend/` (JSON API) and `frontend/` (Jinja2 UI) servers. Triggered via `MOC`/`moc`. |
| `monolithic-converter.md` | manual | Converts a modular app (`backend/` + `frontend/`) back into a single monolithic FastAPI + Jinja2 app. Triggered via `MON`/`mon`. |
| `post-content-maker.md` | manual | Documents useful chat answers into `cdocs/` as reference markdown files. Triggered via `pd`/`pdoc`/`postdoc`. |
| `static-maker.md` | manual | Converts all dynamic Jinja2 pages into fully rendered static HTML under an `html/` folder for reference purposes. |
