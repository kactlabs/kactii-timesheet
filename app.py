"""
Kactii Timesheet Dashboard
==========================

A FastAPI application that reads timesheet data from Google Sheets or uploaded
CSV files and renders:

  * A dashboard ("/") showing each user/source in a separate clickable box,
    plus a form to add a new source (Google Sheet URL or CSV upload).
  * A per-user detail page ("/user/{user_id}") that opens when a box is
    clicked, showing that source's full timesheet.

Data sources
------------
Two kinds of sources are supported:

  * "gsheet" - a public Google Sheet. The sheet URL is normalized to its CSV
    export endpoint and fetched live on each request.
  * "csv"    - an uploaded CSV file stored under ``data/uploads/``.

Sources are persisted to ``data/sources.json`` so they survive restarts.

Run
---
    python app.py
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import re
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Any
from urllib.parse import quote, urlparse, parse_qs

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# --------------------------------------------------------------------------- #
# Configuration / storage paths
# --------------------------------------------------------------------------- #

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
SOURCES_FILE = DATA_DIR / "sources.json"

# Default Google Spreadsheet seeded on first run (one tab per user).
DEFAULT_SPREADSHEET_ID = "1elZ90et8sd8ogFFKjPwEbx8S14Vts8BEmNkXrW0GGcU"

# Column headers we expect in a timesheet.
EXPECTED_COLUMNS = ["Date", "Day", "Task Details", "Hours", "Resources used"]


@dataclass
class Source:
    """A dashboard source backed by a Google Sheet URL or an uploaded CSV."""

    id: str                  # URL-safe identifier used in routes
    name: str                # Display name shown on the dashboard box
    kind: str                # "gsheet" or "csv"
    location: str            # gsheet: CSV export URL | csv: filename under uploads/


# --------------------------------------------------------------------------- #
# Source persistence
# --------------------------------------------------------------------------- #

def _ensure_storage() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    if not SOURCES_FILE.exists():
        seed = [
            Source(
                id="meeran",
                name="Meeran",
                kind="gsheet",
                location=_gsheet_csv_export_url(DEFAULT_SPREADSHEET_ID, sheet_name="Sheet1"),
            )
        ]
        _write_sources(seed)


def _read_sources() -> List[Source]:
    _ensure_storage()
    raw = json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
    return [Source(**item) for item in raw]


def _write_sources(sources: List[Source]) -> None:
    SOURCES_FILE.parent.mkdir(parents=True, exist_ok=True)
    SOURCES_FILE.write_text(
        json.dumps([asdict(s) for s in sources], indent=2),
        encoding="utf-8",
    )


def _sources_by_id() -> Dict[str, Source]:
    return {s.id: s for s in _read_sources()}


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or uuid.uuid4().hex[:8]


def _unique_id(base: str, existing: Dict[str, Source]) -> str:
    candidate = base
    suffix = 2
    while candidate in existing:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _name_from_gsheet_url(url: str, existing: Dict[str, Source]) -> str:
    """Derive a display name from a Google Sheet URL.

    Prefers an explicit ``sheet=`` tab name, then a ``gid`` fragment, then a
    numbered fallback so a name is always available without user input.
    """
    parsed = urlparse(url)
    for part in (parsed.query, parsed.fragment):
        if not part:
            continue
        params = parse_qs(part)
        if "sheet" in params and params["sheet"][0].strip():
            return params["sheet"][0].strip()
        if "gid" in params and params["gid"][0].strip():
            return f"Sheet {params['gid'][0].strip()}"
    return f"Source {len(existing) + 1}"


# --------------------------------------------------------------------------- #
# Google Sheet URL handling
# --------------------------------------------------------------------------- #

def _gsheet_csv_export_url(spreadsheet_id: str, gid: Optional[str] = None,
                           sheet_name: Optional[str] = None) -> str:
    """Build a public CSV export URL for a spreadsheet by gid or sheet name."""
    if sheet_name:
        return (
            f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"
            f"/gviz/tq?tqx=out:csv&sheet={quote(sheet_name)}"
        )
    base = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export?format=csv"
    if gid:
        base += f"&gid={gid}"
    return base


def normalize_gsheet_url(url: str) -> str:
    """Turn any Google Sheets URL into its CSV export URL.

    Accepts full edit URLs (with optional #gid=... or ?gid=...) as well as
    URLs that are already export/gviz endpoints (returned unchanged).
    """
    url = url.strip()
    if "docs.google.com/spreadsheets" not in url:
        raise ValueError("Not a Google Sheets URL")

    # Already a CSV export/gviz endpoint - keep as-is.
    if "output=csv" in url or "out:csv" in url or "format=csv" in url:
        return url

    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", url)
    if not match:
        raise ValueError("Could not extract spreadsheet ID from URL")
    spreadsheet_id = match.group(1)

    # Look for a gid in the fragment (#gid=) or query (?gid=).
    gid = None
    parsed = urlparse(url)
    if parsed.fragment:
        frag = parse_qs(parsed.fragment)
        if "gid" in frag:
            gid = frag["gid"][0]
    if gid is None and parsed.query:
        q = parse_qs(parsed.query)
        if "gid" in q:
            gid = q["gid"][0]

    return _gsheet_csv_export_url(spreadsheet_id, gid=gid)


# --------------------------------------------------------------------------- #
# Timesheet parsing
# --------------------------------------------------------------------------- #

def _parse_csv_text(text: str) -> List[Dict[str, str]]:
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return []

    header = [h.strip() for h in rows[0]]
    records: List[Dict[str, str]] = []
    for raw in rows[1:]:
        if not any(cell.strip() for cell in raw):
            continue
        record = {
            header[i] if i < len(header) else f"col{i}": (raw[i].strip() if i < len(raw) else "")
            for i in range(max(len(header), len(raw)))
        }
        records.append(record)
    return records


def _header_of(text: str) -> List[str]:
    reader = csv.reader(io.StringIO(text))
    for row in reader:
        return [h.strip() for h in row]
    return []


# --------------------------------------------------------------------------- #
# Timesheet analytics (for the visualized user page)
# --------------------------------------------------------------------------- #

def _parse_hours(value: str) -> Optional[float]:
    """Parse an hours cell into a float.

    Handles plain decimals ("5", "8.5") and "H:MM" durations ("2:20").
    Returns None when the value is blank or unparseable (no fake fallback).
    """
    value = (value or "").strip()
    if not value:
        return None
    if ":" in value:
        parts = value.split(":")
        try:
            hours = int(parts[0])
            minutes = int(parts[1]) if len(parts) > 1 and parts[1] != "" else 0
            return round(hours + minutes / 60.0, 2)
        except ValueError:
            return None
    try:
        return float(value)
    except ValueError:
        return None


def _parse_date(value: str) -> Optional[datetime]:
    """Parse a date cell using the formats seen in the sheets."""
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%m/%d/%Y", "%d/%m/%Y", "%Y-%m-%d", "%m/%d/%y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


# Keyword-based task categories (checked in order; first match wins). The
# data has no explicit category column, so we derive one from the task text.
TASK_CATEGORIES = [
    ("Article/Writing", ["article", "blog", "substack", "write", "documentation", "docs"]),
    ("Video", ["video", "vedio", "recording", "youtube"]),
    ("AI/Agent", ["ai ", " ai", "agent", "llm", "langchain", "rag", "chatbot", "model", "mcq"]),
    ("Meeting", ["meeting", "zoom", "discussion", "call", "sync", "standup", "scrum"]),
    ("Setup/Install", ["install", "setup", "set up", "configuration", "config", "environment", "scm"]),
    ("Development", ["created", "develop", "build", "code", "api", "fastapi", "flask", "react", "frontend", "backend", "bug", "fix", "feature"]),
    ("Learning", ["learn", "study", "research", "read", "course", "tutorial", "practice"]),
]


def _categorize_task(task: str) -> str:
    """Classify a task into a category by keyword; 'Other' if none match."""
    low = " " + task.lower() + " "
    for label, keywords in TASK_CATEGORIES:
        if any(kw in low for kw in keywords):
            return label
    return "Other"


def build_stats(rows: List[Dict[str, str]]) -> Dict[str, Any]:
    """Build a sorted, chart-ready summary from timesheet rows.

    Returns totals plus a chronologically sorted per-date hours series, a task
    ranking sorted by hours (descending), and a task-category breakdown.
    """
    per_date: Dict[str, float] = {}
    date_order: Dict[str, datetime] = {}
    tasks: List[Dict[str, Any]] = []
    category_hours: Dict[str, float] = {}
    total_hours = 0.0
    hours_count = 0

    for row in rows:
        date_str = (row.get("Date", "") or "").strip()
        task = (row.get("Task Details", "") or "").strip()
        hours = _parse_hours(row.get("Hours", ""))

        if hours is not None:
            total_hours += hours
            hours_count += 1
            if date_str:
                per_date[date_str] = round(per_date.get(date_str, 0.0) + hours, 2)
                parsed = _parse_date(date_str)
                if parsed is not None:
                    date_order[date_str] = parsed

        if task and task.upper() != "NA":
            category = _categorize_task(task)
            tasks.append({
                "date": date_str,
                "day": (row.get("Day", "") or "").strip(),
                "task": task,
                "hours": hours,
                "category": category,
                "sort_key": _parse_date(date_str) or datetime.min,
            })
            # Attribute hours to the category (count 0 when hours missing so
            # the category still registers as activity).
            category_hours[category] = round(
                category_hours.get(category, 0.0) + (hours or 0.0), 2
            )

    # Chronological per-date series for the bar chart.
    def _date_sort(item: tuple[str, float]) -> datetime:
        return date_order.get(item[0], datetime.min)

    ordered = sorted(per_date.items(), key=_date_sort)
    chart_labels = [d for d, _ in ordered]
    chart_values = [v for _, v in ordered]

    # Cumulative hours over time for the progress line chart.
    cumulative_values: List[float] = []
    running = 0.0
    for _, v in ordered:
        running = round(running + v, 2)
        cumulative_values.append(running)

    # Tasks, newest first.
    tasks.sort(key=lambda t: t["sort_key"], reverse=True)
    for t in tasks:
        t.pop("sort_key", None)

    # Task ranking by hours (only those with a numeric value), full list so
    # the page can paginate it.
    ranked = sorted(
        [t for t in tasks if t["hours"] is not None],
        key=lambda t: t["hours"],
        reverse=True,
    )

    days_logged = len([d for d in per_date if per_date[d] > 0])

    return {
        "total_hours": round(total_hours, 2),
        "task_count": len(tasks),
        "days_logged": days_logged,
        "avg_hours": round(total_hours / days_logged, 2) if days_logged else 0.0,
        "chart_labels": chart_labels,
        "chart_values": chart_values,
        "cumulative_values": cumulative_values,
        "tasks": tasks,
        "top_tasks": ranked,
        "category_hours": category_hours,
    }


async def load_source(source: Source) -> tuple[List[str], List[Dict[str, str]]]:
    """Load a source's timesheet, returning (columns, rows).

    Errors propagate so the caller can surface a real message instead of
    returning empty/fake data.
    """
    if source.kind == "gsheet":
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            response = await client.get(source.location)
            response.raise_for_status()
        text = response.text
    elif source.kind == "csv":
        path = UPLOAD_DIR / source.location
        if not path.exists():
            raise FileNotFoundError(f"Uploaded file missing: {source.location}")
        text = path.read_text(encoding="utf-8", errors="replace")
    else:
        raise ValueError(f"Unknown source kind: {source.kind}")

    rows = _parse_csv_text(text)
    columns = _header_of(text) if rows else []
    return columns, rows


# --------------------------------------------------------------------------- #
# App setup
# --------------------------------------------------------------------------- #

app = FastAPI(title="Kactii Timesheet Dashboard")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, error: Optional[str] = None,
                    added: Optional[str] = None) -> HTMLResponse:
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "users": _read_sources(),
            "error": error,
            "added": added,
        },
    )


@app.post("/add")
async def add_source(
    source_type: str = Form(...),
    gsheet_url: str = Form(""),
    csv_file: Optional[UploadFile] = File(None),
) -> RedirectResponse:
    existing = _sources_by_id()

    if source_type == "gsheet":
        try:
            location = normalize_gsheet_url(gsheet_url)
        except ValueError as exc:
            return RedirectResponse(url=f"/?error={quote(str(exc))}", status_code=303)
        # Derive a display name from the sheet tab, then fall back to a counter.
        name = _name_from_gsheet_url(gsheet_url, existing)
        new_id = _unique_id(_slugify(name), existing)
        source = Source(id=new_id, name=name, kind="gsheet", location=location)

    elif source_type == "csv":
        if csv_file is None or not csv_file.filename:
            return RedirectResponse(url="/?error=No+CSV+file+uploaded", status_code=303)
        # Derive the display name from the uploaded file name.
        name = Path(csv_file.filename).stem.strip() or "CSV"
        new_id = _unique_id(_slugify(name), existing)
        content = await csv_file.read()
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        stored_name = f"{new_id}.csv"
        (UPLOAD_DIR / stored_name).write_bytes(content)
        source = Source(id=new_id, name=name, kind="csv", location=stored_name)

    else:
        return RedirectResponse(url="/?error=Invalid+source+type", status_code=303)

    sources = _read_sources()
    sources.append(source)
    _write_sources(sources)
    return RedirectResponse(url=f"/?added={quote(source.name)}", status_code=303)


@app.post("/delete/{user_id}")
async def delete_source(user_id: str) -> RedirectResponse:
    sources = _read_sources()
    remaining = []
    for s in sources:
        if s.id == user_id:
            if s.kind == "csv":
                fpath = UPLOAD_DIR / s.location
                if fpath.exists():
                    fpath.unlink()
            continue
        remaining.append(s)
    _write_sources(remaining)
    return RedirectResponse(url="/", status_code=303)


@app.get("/user/{user_id}", response_class=HTMLResponse)
async def user_detail(request: Request, user_id: str) -> HTMLResponse:
    user = _sources_by_id().get(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    try:
        columns, rows = await load_source(user)
    except (httpx.HTTPError, FileNotFoundError, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not load timesheet for '{user.name}': {exc}",
        ) from exc

    stats = build_stats(rows)
    return templates.TemplateResponse(
        "user.html",
        {
            "request": request,
            "user": user,
            "rows": rows,
            "columns": columns,
            "stats": stats,
            "chart_labels_json": json.dumps(stats["chart_labels"]),
            "chart_values_json": json.dumps(stats["chart_values"]),
            "cumulative_values_json": json.dumps(stats["cumulative_values"]),
        },
    )


def _build_comparison(selected: List[Source],
                      results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Assemble aligned chart data for the comparison page.

    - Totals bar: one bar of total hours per user.
    - Cumulative lines: each user's cumulative hours plotted on a shared,
      chronologically sorted date axis (missing dates carry the prior value).
    """
    # Unified, chronologically sorted date axis across all selected users.
    all_dates: Dict[str, datetime] = {}
    for r in results:
        for label in r["stats"]["chart_labels"]:
            parsed = _parse_date(label)
            all_dates[label] = parsed if parsed is not None else datetime.min
    axis = sorted(all_dates, key=lambda d: all_dates[d])

    cumulative_series = []
    for r in results:
        st = r["stats"]
        # Map date -> cumulative value for this user.
        by_date = dict(zip(st["chart_labels"], st["cumulative_values"]))
        series = []
        last = 0.0
        for d in axis:
            if d in by_date:
                last = by_date[d]
            series.append(last)
        cumulative_series.append({"name": r["name"], "data": series})

    totals = [{"name": r["name"], "total": r["stats"]["total_hours"]} for r in results]

    # --- Task-category distribution (flowchart/pie style) ---
    # Unified set of categories across all users, ordered by total hours.
    cat_totals: Dict[str, float] = {}
    for r in results:
        for cat, hrs in r["stats"]["category_hours"].items():
            cat_totals[cat] = round(cat_totals.get(cat, 0.0) + hrs, 2)
    categories = [c for c, _ in sorted(cat_totals.items(), key=lambda kv: kv[1], reverse=True)]

    # Per-user distribution over the shared category axis (stacked bars).
    category_series = []
    for r in results:
        ch = r["stats"]["category_hours"]
        category_series.append({
            "name": r["name"],
            "data": [round(ch.get(cat, 0.0), 2) for cat in categories],
        })

    # Per-user pie/doughnut of their own category split.
    category_pies = []
    for r in results:
        ch = r["stats"]["category_hours"]
        labels = [c for c in categories if ch.get(c, 0.0) > 0]
        category_pies.append({
            "name": r["name"],
            "labels": labels,
            "data": [round(ch[c], 2) for c in labels],
        })

    # --- Time spent on each task (top tasks per user) ---
    task_time = []
    for r in results:
        top = [t for t in r["stats"]["top_tasks"] if t["hours"] is not None][:8]
        task_time.append({
            "name": r["name"],
            "labels": [
                (t["task"][:45] + "...") if len(t["task"]) > 45 else t["task"]
                for t in top
            ],
            "data": [t["hours"] for t in top],
        })

    return {
        "axis": axis,
        "cumulative_series": cumulative_series,
        "totals": totals,
        "categories": categories,
        "category_series": category_series,
        "category_pies": category_pies,
        "task_time": task_time,
    }


@app.get("/compare", response_class=HTMLResponse)
async def compare(request: Request,
                  users: List[str] = Query(default=[])) -> HTMLResponse:
    """Picker page: choose users, then submit to the results page."""
    all_sources = _read_sources()
    valid_ids = {s.id for s in all_sources}
    selected_ids = [uid for uid in users if uid in valid_ids]
    return templates.TemplateResponse(
        "compare.html",
        {
            "request": request,
            "all_users": all_sources,
            "selected_ids": selected_ids,
        },
    )


@app.get("/compare/results", response_class=HTMLResponse)
async def compare_results(request: Request,
                          users: List[str] = Query(default=[])) -> HTMLResponse:
    """Results page: the actual comparison graphs for the chosen users."""
    all_sources = _read_sources()
    by_id = {s.id: s for s in all_sources}
    selected = [by_id[uid] for uid in users if uid in by_id]

    results: List[Dict[str, Any]] = []
    errors: List[str] = []
    if selected:
        loaded = await asyncio.gather(
            *[load_source(s) for s in selected],
            return_exceptions=True,
        )
        for src, outcome in zip(selected, loaded):
            if isinstance(outcome, Exception):
                errors.append(f"{src.name}: {outcome}")
                continue
            _cols, rows = outcome
            results.append({
                "id": src.id,
                "name": src.name,
                "stats": build_stats(rows),
            })

    comparison = _build_comparison(selected, results) if results else None

    return templates.TemplateResponse(
        "compare_results.html",
        {
            "request": request,
            "selected_ids": [s.id for s in selected],
            "results": results,
            "errors": errors,
            "comparison": comparison,
            "comparison_json": json.dumps(comparison) if comparison else "null",
        },
    )


if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8477, reload=True)
