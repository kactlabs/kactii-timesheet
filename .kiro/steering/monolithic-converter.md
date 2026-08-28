---
inclusion: manual
---

# Monolithic Converter

Convert a modular app (separate **backend** and **frontend** servers) into a single monolithic application.

## Defaults

- **Monolith**: FastAPI + Jinja2 (serves HTML directly, business logic inline)

The user may override this default by specifying a different framework in their chat message (e.g., "use Flask" or "use Express").

## Process

1. Read and understand the current modular app (`backend/` and `frontend/` folders — routes, templates, API endpoints, HTTP client calls).
2. If a `/sample` folder exists, use it as a structural reference (follow KImport rules — logic only, no theme/CSS).
3. Merge both modules into the project root:
   - Combine backend API logic and frontend template rendering into a single app.
   - Routes serve HTML directly with embedded business logic (no separate API layer).
4. Consolidate the modular logic:
   - **Business logic** from backend endpoints becomes direct function calls (no HTTP round-trips).
   - **Templates** from frontend move to `templates/` at the project root.
   - **Static files** from frontend move to `static/` at the project root.
   - **HTTP client modules** (`backend_consumer`, `business.py` that calls API) are removed — replaced with direct imports.
5. The merged project root gets:
   - `app.py` (single entry point, runnable via `python app.py`)
   - `requirements.txt` (merged dependencies, remove `httpx` or equivalent HTTP client if no longer needed)
   - `.env.sample` (single `PORT_NUMBER`, remove `API_BASE` and separate backend/frontend port vars)
   - `preloader.py` (if needed, for loading env vars)
6. Remove the old modular folders (`backend/`, `frontend/`) from the project root after confirming the monolithic structure works.
7. Inline all inter-service HTTP calls — replace API requests with direct function/module imports.

## Port Convention

- Monolith: uses a single port (the original frontend port, or user-specified)

## Constraints

- Do NOT leave dead code or modular leftovers (`backend/`, `frontend/`) in the root after conversion.
- Do NOT keep HTTP client dependencies (`httpx`, etc.) if they are only used for inter-service calls.
- Do NOT keep separate API-only endpoints unless the user explicitly requests them.
- The monolith must be independently runnable with `python app.py`.
- Follow all existing project steering rules (environment, formatting, etc.).
