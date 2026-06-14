from __future__ import annotations

import html
import json
import re
import time
from pathlib import Path
from datetime import datetime
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Security, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import APIKeyHeader
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.cli import _build_parser, _parser_stats_snapshot
from app.core.config import settings
from app.db.session import get_db
from app.models.listing import Listing
from app.models.listing_analysis import ListingAnalysis
from app.models.human_review import HUMAN_VERDICTS, NEXT_ACTIONS, OUTCOME_STATUSES, HumanReviewAction
from app.parsers.errors import ParserError
from app.repositories.search_repository import SearchRepository
from app.services.monitor_service import MonitorService, runtime_diagnostics
from app.services.human_reviews import HumanReviewService, HumanReviewValidationError, build_review_context_key
from app.workers.status import read_worker_status, summarize_worker_status

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,120}$")
FRESHNESS_PRESETS = {"12": 12.0, "24": 24.0, "48": 48.0, "72": 72.0}
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
router = APIRouter(prefix="/admin", tags=["admin"])

UI_TEXT = {
    "ru": {
        "nav.dashboard": "Панель", "nav.listings": "Объекты", "nav.searches": "Поиски",
        "nav.alerts": "Уведомления", "nav.analyses": "Анализы", "nav.system": "Состояние",
        "nav.technical": "Технический режим", "dashboard.title": "Панель оператора",
        "no_data": "Нет данных", "technical.details": "Технические детали",
    },
    "en": {
        "nav.dashboard": "Dashboard", "nav.listings": "Listings", "nav.searches": "Searches",
        "nav.alerts": "Alerts", "nav.analyses": "Analyses", "nav.system": "System status",
        "nav.technical": "Technical mode", "dashboard.title": "Operator dashboard",
        "no_data": "No data", "technical.details": "Technical details",
    },
}
_SECRET_KEY_RE = re.compile(r"(key|token|secret|password|webhook|authorization|api_key|smtp_password|telegram_bot_token|google_sheets_webhook_secret)", re.I)

def _lang() -> str:
    return settings.admin_ui_language if settings.admin_ui_language in UI_TEXT else "ru"

def _t(key: str) -> str:
    return UI_TEXT.get(_lang(), UI_TEXT["ru"]).get(key, UI_TEXT["en"].get(key, key))

def truncate_admin_text(value: object, limit: int = 500) -> str:
    text = str(value or "")
    return text if len(text) <= limit else f"{text[: max(0, limit - 1)]}…"

def redact_admin_value(value: object, key: str = "") -> str:
    if _SECRET_KEY_RE.search(key or ""):
        return "[redacted]"
    text = str(value or "")
    if "script.google.com" in text:
        return "https://script.google.com/.../exec"
    return truncate_admin_text(text, 500)

def _redact_obj(value: object, key: str = "") -> object:
    if isinstance(value, dict):
        return {str(k): _redact_obj(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_obj(v, key) for v in value[:50]]
    if _SECRET_KEY_RE.search(key or ""):
        return "[redacted]"
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return redact_admin_value(value, key)

def redact_admin_json(value: object) -> str:
    return html.escape(json.dumps(_redact_obj(value or {}), ensure_ascii=False, indent=2, sort_keys=True))

def display_boolean(value: object, lang: str | None = None) -> str:
    ru = (lang or _lang()) == "ru"
    return ("Да" if value else "Нет") if ru else ("Yes" if value else "No")

def _display_mapped(value: object, mapping: dict[str, str], lang: str | None = None) -> str:
    text = str(value or "")
    if not text:
        return "—"
    if text in mapping:
        return mapping[text]
    prefix = "Неизвестный статус" if (lang or _lang()) == "ru" else "Unknown status"
    return f"{prefix}: {html.escape(text)}"

def display_verdict(value: object, lang: str | None = None) -> str:
    return _display_mapped(value, {"strong": "Интересно", "review": "Нужно проверить", "weak": "Слабый объект", "reject": "Отклонено", "error": "Ошибка"}, lang)

def display_review_status(value: object, lang: str | None = None) -> str:
    return _display_mapped(value, {"needs_review": "Требует проверки", "approved": "Одобрено", "rejected": "Отклонено", "false_positive": "Ложное срабатывание"}, lang)

def display_outcome_status(value: object, lang: str | None = None) -> str:
    return _display_mapped(value, OUTCOME_STATUS_LABELS_RU, lang)

HUMAN_VERDICT_LABELS_RU = {
    "interesting": "Интересно",
    "neutral": "Нейтрально",
    "not_interesting": "Не интересно",
    "false_positive": "Ложное срабатывание",
    "false_negative": "Пропущенная возможность",
    "needs_more_data": "Нужны данные",
}
OUTCOME_STATUS_LABELS_RU = {
    "not_started": "Не начато",
    "contacted_owner": "Связались с владельцем",
    "waiting_response": "Ждём ответ",
    "documents_requested": "Запрошены документы",
    "sent_to_expert": "Отправлено эксперту",
    "under_review": "На проверке",
    "rejected_after_call": "Отклонено после звонка",
    "watchlist": "В наблюдении",
    "deal_candidate": "Кандидат в сделку",
    "offer_made": "Сделано предложение",
    "deal_lost": "Сделка потеряна",
    "deal_done": "Сделка состоялась",
    "closed": "Закрыто",
}
NEXT_ACTION_LABELS_RU = {
    "open_listing": "Открыть объявление",
    "call_owner": "Позвонить владельцу",
    "request_documents": "Запросить документы",
    "run_market_research": "Запустить исследование рынка",
    "run_data_quality_review": "Проверить качество данных",
    "send_to_expert": "Отправить эксперту",
    "add_to_watchlist": "Добавить в наблюдение",
    "reject": "Отклонить",
    "do_nothing": "Ничего не делать",
}

def display_human_verdict(value: object, lang: str | None = None) -> str:
    return _display_mapped(value, HUMAN_VERDICT_LABELS_RU, lang)

def display_next_action(value: object, lang: str | None = None) -> str:
    return _display_mapped(value, NEXT_ACTION_LABELS_RU, lang)

def display_risk_flag(value: object, lang: str | None = None) -> str:
    return _display_mapped(value, {"missing_area": "Не указана площадь", "stale_publication": "Объявление может быть старым", "suspicious_low_price_per_m2": "Подозрительно низкая цена за м²", "market_evidence_used": "Использованы рыночные ориентиры"}, lang)

def display_money(value: object) -> str:
    return "—" if value in (None, "") else f"{value} ₽"

def display_area(value: object) -> str:
    return "—" if value in (None, "") else f"{value} м²"

def display_datetime(value: object) -> str:
    return "—" if not value else html.escape(str(value))


def _configured_read_key() -> str:
    return settings.admin_ui_read_key or settings.admin_ui_write_key or settings.admin_ui_technical_write_key or settings.api_key

def _configured_write_key() -> str:
    return settings.admin_ui_write_key or settings.admin_ui_read_key or settings.api_key

def _configured_technical_key() -> str:
    return settings.admin_ui_technical_write_key or settings.admin_ui_write_key or settings.api_key

def _request_key_ok(key_header: str | None, api_key_qs: str | None, expected: str) -> bool:
    if not expected:
        return True
    if key_header == expected:
        return True
    return bool(settings.admin_ui_allow_query_api_key and api_key_qs == expected)

def _require_admin_api_key(
    key_header: str | None = Security(_api_key_header),
    api_key_qs: str | None = Query(default=None, alias="api_key"),
) -> None:
    expected = _configured_read_key()
    if _request_key_ok(key_header, api_key_qs, expected):
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid admin key")

def _require_technical_write(
    key_header: str | None = Security(_api_key_header),
    api_key_qs: str | None = Query(default=None, alias="api_key"),
) -> None:
    if not settings.admin_ui_technical_ops_enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Technical operations are disabled")
    expected = _configured_technical_key()
    if _request_key_ok(key_header, api_key_qs, expected):
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid technical admin key")

def _admin_url(path: str, api_key: str | None) -> str:
    if not api_key or not settings.admin_ui_allow_query_api_key:
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


def _admin_nav() -> str:
    links = [("/admin", "nav.dashboard"), ("/admin/listings", "nav.listings"), ("/admin/searches", "nav.searches"), ("/admin/alerts", "nav.alerts"), ("/admin/listing-analyses", "nav.analyses"), ("/admin/technical", "nav.technical")]
    return "<nav>" + " · ".join(f"<a href='{href}'>{html.escape(_t(key))}</a>" for href, key in links) + "</nav>"

def _render_page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}</title>
<style>body{{font-family:Arial,sans-serif;max-width:1200px;margin:1rem auto;padding:0 1rem;color:#17202a}}nav{{padding:.7rem 0;margin-bottom:1rem;border-bottom:1px solid #dfe3e8}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ccc;padding:.4rem;vertical-align:top}}input,textarea,select{{width:100%;padding:.35rem}}.row{{margin:.4rem 0}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:.7rem}}.card,.section{{border:1px solid #dfe3e8;border-radius:.4rem;padding:.7rem .8rem;margin:.7rem 0;background:#fafbfc}}.section h3{{margin:.1rem 0 .6rem 0}}.checkbox input{{width:auto;margin-right:.35rem}}.actions form{{display:inline-block;margin:.1rem}}.note{{background:#fff7d6;padding:.5rem;border:1px solid #e2c86f}}.error{{background:#ffdede;padding:.5rem;border:1px solid #d66}}.badge{{display:inline-block;padding:.12rem .4rem;border-radius:.35rem;font-size:.8rem;font-weight:600;margin:.08rem .15rem .08rem 0}}.badge-green{{background:#d9f7e6;color:#115c36;border:1px solid #94d6b1}}.badge-yellow{{background:#fff6d6;color:#745700;border:1px solid #f2d37c}}.badge-red{{background:#ffe1e1;color:#7a1212;border:1px solid #f2a5a5}}.badge-gray{{background:#eceef1;color:#3d4954;border:1px solid #c9ced4}}.preview{{font-size:.88rem;word-break:break-all;color:#53606b}}code{{font-size:.84rem}}</style></head><body>{_admin_nav()}{body}</body></html>""")

def _render_worker_cycle_status() -> str:
    status = read_worker_status(settings.monitor_worker_status_path)
    summary = summarize_worker_status(
        status,
        stale_after_seconds=settings.monitor_worker_stale_after_seconds,
    )
    payload = summary.get("payload") or {}
    badge = summary.get("badge") or {"label": "Unknown", "color": "gray"}
    cycle_ok = summary.get("cycle_ok")
    cycle_badge = _badge("Cycle OK", "green") if cycle_ok is True else (_badge("Cycle failed", "red") if cycle_ok is False else _badge("Cycle unknown", "gray"))
    age_seconds = summary.get("age_seconds")
    age_label = "—" if age_seconds is None else str(age_seconds)
    error_note = ""
    if summary.get("state") == "corrupt" and summary.get("error"):
        error_note = f"<br><strong>Status error:</strong> <span class='preview'>{html.escape(_truncate(summary.get('error'), 160))}</span>"

    engine_first = html.escape(str(payload.get("selected_first_engine") or "—"))
    engine_used = html.escape(str(payload.get("engine_used") or "—"))
    counter_labels = [
        "fallback_used",
        "browser_driver_crash_count",
        "browser_driver_crash_retry_attempt_count",
        "browser_driver_crash_retry_success_count",
        "close_failure_after_driver_crash_count",
        "engine_error_count",
        "timeout_failure_count",
        "block_detected_count",
        "proxy_failure_count",
        "session_open_count",
        "session_reuse_count",
        "session_evict_count",
        "session_close_failure_count",
        "layout_changed_hint",
    ]
    counters = "; ".join(
        f"{name}={html.escape(str(payload.get(name, '—')))}" for name in counter_labels
    )
    return (
        f"<strong>Worker status file:</strong> {_badge(str(badge.get('label', 'Unknown')), str(badge.get('color', 'gray')))} "
        f"state={html.escape(str(summary.get('state') or 'missing'))}<br>"
        f"<strong>Status path:</strong> <code>{html.escape(str(summary.get('path') or settings.monitor_worker_status_path))}</code><br>"
        f"<strong>Updated at:</strong> {html.escape(str(summary.get('updated_at') or '—'))}<br>"
        f"<strong>Age seconds:</strong> {html.escape(age_label)} "
        f"<strong>Stale after seconds:</strong> {html.escape(str(summary.get('stale_after_seconds')))}<br>"
        f"<strong>Cycle status:</strong> {cycle_badge} searches_processed={html.escape(str(payload.get('searches_processed', '—')))}<br>"
        f"<strong>Engines:</strong> selected_first_engine={engine_first}; engine_used={engine_used}<br>"
        f"<strong>Parser counters:</strong> {counters}"
        f"{error_note}"
    )

def _truncate(value: object, limit: int = 120) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1]}…"


def _html_attr(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def _read_jsonl_alerts(path: str, search_name: str | None, limit: int) -> tuple[list[dict], int, int]:
    file_path = Path(path)
    if not file_path.exists() or not file_path.is_file():
        return [], 0, 0
    valid_records: list[dict] = []
    invalid_count = 0
    with file_path.open("r", encoding="utf-8") as infile:
        for raw in infile:
            line = raw.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                invalid_count += 1
                continue
            if isinstance(parsed, dict):
                valid_records.append(parsed)
    valid_records.reverse()
    if search_name:
        valid_records = [r for r in valid_records if str(r.get("search_name", "")) == search_name]
    return valid_records[:limit], len(valid_records), invalid_count


def _badge(text: str, level: str) -> str:
    return f"<span class='badge badge-{level}'>{html.escape(text)}</span>"


def _bool_label(value: object) -> str:
    return "yes" if bool(value) else "no"


def _delivery_badge(attempted: int, unsuccessful: int, failed: int, unknown: int) -> str:
    if attempted == 0:
        return _badge("neutral", "gray")
    if failed > 0 or unknown > 0:
        return _badge("warning", "yellow")
    if unsuccessful == 0:
        return _badge("success", "green")
    return _badge("warning", "yellow")


async def _parse_form(request: Request) -> dict[str, str]:
    data = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
    return {k: v[-1] if v else "" for k, v in data.items()}


def _safe_external_link(url: str | None, label: str = "open") -> str:
    raw = str(url or "").strip()
    if not raw:
        return "—"
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return html.escape(raw)
    return f"<a href='{_html_attr(raw)}' target='_blank' rel='noopener noreferrer'>{html.escape(label)}</a>"


def _latest_successful_analysis(db: Session, listing: Listing) -> ListingAnalysis | None:
    return db.scalar(
        select(ListingAnalysis)
        .where(ListingAnalysis.listing_external_id == listing.external_id, ListingAnalysis.status == "success")
        .order_by(ListingAnalysis.created_at.desc(), ListingAnalysis.id.desc())
        .limit(1)
    )


def _admin_listing_context_key(listing: Listing, analysis: ListingAnalysis | None) -> str:
    return build_review_context_key(
        listing.external_id,
        search_job_id=None,
        listing_analysis_id=analysis.id if analysis else None,
        context_type="admin_listing_detail",
    )


def _admin_listing_review(service: HumanReviewService, listing: Listing, analysis: ListingAnalysis | None):
    preferred = service.get_review_by_context_key(_admin_listing_context_key(listing, analysis))
    if preferred:
        return preferred
    for review in service.list_reviews(listing_external_id=listing.external_id):
        if str(review.review_context_key or "").endswith(":context:admin_listing_detail"):
            return review
    return None


def _require_admin_write_form(request: Request, form: dict[str, str]) -> None:
    expected = _configured_write_key()
    if not expected:
        return
    header_key = request.headers.get("X-API-Key")
    form_key = form.pop("admin_write_key", None)
    # Query-string keys intentionally remain governed by ADMIN_UI_ALLOW_QUERY_API_KEY.
    query_key = request.query_params.get("api_key") if settings.admin_ui_allow_query_api_key else None
    if header_key == expected or form_key == expected or query_key == expected:
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid admin write key")


def _parse_bool_field(value: str | None) -> bool:
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"", "0", "false", "no", "off"}:
        return False
    raise ValueError("watchlist must be boolean")


def _validate_review_form(form: dict[str, str]) -> dict[str, object]:
    human_verdict = (form.get("human_verdict") or "").strip() or None
    outcome_status = (form.get("outcome_status") or "").strip() or None
    next_action = (form.get("next_action") or "").strip() or None
    if human_verdict is not None and human_verdict not in HUMAN_VERDICTS:
        raise ValueError("invalid human_verdict")
    if outcome_status is not None and outcome_status not in OUTCOME_STATUSES:
        raise ValueError("invalid outcome_status")
    if next_action is not None and next_action not in NEXT_ACTIONS:
        raise ValueError("invalid next_action")
    notes = form.get("notes") or None
    if notes is not None and len(notes) > 5000:
        raise ValueError("notes is too long")
    return {
        "human_verdict": human_verdict,
        "outcome_status": outcome_status,
        "next_action": next_action,
        "watchlist": _parse_bool_field(form.get("watchlist")),
        "notes": notes,
        "reviewer": "admin_ui",
    }


def _select_options(values: set[str], labels: dict[str, str], selected: str | None) -> str:
    options = ["<option value=''>—</option>"]
    for value in sorted(values):
        options.append(
            f"<option value='{_html_attr(value)}' {'selected' if value == selected else ''}>{html.escape(labels.get(value, value))} ({html.escape(value)})</option>"
        )
    return "".join(options)


def _job_form(job=None, error: str = "", return_url: str = "") -> str:
    filters = (getattr(job, "filters_json", {}) if job else {}) or {}

    def v(name, default=""):
        if job and hasattr(job, name):
            return getattr(job, name)
        return filters.get(name, default)
    def fv(name, default=""):
        if job and hasattr(job, name):
            return getattr(job, name)
        return filters.get(name, default)

    def _selected(value: str, current: str) -> str:
        return "selected" if value == current else ""

    def _freshness_preset_value() -> str:
        explicit = str(fv("freshness_preset", "")).strip()
        if explicit:
            return explicit
        max_age = fv("max_age_hours", "")
        try:
            parsed = float(str(max_age).strip())
        except (TypeError, ValueError):
            return "custom"
        for preset_key, preset_hours in FRESHNESS_PRESETS.items():
            if parsed == preset_hours:
                return preset_key
        return "custom"

    checked_active = "checked" if (getattr(job, "is_active", True) if job else True) else ""
    checked_req_pub = "checked" if filters.get("require_published_at") else ""
    freshness_preset_value = _freshness_preset_value()
    profile_value = str(fv("profile", "production"))
    category_value = str(fv("category", ""))
    city_value = str(fv("city", ""))
    seller_value = str(fv("seller", ""))
    floor_value = str(fv("floor", ""))
    analysis_profile_value = str(fv("analysis_profile", ""))
    asset_type_value = str(fv("asset_type", ""))
    deal_type_value = str(fv("deal_type", ""))
    missing_published_at_policy_value = str(fv("missing_published_at_policy", ""))
    source_sort_value = str(fv("source_sort", ""))
    return f"""{'<div class="error">'+html.escape(error)+'</div>' if error else ''}
<input type='hidden' name='return_url' value='{html.escape(return_url)}'>
<div class='section'><h3>Basic</h3>
<div class='row'><label>Human title<input name='human_title' value='{html.escape(str(fv("human_title", "")))}'></label></div>
<div class='row'><label>Technical name<input name='name' value='{html.escape(str(v("name", "")))}' required></label><div class='preview'>Latin letters, digits, _ and -, 3-121 chars. Existing legacy names may remain unchanged when not renamed.</div></div>
<div class='row checkbox'><label><input type='checkbox' name='is_active' {checked_active}> Active</label></div></div>
<div class='section'><h3>Avito source</h3>
<div class='row'><label>Avito search URL<textarea name='source_url' rows='3' required>{html.escape(str(v("source_url", "")))}</textarea></label></div>
<div class='note'>Настройте основные фильтры на Avito, затем вставьте URL сюда. Ограничения вроде собственника и первого этажа пока надежнее задавать в URL Avito.</div></div>
<div class='section'><h3>Internal filters</h3>
<div class='note'>analysis_profile controls which specialized analysis provider is used by <code>analyze-search-matches</code>. <code>commercial_rent</code>, <code>flat_sale</code>, and <code>flat_rent</code> are currently available deterministic providers; missing analysis_profile uses the <code>default</code> fallback. analysis_profile does not affect parsing or alert delivery. Analysis is search-aware and uses <code>listing_search_matches</code>.</div>
<div class='row'><label>Analysis profile<select name='analysis_profile'><option value='' {_selected('', analysis_profile_value)}>empty / default fallback</option><option value='default' {_selected('default', analysis_profile_value)}>default</option><option value='commercial_rent' {_selected('commercial_rent', analysis_profile_value)}>commercial_rent</option><option value='flat_sale' {_selected('flat_sale', analysis_profile_value)}>flat_sale</option><option value='flat_rent' {_selected('flat_rent', analysis_profile_value)}>flat_rent</option></select></label></div>
<div class='row'><label>Asset type<select name='asset_type'><option value='' {_selected('', asset_type_value)}>empty / not specified</option><option value='commercial' {_selected('commercial', asset_type_value)}>commercial</option><option value='flat' {_selected('flat', asset_type_value)}>flat</option></select></label></div>
<div class='row'><label>Deal type<select name='deal_type'><option value='' {_selected('', deal_type_value)}>empty / not specified</option><option value='rent' {_selected('rent', deal_type_value)}>rent</option><option value='sale' {_selected('sale', deal_type_value)}>sale</option></select></label></div>
<div class='row'><label>Freshness preset<select name='freshness_preset'><option value='custom' {_selected('custom', freshness_preset_value)}>custom</option><option value='12' {_selected("12", freshness_preset_value)}>12 hours</option><option value='24' {_selected("24", freshness_preset_value)}>24 hours</option><option value='48' {_selected("48", freshness_preset_value)}>48 hours</option><option value='72' {_selected("72", freshness_preset_value)}>72 hours</option></select></label></div>
<div class='row'><label>Freshness, hours<input name='max_age_hours' value='{html.escape(str(fv("max_age_hours", "")))}'></label></div>
<div class='row checkbox'><label><input type='checkbox' name='require_published_at' {checked_req_pub}> Require publication date</label></div>
<div class='row'><label>Missing published_at policy<select name='missing_published_at_policy'><option value='' {_selected('', missing_published_at_policy_value)}>empty / default (reject)</option><option value='reject' {_selected('reject', missing_published_at_policy_value)}>reject</option><option value='allow' {_selected('allow', missing_published_at_policy_value)}>allow</option><option value='allow_when_date_sorted' {_selected('allow_when_date_sorted', missing_published_at_policy_value)}>allow_when_date_sorted</option></select></label></div>
<div class='row'><label>Source sort<select name='source_sort'><option value='' {_selected('', source_sort_value)}>empty / not specified</option><option value='date' {_selected('date', source_sort_value)}>date</option></select></label></div>
<div class='row'><label>Price from<input name='min_price' value='{html.escape(str(fv("min_price", "")))}'></label></div>
<div class='row'><label>Price to<input name='max_price' value='{html.escape(str(fv("max_price", "")))}'></label></div>
<div class='row'><label>Area from<input name='min_area' value='{html.escape(str(fv("min_area", "")))}'></label></div>
<div class='row'><label>Area to<input name='max_area' value='{html.escape(str(fv("max_area", "")))}'></label></div>
<div class='row'><label>Include keywords<input name='include_keywords' value='{html.escape(",".join(fv("include_keywords", [])) if isinstance(fv("include_keywords", []), list) else str(fv("include_keywords", "")))}'></label></div>
<div class='row'><label>Exclude keywords<input name='exclude_keywords' value='{html.escape(",".join(fv("exclude_keywords", [])) if isinstance(fv("exclude_keywords", []), list) else str(fv("exclude_keywords", "")))}'></label></div>
<div class='row'><label>Location keywords<input name='location_keywords' value='{html.escape(",".join(fv("location_keywords", [])) if isinstance(fv("location_keywords", []), list) else str(fv("location_keywords", "")))}'></label><div class='preview'>Keywords are comma-separated.</div></div></div>
<div class='section'><h3>Metadata</h3><div class='preview'>seller/floor/category/city are metadata for now unless current code supports them directly. Owner/first floor should still be controlled by Avito URL.</div>
<div class='row'><label>Profile<select name='profile'><option value='production' {_selected('production', profile_value)}>production</option><option value='smoke' {_selected('smoke', profile_value)}>smoke</option><option value='test' {_selected('test', profile_value)}>test</option></select></label></div>
<div class='row'><label>Category<select name='category'><option value='' {_selected('', category_value)}>empty / not specified</option><option value='flats_sale' {_selected('flats_sale', category_value)}>flats_sale</option><option value='flats_rent' {_selected('flats_rent', category_value)}>flats_rent</option><option value='commercial' {_selected('commercial', category_value)}>commercial</option></select></label></div>
<div class='row'><label>City<select name='city'><option value='' {_selected('', city_value)}>empty / not specified</option><option value='spb' {_selected('spb', city_value)}>spb</option><option value='kudrovo' {_selected('kudrovo', city_value)}>kudrovo</option><option value='murino' {_selected('murino', city_value)}>murino</option><option value='len_oblast' {_selected('len_oblast', city_value)}>len_oblast</option></select></label></div>
<div class='row'><label>Seller<select name='seller'><option value='' {_selected('', seller_value)}>empty / not specified</option><option value='owner' {_selected('owner', seller_value)}>owner</option><option value='agency' {_selected('agency', seller_value)}>agency</option><option value='any' {_selected('any', seller_value)}>any</option></select></label></div>
<div class='row'><label>Floor<select name='floor'><option value='' {_selected('', floor_value)}>empty / not specified</option><option value='first' {_selected('first', floor_value)}>first</option><option value='not_first' {_selected('not_first', floor_value)}>not_first</option><option value='any' {_selected('any', floor_value)}>any</option></select></label></div></div>
<div class='section'><h3>Runtime</h3><div class='row'><label>Check interval, seconds<input name='poll_interval_sec' type='number' min='1' value='{html.escape(str(v("poll_interval_sec", 180)))}'></label></div></div>
"""


def _extract_filters(form: dict[str, str], require_published_at: bool) -> dict:
    out = {}
    if form["human_title"].strip():
        out["human_title"] = form["human_title"].strip()
    freshness_preset = form.get("freshness_preset", "custom").strip()
    for n in ("min_price", "max_price", "min_area", "max_area"):
        num = _num(form[n], n)
        if num is not None:
            out[n] = num
    if freshness_preset in FRESHNESS_PRESETS:
        out["max_age_hours"] = FRESHNESS_PRESETS[freshness_preset]
    else:
        max_age_hours = _num(form["max_age_hours"], "max_age_hours")
        if max_age_hours is not None:
            out["max_age_hours"] = max_age_hours
    if require_published_at:
        out["require_published_at"] = True
    for n in ("include_keywords", "exclude_keywords", "location_keywords"):
        kws = _keywords(form[n])
        if kws is not None:
            out[n] = kws
    for n in ("analysis_profile", "asset_type", "deal_type", "profile", "category", "city", "seller", "floor"):
        if form[n].strip():
            out[n] = form[n].strip()
    missing_published_at_policy = form.get("missing_published_at_policy", "").strip()
    if missing_published_at_policy:
        if missing_published_at_policy not in {"reject", "allow", "allow_when_date_sorted"}:
            raise ValueError("missing_published_at_policy must be one of: reject, allow, allow_when_date_sorted")
        out["missing_published_at_policy"] = missing_published_at_policy
    source_sort = form.get("source_sort", "").strip()
    if source_sort:
        if source_sort != "date":
            raise ValueError("source_sort must be empty or date")
        out["source_sort"] = source_sort
    return out


@router.get("", response_class=HTMLResponse, dependencies=[Depends(_require_admin_api_key)])
def dashboard(db: Session = Depends(get_db)):
    def count(model):
        try:
            return str(db.query(model).count())
        except Exception:
            return _t("no_data")
    cards = [
        ("Новые объекты", count(Listing)),
        ("Требуют решения", _t("no_data")),
        ("Интересные объекты", count(ListingAnalysis)),
        ("Отправлены эксперту", _t("no_data")),
        ("Отклонены", _t("no_data")),
        ("Ошибки мониторинга", _t("no_data")),
    ]
    cards_html = "".join(f"<div class='card'><h3>{html.escape(k)}</h3><p>{html.escape(v)}</p></div>" for k, v in cards)
    body = (
        f"<h1>{html.escape(_t('dashboard.title'))}</h1>"
        "<p>Операторская панель показывает состояние системы без изменения данных.</p>"
        f"<h2>Сегодня</h2><div class='cards'>{cards_html}</div>"
        "<h2>Быстрые переходы</h2><p><a href='/admin/listings'>Объекты</a> · <a href='/admin/searches'>Поиски</a> · "
        "<a href='/admin/alerts'>Уведомления</a> · <a href='/admin/technical'>Состояние системы и технический режим</a></p>"
    )
    return _render_page(_t('dashboard.title'), body)


@router.get("/technical", response_class=HTMLResponse, dependencies=[Depends(_require_admin_api_key)])
def technical():
    ops = display_boolean(settings.admin_ui_technical_ops_enabled)
    body = (
        "<h1>Технический режим</h1>"
        "<div class='note'>Эти действия могут изменить поведение мониторинга. Используйте только если понимаете эффект.</div>"
        f"<p><strong>Technical operations:</strong> {ops}<br>"
        f"<strong>Admin mode:</strong> {html.escape(settings.admin_ui_mode)}<br>"
        f"<strong>Language:</strong> {html.escape(_lang())}</p>"
        + ("<p>Технические действия выключены. Чтобы включить, установите <code>ADMIN_UI_TECHNICAL_OPS_ENABLED=true</code>.</p>" if not settings.admin_ui_technical_ops_enabled else "<p>Технические действия включены.</p>")
        + f"<section class='section'><h2>Состояние worker</h2>{_render_worker_cycle_status()}</section>"
        "<p><a href='/admin/searches'>Поиски и технические операции поиска</a> · <a href='/admin/listing-analyses'>Список анализов</a></p>"
    )
    return _render_page('Технический режим', body)


@router.get("/searches", response_class=HTMLResponse, dependencies=[Depends(_require_admin_api_key)])
def searches(request: Request, db: Session = Depends(get_db)):
    api_key = request.query_params.get("api_key")
    all_searches = SearchRepository(db).list_all()
    rows = []
    now = datetime.utcnow()
    for s in all_searches:
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
        if settings.admin_ui_technical_ops_enabled:
            toggle_action = 'deactivate' if s.is_active else 'activate'
            toggle_label = 'deactivate' if s.is_active else 'activate'
            action_html = (
                f"<details><summary>Technical actions</summary><div class='note'>These actions can change monitoring behavior. Use only if you understand the effect.</div>"
                f"<code>python3 -m app.cli run-once --search-id {s.id}</code><br><a href='{_admin_url(f'/admin/searches/{s.id}/edit', api_key)}'>edit</a> {open_avito}"
                f"<form method='post' action='{_admin_url(f'/admin/searches/{s.id}/' + toggle_action, api_key)}'><button>{toggle_label}</button></form>"
                f"<form method='post' action='{_admin_url(f'/admin/searches/{s.id}/reset-baseline', api_key)}'><button>reset baseline</button></form>"
                f"<form method='post' action='{_admin_url(f'/admin/searches/{s.id}/run-once', api_key)}'><button>run once</button></form></details>"
            )
        else:
            action_html = "<span class='preview'>Технические действия выключены</span>"
        rows.append(
            f"<tr><td>{s.id}</td><td>{html.escape(str((s.filters_json or {}).get('human_title') or s.name))}<div class='preview'>technical name: {html.escape(s.name)} · {source_url_preview}</div></td><td>{status_badges}</td><td>{s.fail_count}</td><td class='preview'>{last_error_preview}</td><td>{display_datetime(s.last_success_at)}</td><td>{next_run_cell}</td><td>{s.poll_interval_sec}</td><td class='actions'>{action_html}</td></tr>"
        )
    notice = ""
    if request.query_params.get("saved") == "1":
        notice = "<div class='note'>Saved successfully.</div>"
    elif request.query_params.get("updated") == "1":
        notice = "<div class='note'>Updated successfully.</div>"
    active_searches = [s for s in all_searches if s.is_active]
    due_now_count = sum(1 for s in active_searches if s.next_run_at is None or s.next_run_at <= now)
    last_success = max((s.last_success_at for s in active_searches if s.last_success_at), default=None)
    recent_error = "—"
    for s in sorted(
        active_searches,
        key=lambda item: item.last_checked_at or datetime.min,
        reverse=True,
    ):
        if (s.last_error or "").strip():
            recent_error = _truncate(s.last_error, 160)
            break
    runtime = runtime_diagnostics()
    lock_path = settings.monitor_worker_lock_path
    lock_exists = Path(lock_path).exists()
    runtime_alert_channels_list = runtime.get("alert_channels") or []
    runtime_alert_channels = ", ".join(runtime_alert_channels_list)
    debug_dump_dir = Path(str(runtime.get("scrape_debug_dump_dir") or ""))
    debug_dump_count = "missing"
    if debug_dump_dir.exists() and debug_dump_dir.is_dir():
        debug_dump_count = str(sum(1 for _ in debug_dump_dir.iterdir() if _.is_file()))
    worker_cycle_status = _render_worker_cycle_status()
    runtime_block = (
        "<section><h2>Worker status</h2>"
        "<p>The worker is a separate long-running process. Admin UI does not start or stop it.</p>"
        f"<p><strong>Suggested command:</strong> <code>python3 -m app.workers.monitor</code><br>"
        f"<strong>Lock path:</strong> <code>{html.escape(lock_path)}</code><br>"
        f"<strong>Lock file:</strong> {'exists' if lock_exists else 'missing'}<br>"
        f"{worker_cycle_status}<br>"
        f"<strong>Runtime:</strong> alert_channels={html.escape(runtime_alert_channels or '—')}; "
        f"scoring_enabled={html.escape(str(runtime.get('scoring_enabled')))}; "
        f"scrape_preferred_engine={html.escape(str(runtime.get('scrape_preferred_engine')))}; "
        f"scrape_headless={html.escape(str(runtime.get('scrape_headless')))}; "
        f"scrape_timeout_retry_once={html.escape(str(runtime.get('scrape_timeout_retry_once')))}; "
        f"scrape_max_pages={html.escape(str(runtime.get('scrape_max_pages')))}<br>"
        f"<strong>Channels:</strong> configured={html.escape(runtime_alert_channels or '—')}; "
        f"jsonl channel_enabled={_bool_label('jsonl' in runtime_alert_channels_list)} jsonl_enabled={_bool_label(settings.jsonl_outbox_enabled)} path=<code>{html.escape(settings.jsonl_outbox_path)}</code>; "
        f"google_sheets channel_enabled={_bool_label('google_sheets' in runtime_alert_channels_list)} integration_enabled={_bool_label(settings.google_sheets_webhook_enabled)} webhook_url_set={_bool_label(settings.google_sheets_webhook_url)} secret_set={_bool_label(settings.google_sheets_webhook_secret)}; "
        f"email channel_enabled={_bool_label('email' in runtime_alert_channels_list)} email_enabled={_bool_label(settings.email_enabled)} smtp_host={html.escape(settings.smtp_host or '—')} smtp_port={html.escape(str(settings.smtp_port))} username_set={_bool_label(settings.smtp_username)} password_set={_bool_label(settings.smtp_password)} email_from_set={_bool_label(settings.email_from)} email_to_set={_bool_label(settings.email_to)}; "
        f"telegram channel_enabled={_bool_label('telegram' in runtime_alert_channels_list)} token_set={_bool_label(settings.telegram_bot_token)} chat_id_set={_bool_label(settings.telegram_chat_id)}<br>"
        f"<strong>Debug:</strong> scrape_debug_dump_html={html.escape(str(runtime.get('scrape_debug_dump_html')))}; "
        f"scrape_debug_dump_dir=<code>{html.escape(str(runtime.get('scrape_debug_dump_dir')))}</code>; "
        f"debug_dump_file_count={html.escape(debug_dump_count)}<br>"
        f"<strong>Active searches:</strong> {len(active_searches)}<br>"
        f"<strong>Due now:</strong> {due_now_count}<br>"
        f"<strong>Last success:</strong> {html.escape(str(last_success or '—'))}<br>"
        f"<strong>Last error:</strong> {html.escape(recent_error)}</p></section>"
    )
    technical_links = (f"<p><a href='{_admin_url('/admin/searches/new', api_key)}'>New search</a> · <a href='{_admin_url('/admin/alerts', api_key)}'>Alerts</a> · <a href='{_admin_url('/admin/listings', api_key)}'>Listings</a> · <a href='{_admin_url('/admin/listing-analyses', api_key)}'>Listing analyses</a></p>" if settings.admin_ui_technical_ops_enabled else "")
    return _render_page("Поиски", f"<h1>Поиски</h1>{notice}<p class='preview'>Технические операции скрыты и заблокированы по умолчанию.</p>{technical_links}{runtime_block}<table><tr><th>id</th><th>Название / источник</th><th>Статус</th><th>Ошибки</th><th>Последняя ошибка</th><th>Последний успех</th><th>Следующий запуск</th><th>Интервал, сек</th><th>Технические действия</th></tr>{''.join(rows)}</table>")


@router.get('/alerts', response_class=HTMLResponse, dependencies=[Depends(_require_admin_api_key)])
def alerts(request: Request, limit: int = Query(default=50), search_name: str | None = Query(default=None)):
    api_key = request.query_params.get("api_key")
    effective_limit = max(1, min(limit, 500))
    normalized_search_name = (search_name or "").strip() or None
    rows_data, total_loaded, invalid_count = _read_jsonl_alerts(settings.jsonl_outbox_path, normalized_search_name, effective_limit)
    rows = []
    for item in rows_data:
        link = str(item.get("url", ""))
        open_link = ""
        if link:
            open_link = f"<a href='{_html_attr(link)}' target='_blank' rel='noopener noreferrer'>open</a>"
        rows.append(
            f"<tr><td>{html.escape(_truncate(item.get('timestamp', ''), 40))}</td><td>{html.escape(_truncate(item.get('search_name', ''), 60))}</td>"
            f"<td>{html.escape(_truncate(item.get('title', ''), 100))}</td><td>{html.escape(_truncate(item.get('price', ''), 40))}</td>"
            f"<td>{html.escape(_truncate(item.get('area_m2', ''), 30))}</td><td>{html.escape(_truncate(item.get('address', ''), 100))}</td>"
            f"<td>{html.escape(_truncate(item.get('published_label', ''), 50))}</td><td>{html.escape(_truncate(item.get('llm_summary', ''), 140))}</td>"
            f"<td>{open_link}</td></tr>"
        )
    warning = f"<div class='note'>Skipped invalid JSONL lines: {invalid_count}</div>" if invalid_count else ""
    empty = "<p>No alerts found yet.</p>" if not rows else ""
    table = "" if not rows else f"<table><tr><th>timestamp</th><th>search_name</th><th>title</th><th>price</th><th>area_m2</th><th>address</th><th>published_label</th><th>llm_summary</th><th>url</th></tr>{''.join(rows)}</table>"
    form_action = _admin_url('/admin/alerts', api_key)
    current_limit = _html_attr(effective_limit)
    current_search = _html_attr(normalized_search_name)
    body = (
        f"<h1>История уведомлений</h1><p><a href='{_html_attr(_admin_url('/admin/searches', api_key))}'>Back to searches</a> · <a href='{_html_attr(_admin_url('/admin/listings', api_key))}'>Listings</a> · <a href='{_html_attr(_admin_url('/admin/listing-analyses', api_key))}'>Listing analyses</a></p>"
        f"<p>JSONL file: <code>{html.escape(settings.jsonl_outbox_path)}</code></p>"
        f"<p>Visible: {len(rows_data)} / Loaded: {total_loaded}</p>{warning}"
        f"<form method='get' action='{_html_attr(form_action)}'><input type='hidden' name='api_key' value='{_html_attr(api_key)}'>"
        f"<div class='row'><label>search_name<input name='search_name' value='{current_search}'></label></div>"
        f"<div class='row'><label>limit<input name='limit' type='number' min='1' max='500' value='{current_limit}'></label></div>"
        f"<button type='submit'>Apply</button></form>{empty}{table}"
    )
    return _render_page('Alerts', body)



def _json_pre(value: object) -> str:
    return redact_admin_json(value)


@router.get('/listing-analyses', response_class=HTMLResponse, dependencies=[Depends(_require_admin_api_key)])
def listing_analyses(
    request: Request,
    db: Session = Depends(get_db),
    profile: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias='status'),
    verdict: str | None = Query(default=None),
    search_job_id: str | None = Query(default=None),
    listing_external_id: str | None = Query(default=None),
    limit: int = Query(default=100),
):
    api_key = request.query_params.get('api_key')
    effective_limit = max(1, min(limit, 500))
    normalized_profile = (profile or '').strip() or None
    normalized_status = (status_filter or '').strip() or None
    normalized_verdict = (verdict or '').strip() or None
    normalized_listing_external_id = (listing_external_id or '').strip() or None
    search_job_id_value = (search_job_id or '').strip()
    normalized_search_job_id: int | None = None
    warning = ''
    if search_job_id_value:
        try:
            normalized_search_job_id = int(search_job_id_value)
        except ValueError:
            warning = (
                "<div class='note'>Ignored invalid search_job_id filter: "
                f"{html.escape(search_job_id_value)}. Please enter an integer.</div>"
            )

    stmt = select(ListingAnalysis, Listing).outerjoin(
        Listing,
        Listing.external_id == ListingAnalysis.listing_external_id,
    )
    if normalized_profile:
        stmt = stmt.where(ListingAnalysis.profile == normalized_profile)
    if normalized_status:
        stmt = stmt.where(ListingAnalysis.status == normalized_status)
    if normalized_verdict:
        stmt = stmt.where(ListingAnalysis.verdict == normalized_verdict)
    if normalized_search_job_id is not None:
        stmt = stmt.where(ListingAnalysis.search_job_id == normalized_search_job_id)
    if normalized_listing_external_id:
        stmt = stmt.where(ListingAnalysis.listing_external_id == normalized_listing_external_id)

    rows_data = db.execute(
        stmt.order_by(ListingAnalysis.created_at.desc(), ListingAnalysis.id.desc()).limit(effective_limit)
    ).all()

    rows = []
    for analysis, listing in rows_data:
        listing_block = '<span class="preview">No matching listing.</span>'
        if listing is not None:
            listing_link = (
                f"<div><a href='{_html_attr(listing.url)}' target='_blank' rel='noopener noreferrer'>{html.escape(listing.url)}</a></div>"
                if listing.url
                else ''
            )
            listing_block = (
                f"<div><strong>{html.escape(listing.title or '')}</strong></div>"
                f"<div>price={html.escape(str(listing.price or ''))}; area_m2={html.escape(str(listing.area_m2 or ''))}</div>"
                f"<div>{html.escape(listing.address or '')}</div>{listing_link}"
            )
        details = (
            "<details><summary>Technical details</summary>"
            f"<h4>report_md</h4><pre>{html.escape(analysis.report_md or '')}</pre>"
            f"<h4>facts</h4><pre>{_json_pre(analysis.facts_json)}</pre>"
            f"<h4>risks</h4><pre>{_json_pre(analysis.risks_json)}</pre>"
            f"<h4>questions</h4><pre>{_json_pre(analysis.questions_json)}</pre>"
            f"<h4>error</h4><pre>error_type: {html.escape(analysis.error_type or '')}\nerror_message: {html.escape(analysis.error_message or '')}</pre>"
            "</details>"
        )
        rows.append(
            f"<tr><td>{analysis.id}</td><td>{html.escape(str(analysis.search_job_id or ''))}</td>"
            f"<td>{html.escape(analysis.context_key or '')}</td><td>{html.escape(analysis.listing_external_id or '')}</td>"
            f"<td>{html.escape(analysis.profile or '')}</td><td>{html.escape(analysis.analysis_version or '')}</td>"
            f"<td>{html.escape(analysis.status or '')}</td><td>{html.escape(str(analysis.score if analysis.score is not None else ''))}</td>"
            f"<td>{html.escape(analysis.verdict or '')}</td><td>{html.escape(str(analysis.created_at or ''))}</td>"
            f"<td>{html.escape(str(analysis.updated_at or ''))}</td><td>{listing_block}</td><td>{details}</td></tr>"
        )

    empty = '<p>No listing analyses found yet.</p>' if not rows else ''
    table = '' if not rows else (
        "<table><tr><th>id</th><th>search_job_id</th><th>context_key</th><th>listing_external_id</th>"
        "<th>profile</th><th>analysis_version</th><th>status</th><th>score</th><th>verdict</th>"
        "<th>created_at</th><th>updated_at</th><th>listing</th><th>report</th></tr>"
        f"{''.join(rows)}</table>"
    )
    form_action = _admin_url('/admin/listing-analyses', api_key)
    body = (
        f"<h1>Listing analyses</h1><p><a href='{_html_attr(_admin_url('/admin/searches', api_key))}'>Back to searches</a> · "
        f"<a href='{_html_attr(_admin_url('/admin/listings', api_key))}'>Listings</a> · <a href='{_html_attr(_admin_url('/admin/listing-analyses', api_key))}'>Listing analyses</a></p>"
        "<p class='preview'>Read-only analysis report view. This page does not execute, edit, delete, or re-run analyses.</p>"
        f"<form method='get' action='{_html_attr(form_action)}'><input type='hidden' name='api_key' value='{_html_attr(api_key)}'>"
        f"<div class='row'><label>profile<input name='profile' value='{_html_attr(normalized_profile or '')}'></label></div>"
        f"<div class='row'><label>status<input name='status' value='{_html_attr(normalized_status or '')}'></label></div>"
        f"<div class='row'><label>verdict<input name='verdict' value='{_html_attr(normalized_verdict or '')}'></label></div>"
        f"<div class='row'><label>search_job_id<input name='search_job_id' type='number' value='{_html_attr(search_job_id_value)}'></label></div>"
        f"<div class='row'><label>listing_external_id<input name='listing_external_id' value='{_html_attr(normalized_listing_external_id or '')}'></label></div>"
        f"<div class='row'><label>limit<input name='limit' type='number' min='1' max='500' value='{_html_attr(effective_limit)}'></label></div>"
        f"<button type='submit'>Apply</button></form>{warning}{empty}{table}"
    )
    return _render_page('Listing analyses', body)




@router.get('/listings', response_class=HTMLResponse, dependencies=[Depends(_require_admin_api_key)])
def listings(request: Request, db: Session = Depends(get_db), limit: int = Query(default=100), q: str | None = Query(default=None), published: str = Query(default='any')):
    api_key = request.query_params.get('api_key')
    effective_limit = max(1, min(limit, 500))
    query_text = (q or '').strip()
    normalized_published = published if published in {'any', 'missing', 'present'} else 'any'

    stmt = select(Listing)
    if query_text:
        pattern = f"%{query_text}%"
        stmt = stmt.where(
            or_(
                Listing.title.ilike(pattern),
                Listing.address.ilike(pattern),
                Listing.external_id.ilike(pattern),
            )
        )
    if normalized_published == 'missing':
        stmt = stmt.where(or_(Listing.published_label.is_(None), Listing.published_label == ''))
    elif normalized_published == 'present':
        stmt = stmt.where(
            Listing.published_label.is_not(None),
            Listing.published_label != '',
        )

    items = db.scalars(stmt.order_by(Listing.last_seen_at.desc(), Listing.id.desc()).limit(effective_limit)).all()
    rows = []
    for item in items:
        detail_link = f"<a href='{_html_attr(_admin_url(f'/admin/listings/{item.id}', api_key))}'>детали</a>"
        open_link = _safe_external_link(item.url, "источник") if item.url else ''
        rows.append(
            f"<tr><td>{html.escape(str(item.id or ''))}<br>{detail_link}</td><td>{html.escape(item.external_id or '')}</td><td>{html.escape(item.title or '')}</td>"
            f"<td>{html.escape(str(item.price or ''))}</td><td>{html.escape(str(item.area_m2 or ''))}</td><td>{html.escape(item.address or '')}</td>"
            f"<td>{html.escape(item.published_label or '')}</td><td>{html.escape(str(item.first_seen_at or ''))}</td><td>{html.escape(str(item.last_seen_at or ''))}</td><td>{open_link}</td></tr>"
        )
    empty = '<p>No listings found yet.</p>' if not rows else ''
    table = '' if not rows else f"<table><tr><th>id</th><th>external_id</th><th>Title</th><th>Price</th><th>Area</th><th>Address</th><th>Publication</th><th>First seen</th><th>Last seen</th><th>Open source</th></tr>{''.join(rows)}</table>"
    form_action = _admin_url('/admin/listings', api_key)
    current_q = _html_attr(query_text)
    current_limit = _html_attr(effective_limit)
    body = (
        f"<h1>Объекты</h1><p><a href='{_html_attr(_admin_url('/admin/searches', api_key))}'>Back to searches</a> · <a href='{_html_attr(_admin_url('/admin/alerts', api_key))}'>Alerts</a> · <a href='{_html_attr(_admin_url('/admin/listing-analyses', api_key))}'>Listing analyses</a></p>"
        f"<form method='get' action='{_html_attr(form_action)}'><input type='hidden' name='api_key' value='{_html_attr(api_key)}'>"
        f"<div class='row'><label>q<input name='q' value='{current_q}'></label></div>"
        f"<div class='row'><label>published<select name='published'>"
        f"<option value='any' {'selected' if normalized_published == 'any' else ''}>any</option>"
        f"<option value='missing' {'selected' if normalized_published == 'missing' else ''}>missing</option>"
        f"<option value='present' {'selected' if normalized_published == 'present' else ''}>present</option>"
        f"</select></label></div>"
        f"<div class='row'><label>limit<input name='limit' type='number' min='1' max='500' value='{current_limit}'></label></div>"
        f"<button type='submit'>Apply</button></form>{empty}{table}"
    )
    return _render_page('Listings', body)


def _render_listing_detail_body(listing: Listing, analysis: ListingAnalysis | None, review, actions: list[HumanReviewAction], api_key: str | None, saved: bool = False, error: str = "") -> str:
    success = "<div class='note'>Решение сохранено.</div>" if saved else ""
    error_html = f"<div class='error'>{html.escape(error)}</div>" if error else ""
    core = (
        "<div class='section'><h3>Объявление</h3>"
        f"<p><strong>ID:</strong> {listing.id}<br><strong>external_id:</strong> {html.escape(listing.external_id or '')}<br>"
        f"<strong>Название:</strong> {html.escape(listing.title or '')}<br><strong>Цена:</strong> {display_money(listing.price)}<br>"
        f"<strong>Площадь:</strong> {display_area(listing.area_m2)}<br><strong>Адрес:</strong> {html.escape(listing.address or '')}<br>"
        f"<strong>Источник:</strong> {_safe_external_link(listing.url, listing.url)}<br><strong>Публикация:</strong> {html.escape(listing.published_label or '')}<br>"
        f"<strong>published_at:</strong> {display_datetime(listing.published_at)}<br><strong>first_seen_at:</strong> {display_datetime(listing.first_seen_at)}<br>"
        f"<strong>last_seen_at:</strong> {display_datetime(listing.last_seen_at)}<br><strong>Активно:</strong> {display_boolean(listing.is_active)}</p></div>"
    )
    if analysis:
        risk_flags = (analysis.risks_json or {}).get("flags") if isinstance(analysis.risks_json, dict) else None
        risks = ", ".join(display_risk_flag(x) for x in (risk_flags or [])) or "—"
        questions = redact_admin_json(analysis.questions_json or {})
        facts = redact_admin_json(analysis.facts_json or {})
        report = html.escape(_truncate(analysis.report_md or "", 700))
        analysis_html = (
            "<div class='section'><h3>Последний успешный детерминированный анализ</h3>"
            f"<p><strong>ID:</strong> {analysis.id}<br><strong>Профиль:</strong> {html.escape(analysis.profile or '')}<br>"
            f"<strong>Статус:</strong> {html.escape(analysis.status or '')}<br><strong>Оценка:</strong> {html.escape(str(analysis.score or '—'))}<br>"
            f"<strong>Вердикт:</strong> {display_verdict(analysis.verdict)} ({html.escape(str(analysis.verdict or '—'))})<br>"
            f"<strong>created_at:</strong> {display_datetime(analysis.created_at)}<br><strong>updated_at:</strong> {display_datetime(analysis.updated_at)}<br>"
            f"<strong>Риски:</strong> {risks}</p><p><strong>Краткий отчёт:</strong><br><span class='preview'>{report}</span></p>"
            f"<details><summary>{html.escape(_t('technical.details'))}: вопросы</summary><pre>{questions}</pre></details>"
            f"<details><summary>{html.escape(_t('technical.details'))}: факты</summary><pre>{facts}</pre></details></div>"
        )
    else:
        analysis_html = "<div class='section'><h3>Анализ</h3><p>Успешный детерминированный анализ не найден.</p></div>"
    if review:
        action_rows = "".join(
            f"<li>{display_datetime(a.created_at)} — {html.escape(a.action_type)} — {html.escape(_truncate(a.note, 160))}</li>"
            for a in actions
        )
        review_html = (
            "<div class='section'><h3>Текущее human review</h3>"
            f"<p><strong>ID:</strong> {review.id}<br><strong>review_status:</strong> {html.escape(review.review_status or '')}<br>"
            f"<strong>human_verdict:</strong> {display_human_verdict(review.human_verdict)} ({html.escape(str(review.human_verdict or '—'))})<br>"
            f"<strong>outcome_status:</strong> {display_outcome_status(review.outcome_status)} ({html.escape(str(review.outcome_status or '—'))})<br>"
            f"<strong>watchlist:</strong> {display_boolean(review.watchlist)}<br><strong>next_action:</strong> {display_next_action(review.next_action)} ({html.escape(str(review.next_action or '—'))})<br>"
            f"<strong>notes:</strong> {html.escape(review.notes or '—')}<br><strong>reviewed_at:</strong> {display_datetime(review.reviewed_at)}<br>"
            f"<strong>created_at:</strong> {display_datetime(review.created_at)}<br><strong>updated_at:</strong> {display_datetime(review.updated_at)}<br>"
            f"<strong>listing_analysis_id:</strong> {html.escape(str(review.listing_analysis_id or '—'))}<br><strong>search_job_id:</strong> {html.escape(str(review.search_job_id or '—'))}<br>"
            f"<strong>review_context_key:</strong> <code>{html.escape(review.review_context_key or '')}</code></p>"
            f"<details><summary>Последние действия</summary><ul>{action_rows or '<li>Нет действий</li>'}</ul></details></div>"
        )
    else:
        review_html = "<div class='section'><h3>Human review</h3><p>Решение оператора ещё не сохранено.</p></div>"
    selected_verdict = review.human_verdict if review else None
    selected_outcome = review.outcome_status if review else None
    selected_action = review.next_action if review else None
    checked = "checked" if (review and review.watchlist) else ""
    notes = _html_attr(review.notes if review else "")
    form = (
        f"<div class='section'><h3>Сохранить решение</h3><form method='post' action='{_html_attr(_admin_url(f'/admin/listings/{listing.id}/human-review', api_key))}'>"
        "<div class='row'><label>Ключ записи<input name='admin_write_key' type='password' autocomplete='off'></label></div>"
        f"<div class='row'><label>Вердикт<select name='human_verdict'>{_select_options(HUMAN_VERDICTS, HUMAN_VERDICT_LABELS_RU, selected_verdict)}</select></label></div>"
        f"<div class='row'><label>Статус исхода<select name='outcome_status'>{_select_options(OUTCOME_STATUSES, OUTCOME_STATUS_LABELS_RU, selected_outcome)}</select></label></div>"
        f"<div class='row'><label>Следующее действие<select name='next_action'>{_select_options(NEXT_ACTIONS, NEXT_ACTION_LABELS_RU, selected_action)}</select></label></div>"
        f"<div class='row checkbox'><label><input name='watchlist' type='checkbox' value='true' {checked}>В наблюдении</label></div>"
        f"<div class='row'><label>Комментарий<textarea name='notes' maxlength='5000'>{notes}</textarea></label></div>"
        "<button type='submit'>Сохранить</button></form></div>"
    )
    return f"<h1>Объект #{listing.id}</h1><p><a href='{_html_attr(_admin_url('/admin/listings', api_key))}'>Назад к объектам</a></p>{success}{error_html}{core}{analysis_html}{review_html}{form}"


@router.get('/listings/{listing_id}', response_class=HTMLResponse, dependencies=[Depends(_require_admin_api_key)])
def listing_detail(request: Request, listing_id: int, db: Session = Depends(get_db), saved: int | None = Query(default=None)):
    api_key = request.query_params.get('api_key')
    listing = db.get(Listing, listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail="Listing not found")
    analysis = _latest_successful_analysis(db, listing)
    service = HumanReviewService(db)
    review = _admin_listing_review(service, listing, analysis)
    actions = []
    if review:
        actions = list(db.scalars(select(HumanReviewAction).where(HumanReviewAction.human_review_id == review.id).order_by(HumanReviewAction.created_at.desc(), HumanReviewAction.id.desc()).limit(10)))
    return _render_page("Listing detail", _render_listing_detail_body(listing, analysis, review, actions, api_key, saved=bool(saved)))


@router.post('/listings/{listing_id}/human-review')
async def save_listing_human_review(request: Request, listing_id: int, db: Session = Depends(get_db)):
    form = await _parse_form(request)
    try:
        _require_admin_write_form(request, form)
        data = _validate_review_form(form)
        listing = db.get(Listing, listing_id)
        if listing is None:
            raise HTTPException(status_code=404, detail="Listing not found")
        analysis = _latest_successful_analysis(db, listing)
        service = HumanReviewService(db)
        review = _admin_listing_review(service, listing, analysis)
        if review:
            service.update_review(review.id, **data)
        else:
            service.create_review(
                listing_id=listing.id,
                listing_external_id=listing.external_id,
                listing_analysis_id=analysis.id if analysis else None,
                search_job_id=None,
                context_type="admin_listing_detail",
                review_status="reviewed" if data.get("human_verdict") else "needs_review",
                **data,
            )
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except (ValueError, HumanReviewValidationError) as exc:
        db.rollback()
        listing = db.get(Listing, listing_id)
        if listing is None:
            raise HTTPException(status_code=404, detail="Listing not found") from exc
        analysis = _latest_successful_analysis(db, listing)
        service = HumanReviewService(db)
        review = _admin_listing_review(service, listing, analysis)
        actions = []
        if review:
            actions = list(db.scalars(select(HumanReviewAction).where(HumanReviewAction.human_review_id == review.id).order_by(HumanReviewAction.created_at.desc(), HumanReviewAction.id.desc()).limit(10)))
        return HTMLResponse(_render_page("Validation error", _render_listing_detail_body(listing, analysis, review, actions, request.query_params.get('api_key'), error=str(exc))).body, status_code=400)
    return RedirectResponse(_admin_url(f'/admin/listings/{listing_id}?saved=1', request.query_params.get('api_key')), status_code=303)

@router.get('/searches/new', response_class=HTMLResponse, dependencies=[Depends(_require_admin_api_key)])
def new_search_form(request: Request):
    if not settings.admin_ui_technical_ops_enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Technical operations are disabled')
    api_key = request.query_params.get("api_key")
    return_url = _extract_return_url(request) or _admin_url('/admin/searches', api_key)
    return _render_page('New search', f"<h1>New search</h1><form method='post' action='{_admin_url('/admin/searches', api_key)}'>{_job_form(return_url=return_url)}<button type='submit'>Create</button></form>")


@router.post('/searches', dependencies=[Depends(_require_technical_write)])
async def create_search(request: Request, db: Session = Depends(get_db)):
    api_key = request.query_params.get("api_key")
    form = await _parse_form(request)
    form.setdefault('human_title', '')
    form.setdefault('name', '')
    form.setdefault('source_url', '')
    form.setdefault('poll_interval_sec', '180')
    for k in ('min_price', 'max_price', 'min_area', 'max_area', 'max_age_hours', 'freshness_preset', 'include_keywords', 'exclude_keywords', 'location_keywords', 'analysis_profile', 'asset_type', 'deal_type', 'profile', 'category', 'city', 'seller', 'floor', 'missing_published_at_policy', 'source_sort'):
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
    if not settings.admin_ui_technical_ops_enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Technical operations are disabled')
    api_key = request.query_params.get("api_key")
    job = SearchRepository(db).get(search_id)
    if job is None:
        raise HTTPException(404)
    return_url = _extract_return_url(request) or _admin_url('/admin/searches', api_key)
    return _render_page('Edit search', f"<h1>Edit search #{search_id}</h1><form method='post' action='{_admin_url(f'/admin/searches/{search_id}', api_key)}'>{_job_form(job, return_url=return_url)}<button type='submit'>Save</button></form>")


@router.post('/searches/{search_id}', dependencies=[Depends(_require_technical_write)])
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
    for k in ('min_price', 'max_price', 'min_area', 'max_area', 'max_age_hours', 'freshness_preset', 'include_keywords', 'exclude_keywords', 'location_keywords', 'analysis_profile', 'asset_type', 'deal_type', 'profile', 'category', 'city', 'seller', 'floor', 'missing_published_at_policy', 'source_sort'):
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


@router.post('/searches/{search_id}/activate', dependencies=[Depends(_require_technical_write)])
def activate(search_id: int, request: Request, db: Session = Depends(get_db)):
    api_key = request.query_params.get("api_key")
    job = SearchRepository(db).get(search_id)
    if job is None:
        raise HTTPException(404)
    job.is_active = True
    db.commit()
    return _success_redirect(request, api_key, 'updated')


@router.post('/searches/{search_id}/deactivate', dependencies=[Depends(_require_technical_write)])
def deactivate(search_id: int, request: Request, db: Session = Depends(get_db)):
    api_key = request.query_params.get("api_key")
    job = SearchRepository(db).get(search_id)
    if job is None:
        raise HTTPException(404)
    job.is_active = False
    db.commit()
    return _success_redirect(request, api_key, 'updated')


@router.post('/searches/{search_id}/reset-baseline', dependencies=[Depends(_require_technical_write)])
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


@router.post('/searches/{search_id}/run-once', response_class=HTMLResponse, dependencies=[Depends(_require_technical_write)])
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
            "runtime": runtime_diagnostics(),
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
            "runtime": runtime_diagnostics(),
        }
    parser_stats = result.get("parser_stats", {}) if isinstance(result, dict) else {}
    delivery_channels = sorted(
        set((result.get("delivery_attempted_by_channel") or {}).keys())
        | set((result.get("delivery_success_by_channel") or {}).keys())
        | set((result.get("delivery_skipped_by_channel") or {}).keys())
        | set((result.get("delivery_failed_by_channel") or {}).keys())
        | set((result.get("delivery_unknown_by_channel") or {}).keys())
        | set((result.get("delivery_unsuccessful_by_channel") or {}).keys())
    )
    summary_rows = [
        ("ok", result.get("ok")),
        ("error", result.get("error")),
        ("created", result.get("created")),
        ("alerted", result.get("alerted")),
        ("filtered", result.get("filtered")),
        ("total_seen", result.get("total_seen")),
        ("pages_seen", result.get("pages_seen")),
        ("pages_attempted", result.get("pages_attempted")),
        ("pagination_stopped_reason", result.get("pagination_stopped_reason")),
        ("page_errors_count", len(result.get("page_errors", []) or [])),
        ("parser_engine_used", parser_stats.get("engine_used")),
        ("layout_changed_hint", parser_stats.get("layout_changed_hint")),
        ("timeout_failure_count", parser_stats.get("timeout_failure_count")),
        ("proxy_quarantine_on_failure_count", parser_stats.get("proxy_quarantine_on_failure_count")),
    ]
    summary_table = "".join(
        f"<tr><td>{html.escape(str(k))}</td><td>{html.escape(str(v if v is not None else '—'))}</td></tr>"
        for k, v in summary_rows
    )
    delivery_table = ""
    if delivery_channels:
        delivery_rows = []
        for channel in delivery_channels:
            attempted = int((result.get("delivery_attempted_by_channel") or {}).get(channel, 0) or 0)
            success = int((result.get("delivery_success_by_channel") or {}).get(channel, 0) or 0)
            skipped = int((result.get("delivery_skipped_by_channel") or {}).get(channel, 0) or 0)
            failed = int((result.get("delivery_failed_by_channel") or {}).get(channel, 0) or 0)
            unknown = int((result.get("delivery_unknown_by_channel") or {}).get(channel, 0) or 0)
            unsuccessful = int((result.get("delivery_unsuccessful_by_channel") or {}).get(channel, 0) or 0)
            delivery_rows.append(
                f"<tr><td>{html.escape(channel)}</td><td>{attempted}</td><td>{success}</td><td>{skipped}</td><td>{failed}</td><td>{unknown}</td><td>{unsuccessful}</td><td>{_delivery_badge(attempted, unsuccessful, failed, unknown)}</td></tr>"
            )
        delivery_table = f"<h2>Delivery counters</h2><table><tr><th>channel</th><th>attempted</th><th>success</th><th>skipped</th><th>failed</th><th>unknown</th><th>unsuccessful</th><th>status</th></tr>{''.join(delivery_rows)}</table>"
    body = (
        "<h1>Run once result</h1>"
        f"<h2>Summary</h2><table><tr><th>metric</th><th>value</th></tr>{summary_table}</table>"
        f"{delivery_table}"
        f"<pre>{html.escape(json.dumps(result, ensure_ascii=False, indent=2))}</pre>"
        f"<p><a href='{_admin_url('/admin/searches', api_key)}'>Back</a></p>"
    )
    return _render_page('Run once', body)
