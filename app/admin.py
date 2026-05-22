from __future__ import annotations

import html
import json
import re
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Security, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import APIKeyHeader
from sqlalchemy.orm import Session

from app.cli import _build_parser
from app.core.config import settings
from app.db.session import get_db
from app.parsers.errors import ParserError
from app.repositories.search_repository import SearchRepository
from app.services.monitor_service import MonitorService

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,120}$")
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
router = APIRouter(prefix="/admin", tags=["admin"])


def _require_admin_api_key(
    key_header: str | None = Security(_api_key_header),
    api_key_qs: str | None = Query(default=None, alias="api_key"),
) -> None:
    if not settings.api_key:
        return
    if key_header == settings.api_key or api_key_qs == settings.api_key:
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid API key")


def _is_avito_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").lower()
    return host == "avito.ru" or host.endswith(".avito.ru")


def _num(value: str, name: str) -> float | None:
    if not value.strip():
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a valid number") from exc


def _keywords(value: str) -> list[str] | None:
    items = [item.strip().lower() for item in value.split(",") if item.strip()]
    return items or None


def _render_page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}</title>
<style>body{{font-family:Arial,sans-serif;max-width:1100px;margin:1rem auto;padding:0 1rem}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ccc;padding:.4rem;vertical-align:top}}input,textarea{{width:100%;padding:.35rem}}.row{{margin:.4rem 0}}.actions form{{display:inline-block;margin:.1rem}}.note{{background:#fff7d6;padding:.5rem;border:1px solid #e2c86f}}.error{{background:#ffdede;padding:.5rem;border:1px solid #d66}}</style></head><body>{body}</body></html>""")




async def _parse_form(request: Request) -> dict[str, str]:
    data = parse_qs((await request.body()).decode('utf-8'), keep_blank_values=True)
    return {k: v[-1] if v else '' for k, v in data.items()}
def _job_form(job=None, error: str = "") -> str:
    filters = (getattr(job, "filters_json", {}) if job else {}) or {}
    def v(name, default=""):
        if job and hasattr(job, name):
            return getattr(job, name)
        return filters.get(name, default)
    checked_active = "checked" if (getattr(job, "is_active", True) if job else True) else ""
    checked_req_pub = "checked" if filters.get("require_published_at") else ""
    return f"""{'<div class="error">'+html.escape(error)+'</div>' if error else ''}
<div class='row'>human_title<input name='human_title' value='{html.escape(str(filters.get("human_title", "")))}'></div>
<div class='row'>name<input name='name' value='{html.escape(str(v("name", "")))}' required></div>
<div class='row'>source_url<textarea name='source_url' rows='3' required>{html.escape(str(v("source_url", "")))}</textarea></div>
<div class='note'>Some Avito constraints such as owner/first floor are currently controlled by the Avito URL. Internal filters below are additional safety filters.</div>
<div class='row'>is_active <input type='checkbox' name='is_active' {checked_active}></div>
<div class='row'>poll_interval_sec<input name='poll_interval_sec' type='number' min='1' value='{html.escape(str(v("poll_interval_sec", 180)))}'></div>
<div class='row'>min_price<input name='min_price' value='{html.escape(str(filters.get("min_price", "")))}'></div>
<div class='row'>max_price<input name='max_price' value='{html.escape(str(filters.get("max_price", "")))}'></div>
<div class='row'>min_area<input name='min_area' value='{html.escape(str(filters.get("min_area", "")))}'></div>
<div class='row'>max_area<input name='max_area' value='{html.escape(str(filters.get("max_area", "")))}'></div>
<div class='row'>max_age_hours<input name='max_age_hours' value='{html.escape(str(filters.get("max_age_hours", "")))}'></div>
<div class='row'>require_published_at <input type='checkbox' name='require_published_at' {checked_req_pub}></div>
<div class='row'>include_keywords<input name='include_keywords' value='{html.escape(",".join(filters.get("include_keywords", [])) if isinstance(filters.get("include_keywords"), list) else str(filters.get("include_keywords", "")))}'></div>
<div class='row'>exclude_keywords<input name='exclude_keywords' value='{html.escape(",".join(filters.get("exclude_keywords", [])) if isinstance(filters.get("exclude_keywords"), list) else str(filters.get("exclude_keywords", "")))}'></div>
<div class='row'>location_keywords<input name='location_keywords' value='{html.escape(",".join(filters.get("location_keywords", [])) if isinstance(filters.get("location_keywords"), list) else str(filters.get("location_keywords", "")))}'></div>
<div class='row'>profile<input name='profile' value='{html.escape(str(filters.get("profile", "production")))}'></div>
<div class='row'>category<input name='category' value='{html.escape(str(filters.get("category", "")))}'></div>
<div class='row'>city<input name='city' value='{html.escape(str(filters.get("city", "")))}'></div>
<div class='row'>seller<input name='seller' value='{html.escape(str(filters.get("seller", "")))}'></div>
<div class='row'>floor<input name='floor' value='{html.escape(str(filters.get("floor", "")))}'></div>
"""


def _extract_filters(form: dict[str, str], require_published_at: bool) -> dict:
    out = {}
    if form["human_title"].strip():
        out["human_title"] = form["human_title"].strip()
    for n in ("min_price", "max_price", "min_area", "max_area", "max_age_hours"):
        num = _num(form[n], n)
        if num is not None:
            out[n] = num
    if require_published_at:
        out["require_published_at"] = True
    for n in ("include_keywords", "exclude_keywords", "location_keywords"):
        kws = _keywords(form[n])
        if kws is not None:
            out[n] = kws
    for n in ("profile", "category", "city", "seller", "floor"):
        if form[n].strip():
            out[n] = form[n].strip()
    return out


@router.get("/searches", response_class=HTMLResponse, dependencies=[Depends(_require_admin_api_key)])
def searches(db: Session = Depends(get_db)):
    rows = []
    for s in SearchRepository(db).list_all():
        rows.append(f"<tr><td>{s.id}</td><td>{html.escape(s.name)}</td><td>{html.escape(str((s.filters_json or {}).get('human_title','')))}</td><td>{s.is_active}</td><td>{s.baseline_initialized}</td><td>{s.fail_count}</td><td>{html.escape(s.last_error or '')}</td><td>{s.last_success_at or ''}</td><td>{s.next_run_at or ''}</td><td>{s.poll_interval_sec}</td><td class='actions'><a href='/admin/searches/{s.id}/edit'>edit</a><form method='post' action='/admin/searches/{s.id}/{'deactivate' if s.is_active else 'activate'}'><button>{'deactivate' if s.is_active else 'activate'}</button></form><form method='post' action='/admin/searches/{s.id}/reset-baseline'><button>reset baseline</button></form><form method='post' action='/admin/searches/{s.id}/run-once'><button>run once</button></form></td></tr>")
    return _render_page("Searches", f"<h1>Searches</h1><p><a href='/admin/searches/new'>New search</a></p><table><tr><th>id</th><th>name</th><th>human_title</th><th>active</th><th>baseline</th><th>fail_count</th><th>last_error</th><th>last_success_at</th><th>next_run_at</th><th>poll_interval_sec</th><th>actions</th></tr>{''.join(rows)}</table>")

@router.get('/searches/new', response_class=HTMLResponse, dependencies=[Depends(_require_admin_api_key)])
def new_search_form():
    return _render_page('New search', f"<h1>New search</h1><form method='post' action='/admin/searches'>{_job_form()}<button type='submit'>Create</button></form>")

@router.post('/searches', dependencies=[Depends(_require_admin_api_key)])
async def create_search(request: Request, db: Session = Depends(get_db)):
    form = await _parse_form(request)
    form.setdefault('human_title','')
    form.setdefault('name','')
    form.setdefault('source_url','')
    form.setdefault('poll_interval_sec','180')
    for k in ('min_price','max_price','min_area','max_area','max_age_hours','include_keywords','exclude_keywords','location_keywords','profile','category','city','seller','floor'):
        form.setdefault(k, '')
    try:
        if not form['name'].strip() or not NAME_RE.fullmatch(form['name'].strip()):
            raise ValueError('name must match ^[a-z0-9][a-z0-9_-]{2,120}$')
        if not form['source_url'].strip() or not _is_avito_url(form['source_url'].strip()):
            raise ValueError('source_url must be a valid avito.ru URL')
        poll = int(form['poll_interval_sec'])
        if poll <= 0:
            raise ValueError('poll_interval_sec must be a positive integer')
        filters = _extract_filters(form, 'require_published_at' in form)
    except (ValueError, TypeError) as exc:
        return _render_page('Validation error', f"<h1>New search</h1><form method='post' action='/admin/searches'>{_job_form(type('O',(),form), str(exc))}<button type='submit'>Create</button></form>")
    item = SearchRepository(db).create(name=form['name'].strip(), source_url=form['source_url'].strip(), filters_json=filters, poll_interval_sec=poll)
    item.is_active = 'is_active' in form
    item.baseline_initialized = False
    item.fail_count = 0
    item.next_run_at = None
    db.commit()
    return RedirectResponse('/admin/searches', status_code=303)

@router.get('/searches/{search_id}/edit', response_class=HTMLResponse, dependencies=[Depends(_require_admin_api_key)])
def edit_form(search_id: int, db: Session = Depends(get_db)):
    job = SearchRepository(db).get(search_id)
    if job is None:
        raise HTTPException(404)
    return _render_page('Edit search', f"<h1>Edit search #{search_id}</h1><form method='post' action='/admin/searches/{search_id}'>{_job_form(job)}<button type='submit'>Save</button></form>")

@router.post('/searches/{search_id}', dependencies=[Depends(_require_admin_api_key)])
async def update_search(search_id: int, request: Request, db: Session = Depends(get_db)):
    repo = SearchRepository(db)
    job = repo.get(search_id)
    if job is None:
        raise HTTPException(404)
    form = await _parse_form(request)
    form.setdefault('human_title','')
    form.setdefault('name','')
    form.setdefault('source_url','')
    form.setdefault('poll_interval_sec','180')
    for k in ('min_price','max_price','min_area','max_area','max_age_hours','include_keywords','exclude_keywords','location_keywords','profile','category','city','seller','floor'):
        form.setdefault(k, '')
    try:
        if not form['name'].strip() or not NAME_RE.fullmatch(form['name'].strip()):
            raise ValueError('name must match ^[a-z0-9][a-z0-9_-]{2,120}$')
        if not form['source_url'].strip() or not _is_avito_url(form['source_url'].strip()):
            raise ValueError('source_url must be a valid avito.ru URL')
        poll = int(form['poll_interval_sec'])
        if poll <= 0:
            raise ValueError('poll_interval_sec must be a positive integer')
        filters = _extract_filters(form, 'require_published_at' in form)
    except (ValueError, TypeError) as exc:
        return _render_page('Validation error', f"<h1>Edit search #{search_id}</h1><form method='post' action='/admin/searches/{search_id}'>{_job_form(job, str(exc))}<button type='submit'>Save</button></form>")
    job.name = form['name'].strip()
    job.source_url = form['source_url'].strip()
    job.poll_interval_sec = poll
    job.filters_json = filters
    job.is_active = 'is_active' in form
    db.commit()
    return RedirectResponse('/admin/searches', status_code=303)

@router.post('/searches/{search_id}/activate', dependencies=[Depends(_require_admin_api_key)])
def activate(search_id: int, db: Session = Depends(get_db)):
    job = SearchRepository(db).get(search_id)
    if job is None:
        raise HTTPException(404)
    job.is_active = True
    db.commit()
    return RedirectResponse('/admin/searches', status_code=303)

@router.post('/searches/{search_id}/deactivate', dependencies=[Depends(_require_admin_api_key)])
def deactivate(search_id: int, db: Session = Depends(get_db)):
    job = SearchRepository(db).get(search_id)
    if job is None:
        raise HTTPException(404)
    job.is_active = False
    db.commit()
    return RedirectResponse('/admin/searches', status_code=303)

@router.post('/searches/{search_id}/reset-baseline', dependencies=[Depends(_require_admin_api_key)])
def reset_baseline(search_id: int, db: Session = Depends(get_db)):
    job = SearchRepository(db).get(search_id)
    if job is None:
        raise HTTPException(404)
    job.baseline_initialized = False
    job.baseline_initialized_at = None
    job.next_run_at = None
    db.commit()
    return RedirectResponse('/admin/searches', status_code=303)

@router.post('/searches/{search_id}/run-once', response_class=HTMLResponse, dependencies=[Depends(_require_admin_api_key)])
def run_once(search_id: int):
    service = MonitorService(parser=_build_parser())
    started_at = datetime.now(UTC)
    try:
        result = service.run_once(search_id)
    except ParserError as exc:
        result = {"ok": False, "search_id": search_id, "error_type": exc.error_type.value, "error": str(exc)}
    except Exception as exc:
        result = {"ok": False, "search_id": search_id, "error_type": exc.__class__.__name__, "error": str(exc)}
    result["started_at"] = started_at.isoformat()
    return _render_page('Run once', f"<h1>Run once result</h1><pre>{html.escape(json.dumps(result, ensure_ascii=False, indent=2))}</pre><p><a href='/admin/searches'>Back</a></p>")
