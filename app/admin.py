from __future__ import annotations

import html
import json
import re
import time
from datetime import datetime
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Security, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import APIKeyHeader
from sqlalchemy.orm import Session

from app.cli import _build_parser, _parser_stats_snapshot
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


def _admin_url(path: str, api_key: str | None) -> str:
    if not api_key:
        return path
    return _append_query_param(path, "api_key", api_key)




def _append_query_param(url: str, key: str, value: str | None) -> str:
    if not value:
        return url
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query[key] = [value]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def _safe_admin_return_url(raw_url: str | None) -> str | None:
    if not raw_url:
        return None
    parsed = urlparse(raw_url)
    if parsed.scheme or parsed.netloc:
        return None
    if not parsed.path.startswith('/admin'):
        return None
    return urlunparse(parsed._replace(fragment=''))


def _extract_return_url(request: Request, form: dict[str, str] | None = None) -> str | None:
    candidate = None
    if form is not None:
        candidate = form.get('return_url')
    if not candidate:
        candidate = request.query_params.get('return_url') or request.query_params.get('next')
    if not candidate:
        referer = request.headers.get('referer')
        if referer:
            parsed_ref = urlparse(referer)
            if not parsed_ref.scheme or not parsed_ref.netloc:
                candidate = referer
            else:
                host = request.url.hostname
                if parsed_ref.hostname == host:
                    candidate = urlunparse(('', '', parsed_ref.path, parsed_ref.params, parsed_ref.query, ''))
    return _safe_admin_return_url(candidate)


def _back_links(api_key: str | None, return_url: str | None) -> str:
    back_target = _append_query_param(return_url, 'api_key', api_key) if return_url else _admin_url('/admin/searches', api_key)
    list_target = _admin_url('/admin/searches', api_key)
    return f"<p><a href='{html.escape(back_target)}'>Back</a></p><p><a href='{html.escape(list_target)}'>Back to search list</a></p>"


def _success_redirect(request: Request, api_key: str | None, marker: str, form: dict[str, str] | None = None) -> RedirectResponse:
    target = _extract_return_url(request, form)
    if target:
        return RedirectResponse(_append_query_param(target, 'api_key', api_key), status_code=303)
    return RedirectResponse(_admin_url(f'/admin/searches?{marker}=1', api_key), status_code=303)

def _is_avito_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and (
        (parsed.hostname or "").lower() == "avito.ru" or (parsed.hostname or "").lower().endswith(".avito.ru")
    )


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
<style>body{{font-family:Arial,sans-serif;max-width:1100px;margin:1rem auto;padding:0 1rem}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ccc;padding:.4rem;vertical-align:top}}input,textarea{{width:100%;padding:.35rem}}.row{{margin:.4rem 0}}.actions form{{display:inline-block;margin:.1rem}}.note{{background:#fff7d6;padding:.5rem;border:1px solid #e2c86f}}.error{{background:#ffdede;padding:.5rem;border:1px solid #d66}}.badge{{display:inline-block;padding:.12rem .4rem;border-radius:.35rem;font-size:.8rem;font-weight:600;margin:.08rem .15rem .08rem 0}}.badge-green{{background:#d9f7e6;color:#115c36;border:1px solid #94d6b1}}.badge-yellow{{background:#fff6d6;color:#745700;border:1px solid #f2d37c}}.badge-red{{background:#ffe1e1;color:#7a1212;border:1px solid #f2a5a5}}.badge-gray{{background:#eceef1;color:#3d4954;border:1px solid #c9ced4}}.preview{{font-size:.88rem;word-break:break-all}}code{{font-size:.84rem}}</style></head><body>{body}</body></html>""")


def _badge(text: str, level: str) -> str:
    return f"<span class='badge badge-{level}'>{html.escape(text)}</span>"


async def _parse_form(request: Request) -> dict[str, str]:
    data = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
    return {k: v[-1] if v else "" for k, v in data.items()}


def _job_form(job=None, error: str = "", return_url: str = "") -> str:
    filters = (getattr(job, "filters_json", {}) if job else {}) or {}

    def v(name, default=""):
        if job and hasattr(job, name):
            return getattr(job, name)
        return filters.get(name, default)

    checked_active = "checked" if (getattr(job, "is_active", True) if job else True) else ""
    checked_req_pub = "checked" if filters.get("require_published_at") else ""
    return f"""{'<div class="error">'+html.escape(error)+'</div>' if error else ''}
<div class='row'>human_title<input name='human_title' value='{html.escape(str(filters.get("human_title", "")))}'></div>
<div class='row'>name<input name='name' value='{html.escape(str(v("name", "")))}' required><div class='preview'>Technical name. Use latin letters, digits, _ or -. Existing legacy names may remain unchanged.</div></div><input type='hidden' name='return_url' value='{html.escape(return_url)}'>
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
def searches(request: Request, db: Session = Depends(get_db)):
    api_key = request.query_params.get("api_key")
    rows = []
    now = datetime.utcnow()
    for s in SearchRepository(db).list_all():
        is_error = bool((s.last_error or "").strip()) or s.fail_count > 0
        is_due = s.is_active and (s.next_run_at is None or s.next_run_at <= now)
        is_waiting = s.is_active and not is_due
        status_badges = "".join(
            [
                _badge("Active", "green") if s.is_active else _badge("Inactive", "gray"),
                _badge("Baseline ready", "green") if s.baseline_initialized else _badge("Needs baseline", "yellow"),
                _badge("Error", "red") if is_error else _badge("Healthy", "green"),
                _badge("Due", "yellow") if is_due else (_badge("Waiting", "gray") if is_waiting else _badge("Waiting", "gray")),
            ]
        )
        last_error_preview = html.escape((s.last_error or "")[:160])
        source_url_preview = html.escape(s.source_url[:140])
        next_run_cell = f"{s.next_run_at or ''}{' ' + _badge('due now', 'yellow') if is_due else ''}"
        open_avito = (
            f"<a href='{html.escape(s.source_url)}' target='_blank' rel='noopener noreferrer'>open avito</a>"
            if s.source_url
            else ""
        )
        rows.append(
            f"<tr><td>{s.id}</td><td>{html.escape(s.name)}<div class='preview'>{source_url_preview}</div></td><td>{html.escape(str((s.filters_json or {}).get('human_title','')))}</td><td>{status_badges}</td><td>{s.fail_count}</td><td class='preview'>{last_error_preview}</td><td>{s.last_success_at or ''}</td><td>{next_run_cell}</td><td>{s.poll_interval_sec}</td><td><code>python3 -m app.cli run-once --search-id {s.id}</code></td><td class='actions'><a href='{_admin_url(f'/admin/searches/{s.id}/edit', api_key)}'>edit</a> {open_avito}<form method='post' action='{_admin_url(f'/admin/searches/{s.id}/{"deactivate" if s.is_active else "activate"}', api_key)}'><button>{'deactivate' if s.is_active else 'activate'}</button></form><form method='post' action='{_admin_url(f'/admin/searches/{s.id}/reset-baseline', api_key)}'><button>reset baseline</button></form><form method='post' action='{_admin_url(f'/admin/searches/{s.id}/run-once', api_key)}'><button>run once</button></form></td></tr>"
        )
    notice = ""
    if request.query_params.get("saved") == "1":
        notice = "<div class='note'>Saved successfully.</div>"
    elif request.query_params.get("updated") == "1":
        notice = "<div class='note'>Updated successfully.</div>"
    return _render_page("Searches", f"<h1>Searches</h1>{notice}<p><a href='{_admin_url('/admin/searches/new', api_key)}'>New search</a></p><table><tr><th>id</th><th>name / source</th><th>human_title</th><th>status</th><th>fail_count</th><th>last_error</th><th>last_success_at</th><th>next_run_at</th><th>poll_interval_sec</th><th>cli</th><th>actions</th></tr>{''.join(rows)}</table>")


@router.get('/searches/new', response_class=HTMLResponse, dependencies=[Depends(_require_admin_api_key)])
def new_search_form(request: Request):
    api_key = request.query_params.get("api_key")
    return_url = _extract_return_url(request) or _admin_url('/admin/searches', api_key)
    return _render_page('New search', f"<h1>New search</h1><form method='post' action='{_admin_url('/admin/searches', api_key)}'>{_job_form(return_url=return_url)}<button type='submit'>Create</button></form>")


@router.post('/searches', dependencies=[Depends(_require_admin_api_key)])
async def create_search(request: Request, db: Session = Depends(get_db)):
    api_key = request.query_params.get("api_key")
    form = await _parse_form(request)
    form.setdefault('human_title', '')
    form.setdefault('name', '')
    form.setdefault('source_url', '')
    form.setdefault('poll_interval_sec', '180')
    for k in ('min_price', 'max_price', 'min_area', 'max_area', 'max_age_hours', 'include_keywords', 'exclude_keywords', 'location_keywords', 'profile', 'category', 'city', 'seller', 'floor'):
        form.setdefault(k, '')
    try:
        name = form['name'].strip()
        if not name or not NAME_RE.fullmatch(name):
            raise ValueError('name must match ^[a-z0-9][a-z0-9_-]{2,120}$')
        if SearchRepository(db).get_by_name(name) is not None:
            raise ValueError('name already exists')
        if not form['source_url'].strip() or not _is_avito_url(form['source_url'].strip()):
            raise ValueError('source_url must be a valid avito.ru URL')
        poll = int(form['poll_interval_sec'])
        if poll <= 0:
            raise ValueError('poll_interval_sec must be a positive integer')
        filters = _extract_filters(form, 'require_published_at' in form)
    except (ValueError, TypeError) as exc:
        return_url = _extract_return_url(request, form) or _admin_url('/admin/searches', api_key)
        links = _back_links(api_key, _safe_admin_return_url(form.get('return_url')))
        return _render_page('Validation error', f"<h1>New search</h1><div class='error'>Nothing was saved because validation failed.</div>{links}<form method='post' action='{_admin_url('/admin/searches', api_key)}'>{_job_form(type('O',(),form), str(exc), return_url=return_url)}<button type='submit'>Create</button></form>")
    item = SearchRepository(db).create(name=name, source_url=form['source_url'].strip(), filters_json=filters, poll_interval_sec=poll)
    item.is_active = 'is_active' in form
    item.baseline_initialized = False
    item.fail_count = 0
    item.next_run_at = None
    db.commit()
    return _success_redirect(request, api_key, 'saved', form=form)


@router.get('/searches/{search_id}/edit', response_class=HTMLResponse, dependencies=[Depends(_require_admin_api_key)])
def edit_form(search_id: int, request: Request, db: Session = Depends(get_db)):
    api_key = request.query_params.get("api_key")
    job = SearchRepository(db).get(search_id)
    if job is None:
        raise HTTPException(404)
    return_url = _extract_return_url(request) or _admin_url('/admin/searches', api_key)
    return _render_page('Edit search', f"<h1>Edit search #{search_id}</h1><form method='post' action='{_admin_url(f'/admin/searches/{search_id}', api_key)}'>{_job_form(job, return_url=return_url)}<button type='submit'>Save</button></form>")


@router.post('/searches/{search_id}', dependencies=[Depends(_require_admin_api_key)])
async def update_search(search_id: int, request: Request, db: Session = Depends(get_db)):
    api_key = request.query_params.get("api_key")
    repo = SearchRepository(db)
    job = repo.get(search_id)
    if job is None:
        raise HTTPException(404)
    form = await _parse_form(request)
    form.setdefault('human_title', '')
    form.setdefault('name', '')
    form.setdefault('source_url', '')
    form.setdefault('poll_interval_sec', '180')
    for k in ('min_price', 'max_price', 'min_area', 'max_area', 'max_age_hours', 'include_keywords', 'exclude_keywords', 'location_keywords', 'profile', 'category', 'city', 'seller', 'floor'):
        form.setdefault(k, '')
    try:
        name = form['name'].strip()
        if not name:
            raise ValueError('name must match ^[a-z0-9][a-z0-9_-]{2,120}$')
        if name != job.name and not NAME_RE.fullmatch(name):
            raise ValueError('name must match ^[a-z0-9][a-z0-9_-]{2,120}$')
        conflict = repo.get_by_name(name)
        if conflict is not None and conflict.id != search_id:
            raise ValueError('name already exists')
        if not form['source_url'].strip() or not _is_avito_url(form['source_url'].strip()):
            raise ValueError('source_url must be a valid avito.ru URL')
        poll = int(form['poll_interval_sec'])
        if poll <= 0:
            raise ValueError('poll_interval_sec must be a positive integer')
        filters = _extract_filters(form, 'require_published_at' in form)
    except (ValueError, TypeError) as exc:
        return_url = _extract_return_url(request, form) or _admin_url('/admin/searches', api_key)
        links = _back_links(api_key, _safe_admin_return_url(form.get('return_url')))
        form_job = type('O', (), {**form, 'filters_json': job.filters_json, 'is_active': 'is_active' in form})
        return _render_page('Validation error', f"<h1>Edit search #{search_id}</h1><div class='error'>Nothing was saved because validation failed.</div>{links}<form method='post' action='{_admin_url(f'/admin/searches/{search_id}', api_key)}'>{_job_form(form_job, str(exc), return_url=return_url)}<button type='submit'>Save</button></form>")
    job.name = name
    job.source_url = form['source_url'].strip()
    job.poll_interval_sec = poll
    job.filters_json = filters
    job.is_active = 'is_active' in form
    db.commit()
    return _success_redirect(request, api_key, 'updated', form=form)


@router.post('/searches/{search_id}/activate', dependencies=[Depends(_require_admin_api_key)])
def activate(search_id: int, request: Request, db: Session = Depends(get_db)):
    api_key = request.query_params.get("api_key")
    job = SearchRepository(db).get(search_id)
    if job is None:
        raise HTTPException(404)
    job.is_active = True
    db.commit()
    return _success_redirect(request, api_key, 'updated')


@router.post('/searches/{search_id}/deactivate', dependencies=[Depends(_require_admin_api_key)])
def deactivate(search_id: int, request: Request, db: Session = Depends(get_db)):
    api_key = request.query_params.get("api_key")
    job = SearchRepository(db).get(search_id)
    if job is None:
        raise HTTPException(404)
    job.is_active = False
    db.commit()
    return _success_redirect(request, api_key, 'updated')


@router.post('/searches/{search_id}/reset-baseline', dependencies=[Depends(_require_admin_api_key)])
def reset_baseline(search_id: int, request: Request, db: Session = Depends(get_db)):
    api_key = request.query_params.get("api_key")
    job = SearchRepository(db).get(search_id)
    if job is None:
        raise HTTPException(404)
    job.baseline_initialized = False
    job.baseline_initialized_at = None
    job.next_run_at = None
    db.commit()
    return _success_redirect(request, api_key, 'updated')


@router.post('/searches/{search_id}/run-once', response_class=HTMLResponse, dependencies=[Depends(_require_admin_api_key)])
def run_once(search_id: int, request: Request):
    api_key = request.query_params.get("api_key")
    parser_instance = _build_parser()
    service = MonitorService(parser=parser_instance)
    started_at = time.perf_counter()
    try:
        result = service.run_once(search_id)
    except ParserError as exc:
        result = {
            "ok": False,
            "search_id": search_id,
            "error_type": exc.error_type.value,
            "error": str(exc),
            "elapsed_ms": int((time.perf_counter() - started_at) * 1000),
            "parser_stats": _parser_stats_snapshot(parser_instance),
        }
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        result = {
            "ok": False,
            "search_id": search_id,
            "error_type": exc.__class__.__name__,
            "error": str(exc),
            "elapsed_ms": int((time.perf_counter() - started_at) * 1000),
            "parser_stats": _parser_stats_snapshot(parser_instance),
        }
    return _render_page('Run once', f"<h1>Run once result</h1><pre>{html.escape(json.dumps(result, ensure_ascii=False, indent=2))}</pre><p><a href='{_admin_url('/admin/searches', api_key)}'>Back</a></p>")
