---
inclusion: manual
---

# Modular Converter

Convert a monolithic app into a modular architecture with separate **backend** and **frontend** servers.

## Defaults

- **Backend**: FastAPI (returns JSON only, REST API)
- **Frontend**: FastAPI + Jinja2 (serves HTML, consumes backend via HTTP)

The user may override these defaults by specifying different frameworks in their chat message (e.g., "use Flask for frontend" or "use Express for backend").

## Process

1. Read and understand the current monolithic app (routes, templates, logic).
2. If a `/sample` folder exists, use it as a structural reference (follow KImport rules — logic only, no theme/CSS).
3. Create two folders at the project root:
   - `backend/` — API server, JSON responses only
   - `frontend/` — UI server, consumes backend API over HTTP
4. Split the monolithic logic:
   - **Backend** gets: data processing, business logic exposed as API endpoints.
   - **Frontend** gets: templates, form handling, a `backend_consumer` HTTP client module, and a `business.py` layer that calls the backend.
5. Each folder gets its own:
   - `app.py` (main entry point, runnable via `python app.py`)
   - `requirements.txt`
   - `.env.sample` (with `BACKEND_PORT_NUMBER` for backend, `API_BASE` for frontend)
   - `preloader.py` (backend, for loading env vars)
6. Remove the old monolithic files from the project root (app.py, templates/, requirements.txt) after confirming the new structure works.
7. The frontend should use `httpx` (Python) or equivalent HTTP client to call backend endpoints.

## Port Convention

- Backend: `original_port + 1` (or user-specified)
- Frontend: keeps the original port (or user-specified)

## Constraints

- Do NOT leave dead code or monolithic leftovers in the root after conversion.
- Do NOT hardcode the backend URL — always read from environment variable (`API_BASE`).
- Each module must be independently runnable with `python app.py`.
- Follow all existing project steering rules (environment, formatting, etc.).
