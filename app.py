"""
Kactii Timesheet Dashboard
==========================

A FastAPI application that reads timesheet data from Google Sheets or uploaded
CSV files and renders:

  * A dashboard ("/") showing each profile in a separate clickable box,
    plus a form to add a new profile (Google Sheet URL or CSV upload),
    and a "Top performer" card (week / month / year, current or previous).
  * A per-profile detail page ("/user/{user_id}") with analytic graphs.
  * A comparison page ("/compare") to compare two or more profiles.
  * A debug page ("/debug/{user_id}") showing how a sheet was parsed.
  * A JSON endpoint ("/top-performer") with the leaderboard for a period.

All dates are normalized and displayed as dd-mm-yyyy.

Run
---
    python app.py
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Any, Tuple
from urllib.parse import quote, urlparse, parse_qs

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# --------------------------------------------------------------------------- #
# Configuration / storage paths
# --------------------------------------------------------------------------- #

BASE_DIR = Path(__file__).resolve().parent

BUNDLED_DATA_DIR = BASE_DIR / "data"
BUNDLED_SOURCES_FILE = BUNDLED_DATA_DIR / "sources.json"


def _writable_data_dir() -> Path:
    """Return a directory the process can write to (see original notes)."""
    read_only = os.environ.get("VERCEL") or str(BASE_DIR).startswith("/var/task")
    if read_only:
        return Path("/tmp") / "kactii-timesheet-data"
    return BUNDLED_DATA_DIR


DATA_DIR = _writable_data_dir()
UPLOAD_DIR = DATA_DIR / "uploads"
SOURCES_FILE = DATA_DIR / "sources.json"

DEFAULT_SPREADSHEET_ID = "1elZ90et8sd8ogFFKjPwEbx8S14Vts8BEmNkXrW0GGcU"

EXPECTED_COLUMNS = ["Date", "Day", "Task Details", "Hours", "Resources used"]

# Display format for every date in the app.
DATE_FMT_OUT = "%d-%m-%Y"

# Top-performer settings: per-profile daily hours are cached this many seconds,
# at most this many sheets are fetched from Google at the same time, and each
# sheet gets this many seconds before it is given up on.
TOP_CACHE_TTL = 600
TOP_FETCH_LIMIT = 20
TOP_FETCH_TIMEOUT = 10


@dataclass
class Source:
    """A dashboard profile backed by a Google Sheet URL or an uploaded CSV."""

    id: str
    name: str
    kind: str        # "gsheet" or "csv"
    location: str    # gsheet: CSV export URL | csv: filename under uploads/


# --------------------------------------------------------------------------- #
# Source persistence
# --------------------------------------------------------------------------- #

def _ensure_storage() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    if not SOURCES_FILE.exists() and not BUNDLED_SOURCES_FILE.exists():
        _write_sources([
            Source(
                id="meeran",
                name="Meeran",
                kind="gsheet",
                location=_gsheet_csv_export_url(DEFAULT_SPREADSHEET_ID, sheet_name="Sheet1"),
            )
        ])


def _sources_file_for_read() -> Path:
    if SOURCES_FILE.exists():
        return SOURCES_FILE
    return BUNDLED_SOURCES_FILE


def _read_sources() -> List[Source]:
    _ensure_storage()
    path = _sources_file_for_read()
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [Source(**item) for item in raw]


def _write_sources(sources: List[Source]) -> None:
    SOURCES_FILE.parent.mkdir(parents=True, exist_ok=True)
    SOURCES_FILE.write_text(
        json.dumps([asdict(s) for s in sources], indent=2),
        encoding="utf-8",
    )


def _sources_by_id() -> Dict[str, Source]:
    return {s.id: s for s in _read_sources()}


def _sorted_sources() -> List[Source]:
    """All profiles A to Z by name (case-insensitive)."""
    return sorted(_read_sources(), key=lambda s: s.name.strip().lower())


def _json_for_html(obj: Any) -> str:
    """JSON that is safe inside a <script type="application/json"> block
    (task text can't close the tag with </script>)."""
    return (json.dumps(obj)
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
            .replace("&", "\\u0026"))


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
    """Fallback display name when the person's name is not typed in."""
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
    if sheet_name:
        return (
            f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"
            f"/gviz/tq?tqx=out:csv&sheet={quote(sheet_name)}"
        )
    base = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export?format=csv"
    if gid:
        base += f"&gid={gid}"
    return base


def _spreadsheet_id(url: str) -> str:
    if "docs.google.com/spreadsheets" not in url:
        raise ValueError("Not a Google Sheets URL")
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", url)
    if not match:
        raise ValueError("Could not extract spreadsheet ID from URL")
    return match.group(1)


def normalize_gsheet_url(url: str) -> str:
    url = url.strip()
    if "docs.google.com/spreadsheets" not in url:
        raise ValueError("Not a Google Sheets URL")

    if "output=csv" in url or "out:csv" in url or "format=csv" in url:
        return url

    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", url)
    if not match:
        raise ValueError("Could not extract spreadsheet ID from URL")
    spreadsheet_id = match.group(1)

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

_INVISIBLE = re.compile("[\u200b\u200c\u200d\u2060\ufeff]")


def _clean(cell: str) -> str:
    """Strip zero-width characters and a BOM, which Python's strip() keeps.
    Google Sheets exports can contain them in "empty" cells."""
    return _INVISIBLE.sub("", cell).strip()


# Different tabs name their columns differently ("Tasks", "Hrs", ...). Every
# header is mapped onto the canonical names the rest of the app relies on.
_HEADER_ALIASES = {
    "Date": {"date", "dates"},
    "Day": {"day", "days"},
    "Task Details": {
        "task details", "task detail", "tasks", "task", "task description",
        "task descriptions", "tasks details", "activity", "activities",
        "work done", "work details", "work", "details", "description",
        "work description",
    },
    "Hours": {
        "hours", "hour", "hrs", "hr", "time spent", "duration",
        "hours spent", "hours worked", "no of hours", "no of hrs",
    },
    "Resources used": {"resources used", "resources", "resource",
                       "resource used"},
}

# Any row containing one of these cell values is a summary row, not an entry.
_TOTAL_CELLS = {"total", "grand total", "total hours", "sum"}


def _canonical_header(h: str) -> str:
    key = re.sub(r"[^a-z0-9 ]", " ", _clean(h).lower())
    key = re.sub(r"\s+", " ", key).strip()
    for canon, names in _HEADER_ALIASES.items():
        if key in names:
            return canon
    return _clean(h)


def _find_header_index(rows: List[List[str]]) -> int:
    """Index of the header row (first 15 rows searched); 0 if none matches."""
    for i, r in enumerate(rows[:15]):
        canon = {_canonical_header(c) for c in r if c}
        if "Date" in canon and ("Hours" in canon or "Task Details" in canon):
            return i
    return 0


def _parse_table(text: str) -> Tuple[List[str], List[Dict[str, str]]]:
    """Parse CSV text into (canonical columns, row dicts)."""
    reader = csv.reader(io.StringIO(text))
    rows = [[_clean(c) for c in r] for r in reader]
    if not rows:
        return [], []

    hidx = _find_header_index(rows)
    header = [_canonical_header(h) for h in rows[hidx]]

    records: List[Dict[str, str]] = []
    last_date = ""
    for raw in rows[hidx + 1:]:
        if not any(cell.strip() for cell in raw):
            continue
        if any(cell.strip().lower() in _TOTAL_CELLS for cell in raw):
            continue

        record: Dict[str, str] = {}
        for i in range(max(len(header), len(raw))):
            key = header[i] if i < len(header) and header[i] else f"col{i}"
            value = raw[i].strip() if i < len(raw) else ""
            # Duplicate column names: keep the first non-empty value.
            if key in record and record[key]:
                continue
            record[key] = value

        # Several task rows under one date: blank dates inherit the one above.
        if record.get("Date"):
            last_date = record["Date"]
        elif last_date and (record.get("Task Details") or record.get("Hours")):
            record["Date"] = last_date

        records.append(record)
    return header, records


def _parse_csv_text(text: str) -> List[Dict[str, str]]:
    return _parse_table(text)[1]


def _header_of(text: str) -> List[str]:
    return _parse_table(text)[0]


# --------------------------------------------------------------------------- #
# Hours / date parsing
# --------------------------------------------------------------------------- #

def _parse_hours(value: str) -> Optional[float]:
    """Parse "5", "8.5" or "2:20" into hours; None when blank/unparseable."""
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


_DAY_FIRST = ["%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%d-%m-%y", "%d/%m/%y"]
_MONTH_FIRST = ["%m-%d-%Y", "%m/%d/%Y", "%m-%d-%y", "%m/%d/%y"]
_ISO = ["%Y-%m-%d", "%Y/%m/%d"]

_MONTH_NAME_FMTS = [
    "%d %b %Y", "%d %B %Y", "%d-%b-%Y", "%d-%B-%Y", "%d/%b/%Y",
    "%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%B %d %Y",
    "%d-%b-%y", "%d %b %y", "%d/%b/%y",
]
_ORDINAL = re.compile(r"(\d)(st|nd|rd|th)\b", re.I)
_WEEKDAY_PREFIX = re.compile(r"^(mon|tue|wed|thu|fri|sat|sun)[a-z]*[,.\s]+", re.I)

MIN_YEAR = 2020   # anything earlier is treated as a typo


def _detect_dayfirst(values) -> bool:
    """True unless the sheet contains dates that can only be month-first
    (e.g. 06/21/2025). Ambiguous dates default to day-first."""
    day_votes = month_votes = 0
    for v in values:
        m = re.match(r"^\s*(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})\s*$", v or "")
        if not m:
            continue
        a, b = int(m.group(1)), int(m.group(2))
        if a > 12:
            day_votes += 1
        elif b > 12:
            month_votes += 1
    return month_votes <= day_votes


def _parse_date(value: str, dayfirst: bool = True) -> Optional[datetime]:
    value = (value or "").strip()
    if not value:
        return None

    # Tidy common variations: time suffix, weekday prefix, "21st", "Sept".
    value = re.split(r"[T\s]\d{1,2}:\d{2}", value)[0].strip()
    value = _WEEKDAY_PREFIX.sub("", value)
    value = _ORDINAL.sub(r"\1", value)
    value = re.sub(r"\bsept\b", "Sep", value, flags=re.I)
    value = value.strip(" ,.")

    formats = (_ISO + _MONTH_NAME_FMTS
               + (_DAY_FIRST + _MONTH_FIRST if dayfirst else _MONTH_FIRST + _DAY_FIRST))
    for fmt in formats:
        try:
            parsed = datetime.strptime(value, fmt)
        except ValueError:
            continue
        # Reject typos like 1016 or 2062 so they can't stretch the charts.
        if MIN_YEAR <= parsed.year <= datetime.now().year + 1:
            return parsed
        return None
    return None


# --------------------------------------------------------------------------- #
# Timesheet analytics
# --------------------------------------------------------------------------- #

# Each task cell often lists several activities ("posted blog, AIFC, collected
# linkedin profiles, worked on tools"), so a task can match several categories.
# Its hours are split evenly across the categories it matches.
TASK_CATEGORIES = [
    ("AIFC", r"\baifc\b"),
    ("Blogging/Writing", r"\b(blogs?|articles?|substack|write|writing|documentation|gitbook|docs?)\b|social media post"),
    ("Video", r"\b(video|vedio|recording|youtube)\b"),
    ("AI/Agent", r"\b(ai|agents?|llm|langchain|rag|chatbot|mcq)\b"),
    ("Sessions/Meetings", r"\b(sessions?|hosted|meeting|zoom|discussion|call|sync|standup|scrum|orientation)\b"),
    ("Setup/Install", r"\b(install\w*|set ?up|setting up|configuration|config|environment|scm)\b"),
    ("Deployment", r"\b(deploy\w*|vercel|hosting)\b"),
    ("Data/Outreach", r"\b(data collection|collected|collection|linkedin|profiles?)\b"),
    ("Development", r"\b(fast ?api|flask|react|frontend|backend|api|code|coding|build|built|created|develop\w*|bug|fix\w*|feature|tools?|web ?page|clon\w+|login|merge conflict|resume builder)\b"),
    ("Learning", r"\b(learn\w*|study|research|read|course|tutorial|practice|prepar\w+)\b"),
]


def _task_categories(task: str) -> List[str]:
    """All categories a task matches, in order; ["Other"] if none."""
    low = task.lower()
    found = [label for label, pattern in TASK_CATEGORIES if re.search(pattern, low)]
    return found or ["Other"]


def _categorize_task(task: str) -> str:
    """Primary (first) category of a task."""
    return _task_categories(task)[0]


def _task_key(task: str) -> str:
    """Normalized key so 'Working on AI agent ideas.' == 'working on ai agent ideas'."""
    key = re.sub(r"\s+", " ", task.lower()).strip()
    return key.strip(" .,;:!-")


def _merge_tasks(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge tasks with the same name: sum hours, count entries, track dates."""
    groups: Dict[str, Dict[str, Any]] = {}
    for t in tasks:
        if t["hours"] is None:
            continue
        key = _task_key(t["task"])
        dt = _parse_date(t["date"]) or datetime.min
        g = groups.get(key)
        if g is None:
            g = groups[key] = {
                "task": t["task"],
                "hours": 0.0,
                "count": 0,
                "category": t["category"],
                "_first": dt, "_last": dt,
                "first_date": t["date"], "last_date": t["date"],
            }
        g["hours"] = round(g["hours"] + t["hours"], 2)
        g["count"] += 1
        if dt < g["_first"]:
            g["_first"], g["first_date"] = dt, t["date"]
        if dt > g["_last"]:
            g["_last"], g["last_date"] = dt, t["date"]

    merged = []
    for g in groups.values():
        g.pop("_first")
        g.pop("_last")
        g["date"] = g["last_date"]   # keeps compare page / older templates working
        merged.append(g)
    merged.sort(key=lambda g: g["hours"], reverse=True)
    return merged


def _extra_stats(per_date: Dict[str, float], date_order: Dict[str, datetime]) -> Dict[str, Any]:
    """Best day, longest streak, moving average and weekly totals."""
    series = sorted(
        ((date_order[l], per_date[l]) for l in per_date if l in date_order),
        key=lambda x: x[0],
    )
    if not series:
        return {"best_day": None, "longest_streak": 0, "moving_avg": [],
                "weekly_labels": [], "weekly_values": []}

    best_dt, best_h = max(series, key=lambda x: x[1])

    # Longest run of consecutive calendar days with hours > 0.
    longest = cur = 0
    prev = None
    for dt, h in series:
        if h > 0:
            cur = cur + 1 if prev and (dt - prev).days == 1 else 1
            longest = max(longest, cur)
            prev = dt
        else:
            cur, prev = 0, None

    # 7-entry moving average.
    values = [h for _, h in series]
    moving = []
    for i in range(len(values)):
        window = values[max(0, i - 6): i + 1]
        moving.append(round(sum(window) / len(window), 2))

    # Weekly totals (Monday start); empty weeks are filled with 0 so gaps show.
    weeks: Dict[datetime, float] = {}
    for dt, h in series:
        monday = dt - timedelta(days=dt.weekday())
        weeks[monday] = round(weeks.get(monday, 0.0) + h, 2)
    w, last = min(weeks), max(weeks)
    w_labels, w_values = [], []
    while w <= last:
        w_labels.append(w.strftime(DATE_FMT_OUT))
        w_values.append(weeks.get(w, 0.0))
        w += timedelta(days=7)

    return {
        "best_day": {"date": best_dt.strftime(DATE_FMT_OUT), "hours": best_h},
        "longest_streak": longest,
        "moving_avg": moving,
        "weekly_labels": w_labels,
        "weekly_values": w_values,
    }


def build_stats(rows: List[Dict[str, str]]) -> Dict[str, Any]:
    """Build a sorted, chart-ready summary from timesheet rows."""
    per_date: Dict[str, float] = {}
    date_order: Dict[str, datetime] = {}
    tasks: List[Dict[str, Any]] = []
    category_hours: Dict[str, float] = {}
    total_hours = 0.0

    for row in rows:
        date_str = (row.get("Date", "") or "").strip()
        task = (row.get("Task Details", "") or "").strip()
        hours = _parse_hours(row.get("Hours", ""))

        if date_str:
            parsed = _parse_date(date_str)
            if parsed is not None:
                per_date.setdefault(date_str, 0.0)
                date_order[date_str] = parsed
                if hours is not None:
                    per_date[date_str] = round(per_date[date_str] + hours, 2)

        if hours is not None:
            total_hours += hours

        if task and task.upper() != "NA":
            cats = _task_categories(task)
            tasks.append({
                "date": date_str,
                "day": (row.get("Day", "") or "").strip(),
                "task": task,
                "hours": hours,
                "category": " + ".join(cats),
                "sort_key": _parse_date(date_str) or datetime.min,
            })
            share = (hours or 0.0) / len(cats)
            for c in cats:
                category_hours[c] = round(category_hours.get(c, 0.0) + share, 2)

    ordered = sorted(per_date.items(), key=lambda item: date_order.get(item[0], datetime.min))
    chart_labels = [d for d, _ in ordered]
    chart_values = [v for _, v in ordered]

    cumulative_values: List[float] = []
    running = 0.0
    for _, v in ordered:
        running = round(running + v, 2)
        cumulative_values.append(running)

    tasks.sort(key=lambda t: t["sort_key"], reverse=True)
    for t in tasks:
        t.pop("sort_key", None)

    # Tasks with the same name are merged into one entry (hours summed).
    ranked = _merge_tasks(tasks)

    days_logged = len([d for d in per_date if per_date[d] > 0])
    extra = _extra_stats(per_date, date_order)

    return {
        **extra,
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


async def _fetch_text(source: Source) -> str:
    """Return the raw CSV text for a source."""
    if source.kind == "gsheet":
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            response = await client.get(source.location)
            response.raise_for_status()
        return response.text
    if source.kind == "csv":
        path = UPLOAD_DIR / source.location
        if not path.exists():
            bundled = BUNDLED_DATA_DIR / "uploads" / source.location
            if bundled.exists():
                path = bundled
            else:
                raise FileNotFoundError(f"Uploaded file missing: {source.location}")
        return path.read_text(encoding="utf-8", errors="replace")
    raise ValueError(f"Unknown source kind: {source.kind}")


async def load_source(source: Source) -> tuple[List[str], List[Dict[str, str]]]:
    """Load a source's timesheet, returning (columns, rows).

    The Date column is normalized to dd-mm-yyyy here so every page and chart
    shows the same format regardless of how the sheet was written.
    """
    text = await _fetch_text(source)

    columns, rows = _parse_table(text)
    if not rows:
        columns = []

    dayfirst = _detect_dayfirst([r.get("Date", "") for r in rows])
    for r in rows:
        d = _parse_date(r.get("Date", ""), dayfirst)
        if d:
            r["Date"] = d.strftime(DATE_FMT_OUT)
            r["Day"] = d.strftime("%A")   # sheet's Day column is hand-typed
    return columns, rows


# --------------------------------------------------------------------------- #
# Top performer (week / month / year)
# --------------------------------------------------------------------------- #

# source key -> (fetched_at, {date: hours that day})
_DAILY_CACHE: Dict[str, Tuple[float, Dict[datetime, float]]] = {}


async def _daily_hours(source: Source, sem: asyncio.Semaphore,
                       refresh: bool = False) -> Dict[datetime, float]:
    """Hours per calendar day for one profile (cached for TOP_CACHE_TTL s)."""
    key = f"{source.id}|{source.location}"
    now = time.time()
    cached = _DAILY_CACHE.get(key)
    if cached and not refresh and now - cached[0] < TOP_CACHE_TTL:
        return cached[1]

    try:
        async with sem:
            _cols, rows = await asyncio.wait_for(load_source(source), TOP_FETCH_TIMEOUT)
    except Exception:
        if cached:            # fall back to older data if we have any
            return cached[1]
        raise
    stats = build_stats(rows)

    daily: Dict[datetime, float] = {}
    for label, value in zip(stats["chart_labels"], stats["chart_values"]):
        d = _parse_date(label)
        if d is not None:
            daily[d] = value
    _DAILY_CACHE[key] = (now, daily)
    return daily


def _period_range(period: str, which: str) -> Tuple[datetime, datetime]:
    """(start, end) of the current or previous week / month / year."""
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "month":
        first = today.replace(day=1)
        if which == "last":
            first = (first - timedelta(days=1)).replace(day=1)
        nxt = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
        return first, nxt - timedelta(days=1)
    if period == "year":
        year = today.year - (1 if which == "last" else 0)
        return datetime(year, 1, 1), datetime(year, 12, 31)
    start = today - timedelta(days=today.weekday())
    if which == "last":
        start -= timedelta(days=7)
    return start, start + timedelta(days=6)


async def _warm_top_cache() -> None:
    """Pre-load every profile's daily hours so the dashboard card is instant."""
    sem = asyncio.Semaphore(TOP_FETCH_LIMIT)
    await asyncio.gather(
        *[_daily_hours(s, sem) for s in _read_sources()],
        return_exceptions=True,
    )


# --------------------------------------------------------------------------- #
# App setup
# --------------------------------------------------------------------------- #

app = FastAPI(title="Kactii Timesheet Dashboard")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.on_event("startup")
async def _startup() -> None:
    asyncio.create_task(_warm_top_cache())


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, error: Optional[str] = None,
                    added: Optional[str] = None) -> HTMLResponse:
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "users": _sorted_sources(),
            "error": error,
            "added": added,
        },
    )


@app.get("/top-performer")
async def top_performer(period: str = Query("week"),
                        which: str = Query("this"),
                        refresh: int = Query(0)) -> JSONResponse:
    """Leaderboard: who logged the most hours in the chosen period."""
    period = period if period in ("week", "month", "year") else "week"
    which = "last" if which == "last" else "this"
    start, end = _period_range(period, which)

    sources = _read_sources()
    sem = asyncio.Semaphore(TOP_FETCH_LIMIT)
    outcomes = await asyncio.gather(
        *[_daily_hours(s, sem, bool(refresh)) for s in sources],
        return_exceptions=True,
    )

    board: List[Dict[str, Any]] = []
    failed = 0
    for src, outcome in zip(sources, outcomes):
        if isinstance(outcome, Exception):
            failed += 1
            continue
        hours = round(sum(h for d, h in outcome.items() if start <= d <= end), 2)
        if hours > 0:
            board.append({"id": src.id, "name": src.name, "hours": hours})

    board.sort(key=lambda x: (-x["hours"], x["name"].lower()))

    top: List[Dict[str, Any]] = []
    gap: Optional[float] = None
    if board:
        best = board[0]["hours"]
        top = [b for b in board if b["hours"] == best]       # ties share the title
        rest = [b for b in board if b["hours"] < best]
        if rest:
            gap = round(best - rest[0]["hours"], 2)

    return JSONResponse({
        "period": period,
        "which": which,
        "week_start": start.strftime(DATE_FMT_OUT),
        "week_end": end.strftime(DATE_FMT_OUT),
        "top": top,
        "gap_to_next": gap,
        "leaderboard": board[:5],
        "profiles_with_hours": len(board),
        "profiles_checked": len(sources),
        "profiles_failed": failed,
    })


@app.post("/add")
async def add_source(
    source_type: str = Form(...),
    name: str = Form(""),
    gsheet_url: str = Form(""),
    csv_file: Optional[UploadFile] = File(None),
    tabs: str = Form(""),
) -> RedirectResponse:
    existing = _sources_by_id()
    name = name.strip()

    if source_type == "gsheet":
        try:
            location = normalize_gsheet_url(gsheet_url)
        except ValueError as exc:
            return RedirectResponse(url=f"/?error={quote(str(exc))}", status_code=303)
        name = name or _name_from_gsheet_url(gsheet_url, existing)
        new_id = _unique_id(_slugify(name), existing)
        source = Source(id=new_id, name=name, kind="gsheet", location=location)

    elif source_type == "csv":
        if csv_file is None or not csv_file.filename:
            return RedirectResponse(url="/?error=No+CSV+file+uploaded", status_code=303)
        name = name or Path(csv_file.filename).stem.strip() or "CSV"
        new_id = _unique_id(_slugify(name), existing)
        content = await csv_file.read()
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        stored_name = f"{new_id}.csv"
        (UPLOAD_DIR / stored_name).write_bytes(content)
        source = Source(id=new_id, name=name, kind="csv", location=stored_name)

    elif source_type == "gsheet_tabs":
        # One spreadsheet, many tabs: one profile per tab name (one per line).
        try:
            sheet_id = _spreadsheet_id(gsheet_url.strip())
        except ValueError as exc:
            return RedirectResponse(url=f"/?error={quote(str(exc))}", status_code=303)
        tab_names = [t.strip() for t in re.split(r"[\r\n]+", tabs) if t.strip()]
        if not tab_names:
            return RedirectResponse(url="/?error=No+tab+names+given", status_code=303)
        sources = _read_sources()
        known = {s.id: s for s in sources}
        known_locations = {s.location for s in sources}
        count = 0
        for tab in tab_names:
            location = _gsheet_csv_export_url(sheet_id, sheet_name=tab)
            if location in known_locations:
                continue
            new_id = _unique_id(_slugify(tab), known)
            src = Source(id=new_id, name=tab, kind="gsheet", location=location)
            known[new_id] = src
            known_locations.add(location)
            sources.append(src)
            count += 1
        _write_sources(sources)
        label = f"{count} profiles"
        return RedirectResponse(url=f"/?added={quote(label)}", status_code=303)

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


@app.get("/debug/{user_id}")
async def debug_source(user_id: str) -> JSONResponse:
    """Show how a profile's sheet was parsed (raw header, columns, sample rows)."""
    user = _sources_by_id().get(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        text = await _fetch_text(user)
    except (httpx.HTTPError, FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    raw_rows = [[_clean(c) for c in r] for r in csv.reader(io.StringIO(text))]
    hidx = _find_header_index(raw_rows) if raw_rows else 0
    columns, rows = _parse_table(text)
    stats = build_stats(rows)
    return JSONResponse({
        "name": user.name,
        "location": user.location,
        "header_row_index": hidx,
        "raw_header": raw_rows[hidx] if raw_rows else [],
        "columns_used": columns,
        "missing_expected": [c for c in EXPECTED_COLUMNS if c not in columns],
        "row_count": len(rows),
        "total_hours": stats["total_hours"],
        "task_count": stats["task_count"],
        "days_logged": stats["days_logged"],
        # Date cells the app could not read (typos, odd formats, wrong years).
        "bad_dates": sorted({r.get("Date", "") for r in rows
                             if r.get("Date") and _parse_date(r["Date"]) is None}),
        "sample_rows": rows[:5],
    })


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
            "chart_data_json": _json_for_html({
                "labels": stats["chart_labels"],
                "daily": stats["chart_values"],
                "cumulative": stats["cumulative_values"],
                "movingAvg": stats["moving_avg"],
                "weeklyLabels": stats["weekly_labels"],
                "weeklyValues": stats["weekly_values"],
            }),
        },
    )


def _bucket_series(results: List[Dict[str, Any]],
                   period: str) -> Tuple[List[str], List[Dict[str, Any]]]:
    """Per-profile hour totals per calendar month ("month") or year ("year").

    Returns (axis labels, one {"name", "data"} series per profile). Months or
    years with no entries are filled with 0 so gaps stay visible.
    """
    per_result: List[Dict[datetime, float]] = []
    for r in results:
        st = r["stats"]
        buckets: Dict[datetime, float] = {}
        for label, value in zip(st["chart_labels"], st["chart_values"]):
            d = _parse_date(label)
            if d is None:
                continue
            key = d.replace(day=1) if period == "month" else d.replace(month=1, day=1)
            buckets[key] = round(buckets.get(key, 0.0) + value, 2)
        per_result.append(buckets)

    keys = [k for b in per_result for k in b]
    if not keys:
        return [], [{"name": r["name"], "data": []} for r in results]

    axis_dates: List[datetime] = []
    cur, last = min(keys), max(keys)
    while cur <= last:
        axis_dates.append(cur)
        if period == "month":
            cur = (cur + timedelta(days=32)).replace(day=1)
        else:
            cur = cur.replace(year=cur.year + 1)

    fmt = "%b %Y" if period == "month" else "%Y"
    axis = [d.strftime(fmt) for d in axis_dates]
    series = [
        {"name": r["name"], "data": [b.get(d, 0.0) for d in axis_dates]}
        for r, b in zip(results, per_result)
    ]
    return axis, series


def _build_comparison(selected: List[Source],
                      results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Assemble aligned chart data for the comparison page."""
    # Shared, chronologically sorted daily axis (dd-mm-yyyy labels).
    all_dates: Dict[str, datetime] = {}
    for r in results:
        for label in r["stats"]["chart_labels"]:
            parsed = _parse_date(label)
            all_dates[label] = parsed if parsed is not None else datetime.min
    axis = sorted(all_dates, key=lambda d: all_dates[d])

    cumulative_series = []
    for r in results:
        st = r["stats"]
        by_date = dict(zip(st["chart_labels"], st["cumulative_values"]))
        series = []
        last = 0.0
        for d in axis:
            if d in by_date:
                last = by_date[d]
            series.append(last)
        cumulative_series.append({"name": r["name"], "data": series})

    # Shared weekly axis (Monday dates), gaps filled with 0.
    week_dates = []
    for r in results:
        for label in r["stats"]["weekly_labels"]:
            parsed = _parse_date(label)
            if parsed:
                week_dates.append(parsed)
    weekly_axis: List[str] = []
    if week_dates:
        w, last_w = min(week_dates), max(week_dates)
        while w <= last_w:
            weekly_axis.append(w.strftime(DATE_FMT_OUT))
            w += timedelta(days=7)
    weekly_series = []
    for r in results:
        st = r["stats"]
        by_week = dict(zip(st["weekly_labels"], st["weekly_values"]))
        weekly_series.append({
            "name": r["name"],
            "data": [by_week.get(w, 0.0) for w in weekly_axis],
        })

    monthly_axis, monthly_series = _bucket_series(results, "month")
    yearly_axis, yearly_series = _bucket_series(results, "year")

    totals = [{"name": r["name"], "total": r["stats"]["total_hours"]} for r in results]

    cat_totals: Dict[str, float] = {}
    for r in results:
        for cat, hrs in r["stats"]["category_hours"].items():
            cat_totals[cat] = round(cat_totals.get(cat, 0.0) + hrs, 2)
    categories = [c for c, _ in sorted(cat_totals.items(), key=lambda kv: kv[1], reverse=True)]

    category_series = []
    for r in results:
        ch = r["stats"]["category_hours"]
        category_series.append({
            "name": r["name"],
            "data": [round(ch.get(cat, 0.0), 2) for cat in categories],
        })

    category_pies = []
    for r in results:
        ch = r["stats"]["category_hours"]
        labels = [c for c in categories if ch.get(c, 0.0) > 0]
        category_pies.append({
            "name": r["name"],
            "labels": labels,
            "data": [round(ch[c], 2) for c in labels],
        })

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
        "weekly_axis": weekly_axis,
        "weekly_series": weekly_series,
        "monthly_axis": monthly_axis,
        "monthly_series": monthly_series,
        "yearly_axis": yearly_axis,
        "yearly_series": yearly_series,
        "totals": totals,
        "categories": categories,
        "category_series": category_series,
        "category_pies": category_pies,
        "task_time": task_time,
    }


@app.get("/compare", response_class=HTMLResponse)
async def compare(request: Request,
                  users: List[str] = Query(default=[])) -> HTMLResponse:
    all_sources = _sorted_sources()
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
    all_sources = _read_sources()
    by_id = {s.id: s for s in all_sources}
    # De-duplicate while keeping the order the user picked.
    seen = set()
    selected = []
    for uid in users:
        if uid in by_id and uid not in seen:
            seen.add(uid)
            selected.append(by_id[uid])

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
            "comparison_json": _json_for_html(comparison) if comparison else "null",
        },
    )


if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8477, reload=True)