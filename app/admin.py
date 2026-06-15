from __future__ import annotations

import html
import json
import re
import time
from pathlib import Path
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Security, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import APIKeyHeader
from sqlalchemy import func, or_, select, text, tuple_
from sqlalchemy.orm import Session, raiseload

from app.cli import _build_parser, _parser_stats_snapshot
from app.core.config import settings
from app.db.session import get_db
from app.models.listing import Listing
from app.models.alert_delivery_attempt import AlertDeliveryAttempt
from app.models.alert_sent import AlertSent
from app.models.listing_analysis import ListingAnalysis
from app.models.search_job import SearchJob
from app.models.listing_detail_snapshot import ListingDetailSnapshot
from app.models.listing_enrichment import ListingEnrichment
from app.models.knowledge_note import KnowledgeNote
from app.models.human_review import HUMAN_VERDICTS, NEXT_ACTIONS, OUTCOME_STATUSES, HumanReview, HumanReviewAction, InvestmentDecision
from app.models.agent_task import AgentTask
from app.models.market_evidence import MarketEvidenceItem, MarketResearchRun
from app.models.monitor_cycle_run import MonitorCycleRun
from app.schemas.outcome_analytics import OutcomeAnalyticsRequest
from app.services.outcome_analytics import HumanOutcomeAnalyticsService
from app.parsers.errors import ParserError
from app.repositories.search_repository import SearchRepository
from app.services.monitor_service import MonitorService, runtime_diagnostics
from app.parsers.schemas import ListingCard
from app.repositories.alert_repository import AlertRepository
from app.repositories.alert_delivery_attempt_repository import AlertDeliveryAttemptRepository
from app.utils.formatting import build_listing_message
from app.services.alert_delivery_attempts import compute_alert_payload_hash
from app.services.alert_delivery_attempts import sanitize_alert_delivery_error
from app.services.human_reviews import HumanReviewService, HumanReviewValidationError, build_review_context_key
from app.workers.status import PARSER_STATUS_FIELDS, read_worker_status, summarize_worker_status

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,120}$")
FRESHNESS_PRESETS = {"12": 12.0, "24": 24.0, "48": 48.0, "72": 72.0}
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
router = APIRouter(prefix="/admin", tags=["admin"])

UI_TEXT = {
    "ru": {
        "nav.dashboard": "Панель", "nav.listings": "Объекты", "nav.searches": "Поиски",
        "nav.alerts": "Уведомления", "nav.analyses": "Анализы", "nav.system": "Состояние",
        "nav.technical": "Технический режим", "nav.evidence": "Рыночные данные", "nav.agents": "Агенты", "nav.outcome_analytics": "Аналитика решений", "dashboard.title": "Панель оператора",
        "no_data": "Нет данных", "technical.details": "Технические детали",
    },
    "en": {
        "nav.dashboard": "Dashboard", "nav.listings": "Listings", "nav.searches": "Searches",
        "nav.alerts": "Alerts", "nav.analyses": "Analyses", "nav.system": "System status",
        "nav.technical": "Technical mode", "nav.evidence": "Evidence", "nav.agents": "Agents", "nav.outcome_analytics": "Outcome analytics", "dashboard.title": "Operator dashboard",
        "no_data": "No data", "technical.details": "Technical details",
    },
}
_SECRET_KEY_RE = re.compile(r"(secret|token|api_key|apikey|authorization|auth|password|passwd|cookie|webhook|smtp|telegram|provider_key|access_key|refresh_token|bearer|key)", re.I)

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
    parsed = urlparse(text)
    if parsed.scheme in {"http", "https"} and parsed.query:
        query = parse_qs(parsed.query, keep_blank_values=True)
        changed = False
        for query_key in list(query):
            if _SECRET_KEY_RE.search(query_key):
                query[query_key] = ["[redacted]"]
                changed = True
        if changed:
            text = urlunparse(parsed._replace(query=urlencode(query, doseq=True)))
    if "script.google.com" in text:
        return "https://script.google.com/.../exec"
    text = re.sub(r"(?i)\b(secret|token|api_key|apikey|authorization|auth|password|passwd|cookie|webhook|smtp|telegram|provider_key|access_key|refresh_token|bearer|proxy)\s*[:=]\s*[^\s;&<]+", r"\1=[redacted]", text)
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
    return settings.admin_ui_technical_write_key

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
    if not expected:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Technical write key is not configured")
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
    back_target = return_url if return_url else _admin_url('/admin/searches', api_key)
    list_target = _admin_url('/admin/searches', api_key)
    return f"<p><a href='{html.escape(back_target)}'>Back</a></p><p><a href='{html.escape(list_target)}'>Back to search list</a></p>"


def _success_redirect(request: Request, api_key: str | None, marker: str, form: dict[str, str] | None = None) -> RedirectResponse:
    target = _extract_return_url(request, form)
    if target:
        return RedirectResponse(target, status_code=303)
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
    links = [("/admin", "nav.dashboard"), ("/admin/listings", "nav.listings"), ("/admin/searches", "nav.searches"), ("/admin/alerts", "nav.alerts"), ("/admin/listing-analyses", "nav.analyses"), ("/admin/evidence", "nav.evidence"), ("/admin/agents", "nav.agents"), ("/admin/outcome-analytics", "nav.outcome_analytics"), ("/admin/system", "nav.system"), ("/admin/technical", "nav.technical")]
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

def _safe_status_path(path: object) -> str:
    name = Path(str(path or "")).name
    return "[redacted]" if _SECRET_KEY_RE.search(name) else redact_admin_value(name or "—", "path")


def _safe_cell(value: object, key: str = "", limit: int = 180) -> str:
    return html.escape(truncate_admin_text(redact_admin_value(value, key), limit))


def _count_model(db: Session, model: object) -> int:
    try:
        return int(db.scalar(select(func.count()).select_from(model)) or 0)
    except Exception:
        return 0


def _group_counts(db: Session, model: object, column: object, *where) -> dict[str, int]:
    try:
        rows = db.execute(select(column, func.count()).select_from(model).where(*where).group_by(column)).all()
        return {str(k or "—"): int(v or 0) for k, v in rows}
    except Exception:
        return {}


def _render_counts(items: dict[str, int]) -> str:
    return ", ".join(f"{html.escape(str(k))}: {v}" for k, v in sorted(items.items())) or "—"


NON_SUCCESS_ALERT_DELIVERY_STATUSES = ["failed", "skipped", "unknown"]


def _matching_alert_sent_exists_with_created_at(*created_at_where: object) -> object:
    return select(AlertSent.id).where(_attempt_matches_alert_sent_clause(), *created_at_where).limit(1).exists()


def build_alert_delivery_integrity_summary(db: Session, *, since: datetime | None = None) -> dict[str, dict[str, int]]:
    dialect = db.bind.dialect.name if db.bind else ""
    scope = [AlertDeliveryAttempt.created_at >= since] if since is not None else []
    non_success = AlertDeliveryAttempt.status.in_(NON_SUCCESS_ALERT_DELIVERY_STATUSES)
    return {
        "integrity_issues": {
            "success_without_alert_sent": db.scalar(select(func.count()).select_from(AlertDeliveryAttempt).where(*scope, AlertDeliveryAttempt.status == "success", ~_matching_alert_sent_exists())) or 0,
            "success_missing_sent_at": db.scalar(select(func.count()).select_from(AlertDeliveryAttempt).where(*scope, AlertDeliveryAttempt.status == "success", AlertDeliveryAttempt.sent_at.is_(None))) or 0,
            "non_success_with_sent_at": db.scalar(select(func.count()).select_from(AlertDeliveryAttempt).where(*scope, non_success, AlertDeliveryAttempt.sent_at.is_not(None))) or 0,
            "bad_payload_hash_count": db.scalar(select(func.count()).select_from(AlertDeliveryAttempt).where(*scope, _payload_hash_is_bad_clause(dialect))) or 0,
            "non_success_after_alert_sent": db.scalar(select(func.count()).select_from(AlertDeliveryAttempt).where(*scope, non_success, _matching_alert_sent_exists_with_created_at(AlertSent.created_at < AlertDeliveryAttempt.created_at))) or 0,
        },
        "resolved_history": {
            "resolved_non_success_with_later_alert_sent": db.scalar(select(func.count()).select_from(AlertDeliveryAttempt).where(*scope, non_success, _matching_alert_sent_exists_with_created_at(AlertSent.created_at >= AlertDeliveryAttempt.created_at))) or 0,
        },
        "retry_scheduling": {
            "next_retry_at_non_null": db.scalar(select(func.count()).select_from(AlertDeliveryAttempt).where(*scope, AlertDeliveryAttempt.next_retry_at.is_not(None))) or 0,
        },
    }


def _render_delivery_integrity_summary(summary: dict[str, dict[str, int]], *, scope_label: str) -> str:
    sections = [
        ("Delivery integrity issues", "Expected healthy values: 0. These counters are true integrity issues.", summary["integrity_issues"]),
        ("Resolved delivery history", "Informational historical records; non-zero values are not hard violations.", summary["resolved_history"]),
        ("Retry scheduling indicators", "Scheduling indicators only; next_retry_at is not a hard integrity violation.", summary["retry_scheduling"]),
    ]
    html_sections = []
    for title, note, counters in sections:
        items = "".join(f"<li>{html.escape(key)}: {value}</li>" for key, value in counters.items())
        html_sections.append(f"<h3>{title} ({html.escape(scope_label)})</h3><p>{note}</p><ul>{items}</ul>")
    return "".join(html_sections)


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


async def _parse_mutable_form(request: Request) -> tuple[dict[str, str], dict[str, list[str]]]:
    data = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
    return {k: v[-1] if v else "" for k, v in data.items()}, data


def _require_technical_write_form(request: Request, form: dict[str, str], raw_form: dict[str, list[str]] | None = None) -> None:
    if not settings.admin_ui_technical_ops_enabled:
        form.pop("admin_technical_write_key", None)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Technical operations are disabled")
    expected = _configured_technical_key()
    if not expected:
        form.pop("admin_technical_write_key", None)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Technical write key is not configured")
    form_keys = (raw_form or {}).get("admin_technical_write_key")
    if form_keys is not None and len(form_keys) != 1:
        form.pop("admin_technical_write_key", None)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid technical admin key")
    header_key = request.headers.get("X-API-Key")
    form_key = form.pop("admin_technical_write_key", None)
    query_key = request.query_params.get("api_key") if settings.admin_ui_allow_query_api_key else None
    if header_key == expected or form_key == expected or query_key == expected:
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid technical admin key")


def _require_technical_confirmation(form: dict[str, str], expected_action: str) -> None:
    if (form.get("confirm_action") or "").strip() != expected_action:
        raise HTTPException(status_code=400, detail=f"Confirmation required: {expected_action}")


def _technical_auth_fields(expected_action: str, warning: str = "") -> str:
    return (
        "<div class='section'><h3>Dangerous technical actions</h3>"
        "<div class='note'>These actions can change monitoring state, reset baseline, trigger parsing, and affect alert delivery.</div>"
        f"{f'<p class=\"preview\">{html.escape(warning)}</p>' if warning else ''}"
        "<div class='row'><label>Technical write key<input name='admin_technical_write_key' type='password' autocomplete='off' required></label></div>"
        f"<div class='row'><label>Type <code>{html.escape(expected_action)}</code> to confirm<input name='confirm_action' autocomplete='off' required></label></div></div>"
    )


def _safe_external_link(url: str | None, label: str = "open") -> str:
    raw = str(url or "").strip()
    if not raw:
        return "—"
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return html.escape(raw)
    return f"<a href='{_html_attr(raw)}' target='_blank' rel='noopener noreferrer'>{html.escape(label)}</a>"


def _bounded_int(value: object, name: str, default: int, minimum: int, maximum: int) -> int:
    if value in (None, ""):
        return default
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"{name} must be an integer") from exc
    if parsed < minimum or parsed > maximum:
        raise HTTPException(status_code=400, detail=f"{name} must be between {minimum} and {maximum}")
    return parsed


def _positive_path_int(value: int, name: str) -> int:
    if value < 1:
        raise HTTPException(status_code=400, detail=f"{name} must be a positive integer")
    return value


def _json_details(title: str, value: object) -> str:
    return f"<details><summary>{html.escape(title)}</summary><pre>{redact_admin_json(value)}</pre></details>"


def _kv_table(pairs: list[tuple[str, object]]) -> str:
    rows = ''.join(f"<tr><th>{html.escape(k)}</th><td>{html.escape(_truncate(v, 500))}</td></tr>" for k, v in pairs)
    return f"<table>{rows}</table>"


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


@router.get('/evidence', response_class=HTMLResponse, dependencies=[Depends(_require_admin_api_key)])
def admin_evidence(request: Request, db: Session = Depends(get_db), limit: str | None = Query(default=None)):
    api_key = request.query_params.get('api_key')
    effective_limit = _bounded_int(limit, 'limit', 50, 1, 200)
    runs = db.scalars(select(MarketResearchRun).options(raiseload(MarketResearchRun.evidence_items)).order_by(MarketResearchRun.created_at.desc(), MarketResearchRun.id.desc()).limit(effective_limit)).all()
    run_ids = [run.id for run in runs]
    item_counts = dict(db.execute(select(MarketEvidenceItem.run_id, func.count(MarketEvidenceItem.id)).where(MarketEvidenceItem.run_id.in_(run_ids)).group_by(MarketEvidenceItem.run_id)).all()) if run_ids else {}
    items = db.scalars(select(MarketEvidenceItem).order_by(MarketEvidenceItem.created_at.desc(), MarketEvidenceItem.id.desc()).limit(effective_limit)).all()
    run_rows = []
    for run in runs:
        count = item_counts.get(run.id, 0)
        run_rows.append(f"<tr><td>{run.id}<br><a href='{_html_attr(_admin_url(f'/admin/evidence/runs/{run.id}', api_key))}'>детали</a></td><td>{html.escape(run.listing_external_id or '—')}</td><td>{html.escape(run.research_profile or '—')}</td><td>{html.escape(run.provider or '—')}</td><td>{html.escape(run.status or '—')}</td><td>{display_datetime(run.created_at)}</td><td>{display_datetime(run.checked_at)}</td><td>{count}</td><td>{_json_details('Показать технические данные', {'query_plan_json': run.query_plan_json, 'sources_json': run.sources_json, 'limitations_json': run.limitations_json, 'summary': run.summary})}</td></tr>")
    item_rows = []
    for item in items:
        item_rows.append(f"<tr><td>{item.id}</td><td>{item.run_id}</td><td>{html.escape(item.listing_external_id or '—')}</td><td>{html.escape(item.evidence_type or '—')}</td><td>{html.escape(_truncate(item.source_publisher or item.source_title or '—', 80))}</td><td>{html.escape(_truncate(item.title or item.claim or item.description or '—', 160))}</td><td>{html.escape(str(item.price_rub or '—'))}</td><td>{html.escape(str(item.area_m2 or '—'))}</td><td>{html.escape(str(item.rent_rub_per_month or '—'))}</td><td>{_safe_external_link(item.source_url, 'open')}</td><td>{display_datetime(item.created_at)}</td><td>{display_boolean(item.is_reusable)}</td><td>{_json_details('Показать технические данные', item.evidence_json)}</td></tr>")
    form = f"<form method='get' action='{_html_attr(_admin_url('/admin/evidence', api_key))}'><input type='hidden' name='api_key' value='{_html_attr(api_key)}'><label>limit<input name='limit' type='number' min='1' max='200' value='{effective_limit}'></label><button>Apply</button></form>"
    body = f"<h1>Рыночные данные</h1><p class='preview'>Read-only: no research runs, refreshes, edits, or deletes are available.</p>{form}<section class='section'><h2>Исследования рынка</h2><table><tr><th>id</th><th>listing_external_id</th><th>profile</th><th>provider</th><th>status</th><th>created_at</th><th>checked_at</th><th>items</th><th>details</th></tr>{''.join(run_rows)}</table></section><section class='section'><h2>Рыночные ориентиры / аналоги</h2><table><tr><th>id</th><th>run_id</th><th>listing_external_id</th><th>type</th><th>source</th><th>title / text</th><th>price</th><th>area</th><th>rent</th><th>url</th><th>created_at</th><th>usable</th><th>details</th></tr>{''.join(item_rows)}</table></section>"
    return _render_page('Рыночные данные', body)


@router.get('/evidence/runs/{run_id}', response_class=HTMLResponse, dependencies=[Depends(_require_admin_api_key)])
def admin_evidence_run(request: Request, run_id: int, db: Session = Depends(get_db), limit: str | None = Query(default=None)):
    api_key = request.query_params.get('api_key')
    run_id = _positive_path_int(run_id, 'run_id')
    effective_limit = _bounded_int(limit, 'limit', 50, 1, 200)
    run = db.get(MarketResearchRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail='Market research run not found')
    items = db.scalars(select(MarketEvidenceItem).where(MarketEvidenceItem.run_id == run_id).order_by(MarketEvidenceItem.created_at.desc(), MarketEvidenceItem.id.desc()).limit(effective_limit)).all()
    item_rows = ''.join(f"<tr><td>{i.id}</td><td>{html.escape(i.evidence_type or '—')}</td><td>{html.escape(_truncate(i.title or i.claim or i.description or '—', 180))}</td><td>{_safe_external_link(i.source_url, 'open')}</td><td>{html.escape(str(i.confidence or '—'))}</td><td>{display_boolean(i.is_reusable)}</td><td>{_json_details('Показать технические данные', i.evidence_json)}</td></tr>" for i in items)
    meta = _kv_table([('id', run.id), ('agent_task_id', run.agent_task_id), ('listing_external_id', run.listing_external_id), ('listing_analysis_id', run.listing_analysis_id), ('research_profile', run.research_profile), ('status', run.status), ('provider', run.provider), ('model', run.model), ('schema_version', run.schema_version), ('prompt_version', run.prompt_version), ('confidence', run.confidence), ('checked_at', run.checked_at), ('expires_at', run.expires_at), ('created_at', run.created_at), ('updated_at', run.updated_at)])
    details = _json_details('Показать технические данные', {'summary': run.summary, 'query_plan_json': run.query_plan_json, 'sources_json': run.sources_json, 'limitations_json': run.limitations_json, 'input_hash': run.input_hash, 'output_hash': run.output_hash})
    body = f"<h1>Исследование рынка #{run.id}</h1><p><a href='{_html_attr(_admin_url('/admin/evidence', api_key))}'>Назад к рыночным данным</a></p><p class='preview'>Read-only detail page. No run, refresh, research, edit, or delete action is available.</p><section class='section'><h2>Метаданные</h2>{meta}{details}</section><section class='section'><h2>Рыночные ориентиры / аналоги</h2><table><tr><th>id</th><th>type</th><th>title / text</th><th>url</th><th>confidence</th><th>usable</th><th>details</th></tr>{item_rows}</table></section>"
    return _render_page('Исследование рынка', body)


@router.get('/agents', response_class=HTMLResponse, dependencies=[Depends(_require_admin_api_key)])
def admin_agents(request: Request, db: Session = Depends(get_db), limit: str | None = Query(default=None)):
    api_key = request.query_params.get('api_key')
    effective_limit = _bounded_int(limit, 'limit', 50, 1, 200)
    tasks = db.scalars(select(AgentTask).order_by(AgentTask.created_at.desc(), AgentTask.id.desc()).limit(effective_limit)).all()
    rows = ''.join(f"<tr><td>{t.id}<br><a href='{_html_attr(_admin_url(f'/admin/agents/{t.id}', api_key))}'>детали</a></td><td>{html.escape(t.task_type or '—')}</td><td>{html.escape(t.status or '—')}</td><td>{html.escape(t.error_type or '—')}</td><td>{display_datetime(t.created_at)}</td><td>{display_datetime(t.started_at)}</td><td>{display_datetime(t.finished_at)}</td><td>{html.escape(t.listing_external_id or '—')}</td><td>{html.escape(str(t.search_job_id or '—'))}</td><td>{_json_details('Показать технические данные', {'payload_json': t.payload_json, 'result_json': t.result_json, 'error_message': t.error_message})}</td></tr>" for t in tasks)
    form = f"<form method='get' action='{_html_attr(_admin_url('/admin/agents', api_key))}'><input type='hidden' name='api_key' value='{_html_attr(api_key)}'><label>limit<input name='limit' type='number' min='1' max='200' value='{effective_limit}'></label><button>Apply</button></form>"
    body = f"<h1>Задачи агентов</h1><p class='preview'>Read-only: no run, retry, cancel, approve, or rerun actions are available.</p>{form}<table><tr><th>id</th><th>task_type</th><th>status</th><th>error_type</th><th>created_at</th><th>started_at</th><th>completed_at</th><th>listing_external_id</th><th>search_id</th><th>details</th></tr>{rows}</table>"
    return _render_page('Задачи агентов', body)


@router.get('/agents/{task_id}', response_class=HTMLResponse, dependencies=[Depends(_require_admin_api_key)])
def admin_agent_detail(task_id: int, db: Session = Depends(get_db)):
    task_id = _positive_path_int(task_id, 'task_id')
    task = db.get(AgentTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail='Agent task not found')
    meta = _kv_table([('id', task.id), ('task_type', task.task_type), ('status', task.status), ('priority', task.priority), ('listing_external_id', task.listing_external_id), ('listing_analysis_id', task.listing_analysis_id), ('search_job_id', task.search_job_id), ('context_key', task.context_key), ('dedupe_key', task.dedupe_key), ('error_type', task.error_type), ('error_message', redact_admin_value(task.error_message, 'error_message')), ('created_at', task.created_at), ('updated_at', task.updated_at), ('started_at', task.started_at), ('completed_at', task.finished_at)])
    body = f"<h1>Задача агента #{task.id}</h1><p class='preview'>Read-only detail page. No run, retry, cancel, approve, or rerun action is available.</p><section class='section'><h2>Метаданные</h2>{meta}</section><section class='section'>{_json_details('input payload', task.payload_json)}{_json_details('result payload', task.result_json)}</section>"
    return _render_page('Задача агента', body)


def _dict_table(data: dict) -> str:
    if not data:
        return '<p>Нет данных</p>'
    return '<table>' + ''.join(f"<tr><th>{html.escape(str(k))}</th><td>{html.escape(str(v))}</td></tr>" for k, v in data.items()) + '</table>'


@router.get('/outcome-analytics', response_class=HTMLResponse, dependencies=[Depends(_require_admin_api_key)])
def admin_outcome_analytics(
    request: Request,
    db: Session = Depends(get_db),
    period_days: str | None = Query(default=None),
    max_examples: str | None = Query(default=None),
    search_job_id: str | None = Query(default=None),
    as_of: str | None = Query(default=None),
):
    api_key = request.query_params.get('api_key')
    period = _bounded_int(period_days, 'period_days', 30, 1, 365)
    examples_limit = _bounded_int(max_examples, 'max_examples', 10, 0, 50)
    search_ids = None
    if search_job_id not in (None, ''):
        search_ids = [_bounded_int(search_job_id, 'search_job_id', 1, 1, 2_147_483_647)]
    as_of_dt = None
    if as_of:
        try:
            as_of_dt = datetime.fromisoformat(as_of.replace('Z', '+00:00'))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail='as_of must be an ISO datetime') from exc
        if as_of_dt.tzinfo is None:
            raise HTTPException(status_code=400, detail='as_of must include timezone')
    req = OutcomeAnalyticsRequest(period_days=period, as_of=as_of_dt, search_job_ids=search_ids, max_examples_per_section=examples_limit)
    report = HumanOutcomeAnalyticsService(db).build_report(req)
    form = f"<form method='get' action='{_html_attr(_admin_url('/admin/outcome-analytics', api_key))}'><input type='hidden' name='api_key' value='{_html_attr(api_key)}'><label>period_days<input name='period_days' type='number' min='1' max='365' value='{period}'></label><label>max_examples<input name='max_examples' type='number' min='0' max='50' value='{examples_limit}'></label><label>search_job_id<input name='search_job_id' type='number' value='{_html_attr(search_job_id or '')}'></label><label>as_of<input name='as_of' value='{_html_attr(as_of or '')}'></label><button>Apply</button></form>"
    examples = report.examples.model_dump(mode='json') if hasattr(report.examples, 'model_dump') else {}
    body = f"<h1>Аналитика решений</h1><p class='preview'>Read-only report from existing PR18b outcome analytics service. This page does not persist analytics or update reviews/scoring.</p>{form}<section class='section'><h2>Период</h2>{_kv_table(list(report.period.model_dump().items()))}</section><section class='section'><h2>Totals</h2>{_kv_table(list(report.totals.model_dump().items()))}</section><section class='section'><h2>Human verdict counts</h2>{_dict_table(report.human_verdict_counts)}</section><section class='section'><h2>Outcome status counts</h2>{_dict_table(report.outcome_status_counts)}</section><section class='section'><h2>Investment decision counts</h2>{_json_details('Показать технические данные', report.decision_counts.model_dump())}</section><section class='section'><h2>Score bucket stats</h2>{_json_details('Показать технические данные', {k: v.model_dump() for k, v in report.score_bucket_stats.items()})}</section><section class='section'><h2>Analysis alignment</h2>{_json_details('Показать технические данные', report.analysis_alignment)}</section><section class='section'><h2>Risk flag stats</h2>{_json_details('Показать технические данные', {k: v.model_dump() for k, v in report.risk_flag_stats.items()})}</section><section class='section'><h2>Search stats</h2>{_json_details('Показать технические данные', [s.model_dump() for s in report.search_stats])}</section><section class='section'><h2>Examples</h2>{_json_details('Показать технические данные', examples)}</section><section class='section'><h2>Limitations</h2><ul>{''.join(f'<li>{html.escape(str(x))}</li>' for x in report.limitations)}</ul></section><section class='section'><h2>Hashes</h2>{_kv_table([('request_hash', report.request_hash), ('stats_snapshot_hash', report.stats_snapshot_hash)])}</section>"
    return _render_page('Аналитика решений', body)


@router.get("/technical", response_class=HTMLResponse, dependencies=[Depends(_require_admin_api_key)])
def technical():
    ops = display_boolean(settings.admin_ui_technical_ops_enabled)
    body = (
        "<h1>Технический режим</h1>"
        "<div class='note'>Эти действия могут изменить поведение мониторинга. Используйте только если понимаете эффект.</div>"
        f"<p><strong>Technical operations:</strong> {ops}<br>"
        f"<strong>Admin mode:</strong> {html.escape(settings.admin_ui_mode)}<br>"
        f"<strong>Language:</strong> {html.escape(_lang())}</p>"
        + ("<p>Technical operations are disabled. Set <code>ADMIN_UI_TECHNICAL_OPS_ENABLED=true</code> and configure <code>ADMIN_UI_TECHNICAL_WRITE_KEY</code> to enable dangerous actions.</p>" if not settings.admin_ui_technical_ops_enabled else "<p>Технические действия включены.</p>")
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
            toggle_confirm = 'deactivate_search' if s.is_active else 'activate_search'
            action_html = (
                "<details><summary>Dangerous technical actions</summary><div class='note'>These actions can change monitoring state, reset baseline, trigger parsing, and affect alert delivery.</div>"
                f"<code>python3 -m app.cli run-once --search-id {s.id}</code><br><a href='{_admin_url(f'/admin/searches/{s.id}/edit', api_key)}'>edit search form</a> {open_avito}"
                f"<form method='post' action='{_admin_url(f'/admin/searches/{s.id}/' + toggle_action, api_key)}'>{_technical_auth_fields(toggle_confirm)}<button>{toggle_label}</button></form>"
                f"<form method='post' action='{_admin_url(f'/admin/searches/{s.id}/reset-baseline', api_key)}'>{_technical_auth_fields('reset_baseline', 'Reset baseline can cause the next cycle to behave like a first baseline run and must be used carefully.')}<button>reset baseline</button></form>"
                f"<form method='post' action='{_admin_url(f'/admin/searches/{s.id}/run-once', api_key)}'>{_technical_auth_fields('run_once', 'Run once may parse Avito and may send alerts depending on existing monitor/delivery rules.')}<button>run once</button></form></details>"
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


ALERT_ATTEMPT_STATUSES = {"success", "failed", "skipped", "unknown"}
PAYLOAD_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def _parse_int_param(name: str, value: str | None, default: int, minimum: int, maximum: int) -> int:
    raw = (value or "").strip()
    if not raw:
        return default
    try:
        parsed = int(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {name}") from exc
    if parsed < minimum or parsed > maximum:
        raise HTTPException(status_code=400, detail=f"Invalid {name}")
    return parsed


def _parse_optional_positive_int(name: str, value: str | None) -> int | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        parsed = int(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {name}") from exc
    if parsed <= 0:
        raise HTTPException(status_code=400, detail=f"Invalid {name}")
    return parsed


def _safe_short_filter(name: str, value: str | None, max_len: int) -> str | None:
    normalized = (value or "").strip() or None
    if normalized and len(normalized) > max_len:
        raise HTTPException(status_code=400, detail=f"Invalid {name}")
    return normalized


def _redact_alert_error(value: object, limit: int = 180) -> str:
    return truncate_admin_text(sanitize_alert_delivery_error(str(value or "")), limit)


def _admin_query_api_key_input(api_key: str | None) -> str:
    if not api_key or not settings.admin_ui_allow_query_api_key:
        return ""
    return f"<input type='hidden' name='api_key' value='{_html_attr(api_key)}'>"


MANUAL_RETRY_STATUSES = {"failed", "skipped", "unknown"}


def _delivery_dedupe_key(channel: str, listing_external_id: str) -> str:
    return f"{channel}:new:{listing_external_id}"


def _matching_alert_sent(db: Session, attempt: AlertDeliveryAttempt) -> AlertSent | None:
    return db.scalar(
        select(AlertSent)
        .where(
            AlertSent.dedupe_key == attempt.dedupe_key,
            AlertSent.listing_external_id == attempt.listing_external_id,
            AlertSent.channel == attempt.channel,
        )
        .limit(1)
    )


def _manual_retry_eligibility(
    db: Session, attempt: AlertDeliveryAttempt
) -> tuple[bool, str, Listing | None]:
    channel = (attempt.channel or "").strip()
    dedupe_key = (attempt.dedupe_key or "").strip()
    listing = db.scalar(select(Listing).where(Listing.external_id == attempt.listing_external_id).limit(1))
    if attempt.status not in MANUAL_RETRY_STATUSES:
        return False, "Only failed, skipped, or unknown attempts can be retried.", listing
    if not channel:
        return False, "This attempt is not eligible for manual retry because channel is empty.", listing
    if not dedupe_key:
        return False, "This attempt is not eligible for manual retry because dedupe key is empty.", listing
    if listing is None:
        return False, "This attempt is not eligible for manual retry because the listing is missing.", None
    expected = _delivery_dedupe_key(channel, attempt.listing_external_id)
    if dedupe_key != expected:
        return False, "This attempt is not eligible for manual retry because dedupe key does not match the expected channel/listing key.", listing
    if _matching_alert_sent(db, attempt) is not None:
        return False, "This attempt is not eligible for manual retry because a matching AlertSent already exists.", listing
    return True, "Eligible for manual retry.", listing


def _listing_card_from_listing(listing: Listing) -> ListingCard:
    return ListingCard(
        external_id=listing.external_id,
        url=listing.url,
        title=listing.title or "",
        price=listing.price,
        address=listing.address or "",
        area_m2=listing.area_m2,
        rooms=listing.rooms or "",
        published_label=listing.published_label or "",
        published_at=listing.published_at,
        raw={},
    )


async def _retry_single_delivery_channel(
    db: Session,
    *,
    attempt: AlertDeliveryAttempt,
    listing: Listing,
) -> str:
    channel_name = (attempt.channel or "").strip()
    dedupe_key = _delivery_dedupe_key(channel_name, attempt.listing_external_id)
    service = MonitorService()
    card = _listing_card_from_listing(listing)
    search_name = f"manual_retry:{attempt.search_name}" if attempt.search_name else "manual_retry"
    payload = service._build_alert_payload(
        card=card,
        search_name=search_name,
        summary="",
        score=None,
        tags=[],
        analysis=None,
    )
    message = build_listing_message(
        {
            "title": card.title,
            "price": card.price,
            "address": card.address,
            "area_m2": card.area_m2,
            "rooms": card.rooms,
            "published_label": card.published_label,
            "url": card.url,
        },
        "",
    )
    payload_hash = compute_alert_payload_hash(payload)
    attempt_repo = AlertDeliveryAttemptRepository(db)
    attempt_count = attempt_repo.next_attempt_count(dedupe_key=dedupe_key, channel=channel_name)

    channels = getattr(service.notifier, "channels", [service.notifier])
    channel_map = {
        getattr(channel, "channel_name", ""): channel
        for channel in channels
        if getattr(channel, "channel_name", "")
    }
    channel = channel_map.get(channel_name)

    def record(status_value: str, error: BaseException | str | None = None) -> None:
        attempt_repo.create_attempt(
            listing_external_id=attempt.listing_external_id,
            channel=channel_name,
            dedupe_key=dedupe_key,
            payload_hash=payload_hash,
            status=status_value,
            attempt_count=attempt_count,
            last_error=sanitize_alert_delivery_error(error) if error is not None else None,
            next_retry_at=None,
            sent_at=service._now() if status_value == "success" else None,
            search_job_id=attempt.search_job_id,
            search_name=search_name,
            error_type=error.__class__.__name__ if isinstance(error, BaseException) else ("channel_not_configured" if status_value == "unknown" and error else None),
        )

    if channel is None:
        record("unknown", "channel not configured for manual retry")
        db.commit()
        return "unknown"

    if _matching_alert_sent(db, attempt) is not None:
        raise HTTPException(status_code=400, detail="Matching AlertSent already exists")

    try:
        delivered = await channel.send_listing_alert(message, payload)
    except Exception as exc:
        record("failed", exc)
        db.commit()
        return "failed"

    if delivered is True:
        record("success")
        AlertRepository(db).create(
            listing_external_id=attempt.listing_external_id,
            dedupe_key=dedupe_key,
            channel=channel_name,
        )
        db.commit()
        return "success"
    if delivered is False:
        record("skipped")
        db.commit()
        return "skipped"
    record("unknown", f"unexpected result type: {type(delivered).__name__}")
    db.commit()
    return "unknown"


def _attempt_matches_alert_sent_clause() -> object:
    return (
        (AlertSent.dedupe_key == AlertDeliveryAttempt.dedupe_key)
        & (AlertSent.listing_external_id == AlertDeliveryAttempt.listing_external_id)
        & (AlertSent.channel == AlertDeliveryAttempt.channel)
    )


def _matching_alert_sent_exists() -> object:
    return select(AlertSent.id).where(_attempt_matches_alert_sent_clause()).limit(1).exists()


def _payload_hash_is_bad_clause(dialect_name: str = "") -> object:
    if dialect_name == "sqlite":
        valid_hash = AlertDeliveryAttempt.payload_hash.op("GLOB")("[0-9a-f]" * 64)
    else:
        valid_hash = AlertDeliveryAttempt.payload_hash.op("~")("^[0-9a-f]{64}$")
    return or_(
        AlertDeliveryAttempt.payload_hash.is_(None),
        AlertDeliveryAttempt.payload_hash == "",
        func.length(AlertDeliveryAttempt.payload_hash) != 64,
        ~valid_hash,
    )


def _unknown_metric(value: object) -> str:
    return "unknown" if value is None else html.escape(str(value))


def _render_monitor_cycle_history(db: Session, now: datetime) -> str:
    since_24h = now - timedelta(hours=24)
    stale_after_seconds = max(settings.monitor_worker_stale_after_seconds, 30 * 60)
    stale_cutoff = now - timedelta(seconds=stale_after_seconds)
    status_counts = dict(
        db.execute(
            select(MonitorCycleRun.status, func.count())
            .where(MonitorCycleRun.started_at >= since_24h)
            .group_by(MonitorCycleRun.status)
        ).all()
    )
    total_24h = sum(status_counts.values())
    latest_success = db.scalar(
        select(func.max(MonitorCycleRun.started_at)).where(MonitorCycleRun.status == "success")
    )
    latest_failed = db.scalar(
        select(func.max(MonitorCycleRun.started_at)).where(MonitorCycleRun.status == "failed")
    )
    stale_running = db.scalar(
        select(func.count())
        .select_from(MonitorCycleRun)
        .where(
            MonitorCycleRun.status == "running",
            MonitorCycleRun.finished_at.is_(None),
            MonitorCycleRun.started_at < stale_cutoff,
        )
    ) or 0
    rows = db.scalars(
        select(MonitorCycleRun)
        .order_by(MonitorCycleRun.started_at.desc(), MonitorCycleRun.id.desc())
        .limit(20)
    ).all()
    summary = (
        f"<p>last 24h cycles total: {total_24h}; success: {status_counts.get('success', 0)}; "
        f"partial: {status_counts.get('partial', 0)}; failed: {status_counts.get('failed', 0)}; "
        f"skipped: {status_counts.get('skipped', 0)}; latest successful cycle: {display_datetime(latest_success)}; "
        f"latest failed cycle: {display_datetime(latest_failed)}; stale running count: {stale_running}</p>"
    )
    table_rows = []
    for row in rows:
        stale_note = ""
        if row.status == "running" and row.finished_at is None and row.started_at < stale_cutoff:
            stale_note = " <strong>stale running; possible crash</strong>"
        error_preview = html.escape(redact_admin_value(row.last_error or "—", "last_error")[:220])
        table_rows.append(
            "<tr>"
            f"<td>{row.id}</td><td>{display_datetime(row.started_at)}</td><td>{display_datetime(row.finished_at)}</td>"
            f"<td>{_unknown_metric(row.duration_ms)}</td><td>{html.escape(row.status)}{stale_note}</td>"
            f"<td>{_unknown_metric(row.searches_processed)} / {_unknown_metric(row.searches_total)}</td>"
            f"<td>{_unknown_metric(row.searches_failed)}</td>"
            f"<td>{_unknown_metric(row.listings_created)} / {_unknown_metric(row.listings_updated)}</td>"
            f"<td>{_unknown_metric(row.alert_delivery_attempts_created)}</td>"
            f"<td>{_unknown_metric(row.alerts_sent_created)}</td>"
            f"<td>{_unknown_metric(row.alert_delivery_failed)}</td>"
            f"<td>{_unknown_metric(row.alert_delivery_unknown)}</td>"
            f"<td>{_safe_cell(row.error_type or '—', 'error_type')}</td>"
            f"<td>{error_preview}</td>"
            f"<td><code>{html.escape(Path(str(row.worker_status_file)).name if row.worker_status_file else '—')}</code></td>"
            "</tr>"
        )
    body = "".join(table_rows) or "<tr><td colspan='15'>No monitor cycle runs recorded yet.</td></tr>"
    return (
        "<section class='section'><h2>История циклов мониторинга / Monitor cycle history</h2>"
        f"{summary}<table><tr><th>id</th><th>started_at</th><th>finished_at</th><th>duration_ms</th>"
        "<th>status</th><th>searches processed/total</th><th>searches_failed</th>"
        "<th>listings created/updated</th><th>alert attempts created</th><th>alerts sent created</th>"
        "<th>alert failed</th><th>alert unknown</th><th>error_type</th><th>last_error</th><th>worker_status_file</th></tr>"
        f"{body}</table></section>"
    )


def _render_backup_restore_retention_readiness() -> str:
    rows = [
        ("Backup policy", "docs/ops/backup_restore_retention_policy.md"),
        ("Restore procedure", "documented"),
        ("Retention mode", "policy-only"),
        ("Retention execution", "disabled / not implemented"),
        ("Retention dry-run", "not implemented"),
        ("Latest backup", "unknown"),
        ("Backup metadata source", "not configured"),
    ]
    body = "".join(
        f"<tr><th>{html.escape(label)}</th><td>{html.escape(value)}</td></tr>"
        for label, value in rows
    )
    return (
        "<section class='section'><h2>Готовность backup / restore / retention</h2>"
        "<p>Read-only policy/readiness signals only; no operator actions are available here.</p>"
        f"<table>{body}</table></section>"
    )

@router.get('/system', response_class=HTMLResponse, dependencies=[Depends(_require_admin_api_key)])
def admin_system(request: Request, db: Session = Depends(get_db)):
    api_key = request.query_params.get("api_key")
    raw_status = read_worker_status(settings.monitor_worker_status_path)
    summary = summarize_worker_status(raw_status, stale_after_seconds=settings.monitor_worker_stale_after_seconds)
    payload = summary.get("payload") or {}
    badge = summary.get("badge") or {"label": "Unknown", "color": "gray"}
    cycle_ok = summary.get("cycle_ok")
    cycle_badge = _badge("Cycle OK", "green") if cycle_ok is True else (_badge("Last cycle failed", "red") if cycle_ok is False else _badge("Cycle unknown", "gray"))
    worker_rows = [
        ("status", _badge(str(badge.get("label", "Unknown")), str(badge.get("color", "gray")))),
        ("status file state", html.escape(str(summary.get("state") or "missing"))),
        ("status file basename", f"<code>{_safe_cell(_safe_status_path(summary.get('path')), 'path')}</code>"),
        ("updated_at", _safe_cell(summary.get("updated_at") or "—")),
        ("age_seconds", _safe_cell(summary.get("age_seconds") if summary.get("age_seconds") is not None else "—")),
        ("stale_after_seconds", _safe_cell(summary.get("stale_after_seconds"))),
        ("cycle_ok", cycle_badge),
        ("cycle_error_type", _safe_cell(payload.get("cycle_error_type") or "—", "cycle_error_type")),
        ("cycle_error", _safe_cell(payload.get("cycle_error") or summary.get("error") or "—", "cycle_error", 220)),
        ("searches_processed", _safe_cell(payload.get("searches_processed", "—"))),
        ("result_count", _safe_cell(payload.get("result_count", "—"))),
    ]
    worker_table = "<table>" + "".join(f"<tr><th>{html.escape(k)}</th><td>{v}</td></tr>" for k, v in worker_rows) + "</table>"
    parser_rows = "".join(
        f"<tr><th>{html.escape(field)}</th><td>{_safe_cell(payload.get(field), field)}</td></tr>"
        for field in PARSER_STATUS_FIELDS
        if field in payload
    ) or "<tr><td colspan='2'>unknown</td></tr>"

    search_total = _count_model(db, SearchJob)
    active = db.scalar(select(func.count()).select_from(SearchJob).where(SearchJob.is_active.is_(True))) or 0
    search_errors = db.scalar(select(func.count()).select_from(SearchJob).where(SearchJob.last_error.is_not(None), SearchJob.last_error != "")) or 0
    oldest_last = db.scalar(select(func.min(SearchJob.last_checked_at)))
    latest_last = db.scalar(select(func.max(SearchJob.last_checked_at)))

    now = datetime.utcnow()
    since_24h = now - timedelta(hours=24)
    since_7d = now - timedelta(days=7)
    status_24h = _group_counts(db, AlertDeliveryAttempt, AlertDeliveryAttempt.status, AlertDeliveryAttempt.created_at >= since_24h)
    status_7d = _group_counts(db, AlertDeliveryAttempt, AlertDeliveryAttempt.status, AlertDeliveryAttempt.created_at >= since_7d)
    channel_24h = _group_counts(db, AlertDeliveryAttempt, AlertDeliveryAttempt.channel, AlertDeliveryAttempt.created_at >= since_24h)
    channel_7d = _group_counts(db, AlertDeliveryAttempt, AlertDeliveryAttempt.channel, AlertDeliveryAttempt.created_at >= since_7d)
    delivery_integrity_summary = build_alert_delivery_integrity_summary(db)
    recent_delivery = db.scalars(
        select(AlertDeliveryAttempt)
        .where(AlertDeliveryAttempt.status.in_(["failed", "unknown"]))
        .order_by(AlertDeliveryAttempt.created_at.desc(), AlertDeliveryAttempt.id.desc())
        .limit(20)
    ).all()
    delivery_rows = "".join(
        f"<tr><td>{a.id}</td><td>{display_datetime(a.created_at)}</td><td>{_safe_cell(a.channel, 'channel')}</td><td>{_safe_cell(a.listing_external_id, 'listing_external_id')}</td><td>{_safe_cell(a.status, 'status')}</td><td>{_safe_cell(a.error_type or '—', 'error_type')}</td><td>{html.escape(_redact_alert_error(a.last_error, 220))}</td><td><a href='{_html_attr(_admin_url(f'/admin/alerts/delivery-attempts/{a.id}', api_key))}'>details</a></td></tr>"
        for a in recent_delivery
    ) or "<tr><td colspan='8'>No recent failed or unknown attempts.</td></tr>"
    manual_retry_count = db.scalar(select(func.count()).select_from(AlertDeliveryAttempt).where(AlertDeliveryAttempt.search_name.like("manual_retry%"))) or 0

    failed_tasks = db.scalars(select(AgentTask).where(AgentTask.status == "failed").order_by(AgentTask.updated_at.desc(), AgentTask.id.desc()).limit(20)).all()
    task_rows = "".join(
        f"<tr><td>{t.id}</td><td>{_safe_cell(t.task_type, 'task_type')}</td><td>{_safe_cell(t.status, 'status')}</td><td>{_safe_cell(t.error_type or '—', 'error_type')}</td><td>{display_datetime(t.created_at)}</td><td>{display_datetime(t.updated_at)}</td><td>{display_datetime(t.started_at)}</td><td>{display_datetime(t.finished_at)}</td></tr>"
        for t in failed_tasks
    ) or "<tr><td colspan='8'>No recent failed agent tasks.</td></tr>"
    stuck_cutoff = now - timedelta(hours=2)
    stuck_tasks = db.scalar(select(func.count()).select_from(AgentTask).where(AgentTask.status == "running", or_(AgentTask.updated_at < stuck_cutoff, AgentTask.started_at < stuck_cutoff))) or 0

    failed_analyses = db.scalars(select(ListingAnalysis).where(ListingAnalysis.status == "failed").order_by(ListingAnalysis.updated_at.desc(), ListingAnalysis.id.desc()).limit(20)).all()
    analysis_rows = "".join(
        f"<tr><td>{a.id}</td><td>{_safe_cell(a.listing_external_id, 'listing_external_id')}</td><td>{_safe_cell(a.profile, 'profile')}</td><td>{_safe_cell(a.status, 'status')}</td><td>{_safe_cell(a.error_type or '—', 'error_type')}</td><td>{_safe_cell(a.error_message or '—', 'error_message', 220)}</td><td>{display_datetime(a.created_at)}</td><td>{display_datetime(a.updated_at)}</td></tr>"
        for a in failed_analyses
    ) or "<tr><td colspan='8'>No recent failed analyses.</td></tr>"

    volumes = {
        "listings": _count_model(db, Listing),
        "listing_analyses": _count_model(db, ListingAnalysis),
        "alert_delivery_attempts": _count_model(db, AlertDeliveryAttempt),
        "alerts_sent": _count_model(db, AlertSent),
        "agent_tasks": _count_model(db, AgentTask),
        "human_reviews": _count_model(db, HumanReview),
        "human_review_actions": _count_model(db, HumanReviewAction),
        "investment_decisions": _count_model(db, InvestmentDecision),
        "market_research_runs": _count_model(db, MarketResearchRun),
        "market_evidence_items": _count_model(db, MarketEvidenceItem),
        "listing_detail_snapshots": _count_model(db, ListingDetailSnapshot),
        "listing_enrichments": _count_model(db, ListingEnrichment),
        "knowledge_notes": _count_model(db, KnowledgeNote),
        "search_jobs": _count_model(db, SearchJob),
    }
    try:
        alembic_rows = db.execute(select(text("version_num")).select_from(text("alembic_version"))).all()
        alembic_revision = ", ".join(str(r[0]) for r in alembic_rows) or "Not checked in web request; verify with alembic current during deploy."
    except Exception:
        alembic_revision = "Not checked in web request; verify with alembic current during deploy."

    delivery_integrity_html = _render_delivery_integrity_summary(delivery_integrity_summary, scope_label="all time")
    monitor_cycle_history_html = _render_monitor_cycle_history(db, now)
    volume_items = "".join(f"<li>{html.escape(k)}: {v}</li>" for k, v in volumes.items())
    body = (
        "<h1>Состояние / System health</h1><p class='preview'>Read-only dashboard from existing worker status and bounded SQL counters. No forms, no POST actions, no external checks.</p>"
        f"<section class='section'><h2>Overall status</h2>{worker_table}</section>"
        f"<section class='section'><h2>Worker cycle status</h2>{worker_table}<details><summary>Redacted technical details</summary><pre>{redact_admin_json({'status_file_basename': _safe_status_path(summary.get('path')), 'state': summary.get('state')})}</pre></details></section>"
        f"<section class='section'><h2>Parser diagnostics</h2><table>{parser_rows}</table></section>"
        f"<section class='section'><h2>Search jobs</h2><p>total: {search_total}; active: {active}; inactive: {search_total - active}; with last_error: {search_errors}; oldest last_checked_at: {display_datetime(oldest_last)}; latest last_checked_at: {display_datetime(latest_last)}</p></section>"
        f"<section class='section'><h2>Alert Delivery health</h2><p>delivery attempts total: {_count_model(db, AlertDeliveryAttempt)}; last 24h: {sum(status_24h.values())}; last 7d: {sum(status_7d.values())}; failed 24h/7d: {status_24h.get('failed', 0)}/{status_7d.get('failed', 0)}; unknown 24h/7d: {status_24h.get('unknown', 0)}/{status_7d.get('unknown', 0)}; manual_retry attempts: {manual_retry_count}; alerts_sent total: {_count_model(db, AlertSent)}</p><p>status counts 24h: {_render_counts(status_24h)}<br>status counts 7d: {_render_counts(status_7d)}<br>channel counts 24h: {_render_counts(channel_24h)}<br>channel counts 7d: {_render_counts(channel_7d)}<br>alerts_sent by channel: {_render_counts(_group_counts(db, AlertSent, AlertSent.channel))}</p>{delivery_integrity_html}</section>"
        f"{monitor_cycle_history_html}"
        f"<section class='section'><h2>Recent failed delivery attempts</h2><table><tr><th>id</th><th>created_at</th><th>channel</th><th>listing_external_id</th><th>status</th><th>error_type</th><th>last_error</th><th>detail</th></tr>{delivery_rows}</table></section>"
        f"<section class='section'><h2>Agent tasks</h2><p>total: {_count_model(db, AgentTask)}; by status: {_render_counts(_group_counts(db, AgentTask, AgentTask.status))}; by task_type/status: {_render_counts({f'{r[0]}/{r[1]}': r[2] for r in db.execute(select(AgentTask.task_type, AgentTask.status, func.count()).group_by(AgentTask.task_type, AgentTask.status)).all()})}; stuck running older than 2h: {stuck_tasks}</p><table><tr><th>id</th><th>task_type</th><th>status</th><th>error_type</th><th>created_at</th><th>updated_at</th><th>started_at</th><th>finished_at</th></tr>{task_rows}</table></section>"
        f"<section class='section'><h2>Analysis summary</h2><p>total: {_count_model(db, ListingAnalysis)}; by status: {_render_counts(_group_counts(db, ListingAnalysis, ListingAnalysis.status))}; by profile/status: {_render_counts({f'{r[0]}/{r[1]}': r[2] for r in db.execute(select(ListingAnalysis.profile, ListingAnalysis.status, func.count()).group_by(ListingAnalysis.profile, ListingAnalysis.status)).all()})}</p><table><tr><th>id</th><th>listing_external_id</th><th>profile</th><th>status</th><th>error_type</th><th>error</th><th>created_at</th><th>updated_at</th></tr>{analysis_rows}</table></section>"
        f"<section class='section'><h2>Data volume summary</h2><ul>{volume_items}</ul></section>"
        f"<section class='section'><h2>Alembic</h2><p>current DB revision: <code>{html.escape(alembic_revision)}</code></p></section>"
        f"{_render_backup_restore_retention_readiness()}"
    )
    return _render_page("System health", body)


@router.get('/alerts', response_class=HTMLResponse, dependencies=[Depends(_require_admin_api_key)])
def alerts(
    request: Request,
    db: Session = Depends(get_db),
    limit: str | None = Query(default="50"),
    search_name: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    channel: str | None = Query(default=None),
    listing_external_id: str | None = Query(default=None),
    dedupe_key: str | None = Query(default=None),
    search_job_id: str | None = Query(default=None),
    hours: str | None = Query(default="168"),
):
    api_key = request.query_params.get("api_key")
    raw_limit = (limit or "").strip()
    if raw_limit:
        try:
            jsonl_limit = max(1, min(int(raw_limit), 500))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid limit") from None
    else:
        jsonl_limit = 50
    if raw_limit:
        effective_limit = max(1, min(jsonl_limit, 200))
    else:
        effective_limit = 50
    effective_hours = _parse_int_param("hours", hours, 168, 1, 720)
    normalized_status = _safe_short_filter("status", status_filter, 32)
    if normalized_status and normalized_status not in ALERT_ATTEMPT_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid status")
    normalized_channel = _safe_short_filter("channel", channel, 32)
    normalized_listing_external_id = _safe_short_filter("listing_external_id", listing_external_id, 128)
    normalized_dedupe_key = _safe_short_filter("dedupe_key", dedupe_key, 255)
    normalized_search_job_id = _parse_optional_positive_int("search_job_id", search_job_id)
    normalized_search_name = (search_name or "").strip() or None
    rows_data, total_loaded, invalid_count = _read_jsonl_alerts(settings.jsonl_outbox_path, normalized_search_name, jsonl_limit)
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
    current_limit = _html_attr(jsonl_limit)
    current_search = _html_attr(normalized_search_name)
    since = datetime.utcnow() - timedelta(hours=effective_hours)
    base_filters = [AlertDeliveryAttempt.created_at >= since]
    if normalized_status:
        base_filters.append(AlertDeliveryAttempt.status == normalized_status)
    if normalized_channel:
        base_filters.append(AlertDeliveryAttempt.channel == normalized_channel)
    if normalized_listing_external_id:
        base_filters.append(AlertDeliveryAttempt.listing_external_id == normalized_listing_external_id)
    if normalized_dedupe_key:
        base_filters.append(AlertDeliveryAttempt.dedupe_key == normalized_dedupe_key)
    if normalized_search_job_id is not None:
        base_filters.append(AlertDeliveryAttempt.search_job_id == normalized_search_job_id)

    total_period = db.scalar(select(func.count()).select_from(AlertDeliveryAttempt).where(*base_filters)) or 0
    total_all_time = db.scalar(select(func.count()).select_from(AlertDeliveryAttempt)) or 0
    status_counts = dict(db.execute(select(AlertDeliveryAttempt.status, func.count()).where(*base_filters).group_by(AlertDeliveryAttempt.status)).all())
    channel_counts = dict(db.execute(select(AlertDeliveryAttempt.channel, func.count()).where(*base_filters).group_by(AlertDeliveryAttempt.channel)).all())
    latest_attempt = db.scalar(select(func.max(AlertDeliveryAttempt.created_at)).where(*base_filters))
    attempt_rows = db.scalars(select(AlertDeliveryAttempt).where(*base_filters).order_by(AlertDeliveryAttempt.created_at.desc(), AlertDeliveryAttempt.id.desc()).limit(effective_limit)).all()
    external_ids = [row.listing_external_id for row in attempt_rows if row.listing_external_id]
    listing_by_external = {}
    if external_ids:
        listing_by_external = {
            listing.external_id: listing.id
            for listing in db.scalars(select(Listing).where(Listing.external_id.in_(external_ids)).limit(effective_limit)).all()
        }
    match_keys = {(row.dedupe_key, row.listing_external_id, row.channel) for row in attempt_rows}
    matched_keys = set()
    if match_keys:
        matched_keys = set(
            db.execute(
                select(AlertSent.dedupe_key, AlertSent.listing_external_id, AlertSent.channel).where(
                    tuple_(AlertSent.dedupe_key, AlertSent.listing_external_id, AlertSent.channel).in_(match_keys)
                )
            ).all()
        )
    has_alert_sent = {
        row.id: (row.dedupe_key, row.listing_external_id, row.channel) in matched_keys
        for row in attempt_rows
    }
    delivery_integrity_summary = build_alert_delivery_integrity_summary(db, since=since)
    attempt_table_rows = []
    for row in attempt_rows:
        listing_id = listing_by_external.get(row.listing_external_id)
        listing_cell = html.escape(row.listing_external_id)
        if listing_id:
            listing_cell = f"<a href='{_html_attr(_admin_url(f'/admin/listings/{listing_id}', api_key))}'>{html.escape(row.listing_external_id)}</a>"
        attempt_table_rows.append(
            "<tr>"
            f"<td>{row.id}</td><td>{html.escape(str(row.created_at or '—'))}</td><td>{listing_cell}</td>"
            f"<td>{html.escape(row.channel)}</td><td>{html.escape(row.status)}</td><td>{html.escape(row.error_type or '—')}</td><td>{row.attempt_count}</td>"
            f"<td>{html.escape(str(row.sent_at or '—'))}</td><td>{html.escape(str(row.next_retry_at or '—'))}</td>"
            f"<td>{html.escape(row.search_name or '—')}</td><td>{html.escape((row.payload_hash or '')[:12])}</td>"
            f"<td>{html.escape(_redact_alert_error(row.last_error))}</td><td>{'yes' if has_alert_sent[row.id] else 'no'}</td>"
            f"<td><a href='{_html_attr(_admin_url(f'/admin/alerts/delivery-attempts/{row.id}', api_key))}'>details</a></td>"
            "</tr>"
        )
    attempts_empty = ""
    if total_all_time == 0:
        attempts_empty = "<p>Попытки доставки ещё не зафиксированы. Это нормально, если после PR20a не было новых доставок уведомлений.</p>"
    channel_summary = ", ".join(f"{html.escape(str(k))}: {v}" for k, v in sorted(channel_counts.items())) or "—"
    status_summary = "".join(f"<li>{status}: {status_counts.get(status, 0)}</li>" for status in ["success", "failed", "skipped", "unknown"])
    delivery_integrity_html = _render_delivery_integrity_summary(delivery_integrity_summary, scope_label="in selected period")
    attempts_table = (
        "<table><tr><th>id</th><th>created_at</th><th>listing_external_id</th><th>channel</th><th>status</th><th>error_type</th><th>attempt_count</th><th>sent_at</th><th>next_retry_at</th><th>search_name</th><th>payload_hash prefix</th><th>last_error preview</th><th>AlertSent match</th><th>details</th></tr>"
        + "".join(attempt_table_rows)
        + "</table>"
        if attempt_table_rows
        else ""
    )
    delivery_section = (
        "<section><h2>Попытки доставки уведомлений</h2>"
        f"<p>Period hours: {effective_hours}; total attempts in selected period: {total_period}; all-time total attempts: {total_all_time}; channels observed: {channel_summary}; latest attempt timestamp: {html.escape(str(latest_attempt or '—'))}; live delivery observed: {'yes' if total_all_time else 'no'}</p>"
        f"<ul>{status_summary}</ul>{attempts_empty}"
        f"<form method='get' action='{_html_attr(_admin_url('/admin/alerts', api_key))}'>"
        f"{_admin_query_api_key_input(api_key)}"
        f"<div class='row'><label>status<input name='status' value='{_html_attr(normalized_status)}'></label><label>channel<input name='channel' value='{_html_attr(normalized_channel)}'></label><label>listing_external_id<input name='listing_external_id' value='{_html_attr(normalized_listing_external_id)}'></label><label>dedupe_key<input name='dedupe_key' value='{_html_attr(normalized_dedupe_key)}'></label><label>search_job_id<input name='search_job_id' value='{_html_attr(normalized_search_job_id or '')}'></label><label>hours<input name='hours' type='number' min='1' max='720' value='{effective_hours}'></label><label>limit<input name='limit' type='number' min='1' max='200' value='{effective_limit}'></label></div>"
        "<button type='submit'>Apply delivery filters</button></form>"
        f"{delivery_integrity_html}{attempts_table}</section>"
    )
    body = (
        f"<h1>История уведомлений</h1><p><a href='{_html_attr(_admin_url('/admin/searches', api_key))}'>Back to searches</a> · <a href='{_html_attr(_admin_url('/admin/listings', api_key))}'>Listings</a> · <a href='{_html_attr(_admin_url('/admin/listing-analyses', api_key))}'>Listing analyses</a></p>"
        f"<p>JSONL file: <code>{html.escape(settings.jsonl_outbox_path)}</code></p>"
        f"<p>Visible: {len(rows_data)} / Loaded: {total_loaded}</p>{warning}"
        f"<form method='get' action='{_html_attr(form_action)}'><input type='hidden' name='api_key' value='{_html_attr(api_key)}'>"
        f"<div class='row'><label>search_name<input name='search_name' value='{current_search}'></label></div>"
        f"<div class='row'><label>limit<input name='limit' type='number' min='1' max='500' value='{current_limit}'></label></div>"
        f"<button type='submit'>Apply</button></form>{empty}{table}{delivery_section}"
    )
    return _render_page('Alerts', body)


@router.get('/alerts/delivery-attempts/{attempt_id}', response_class=HTMLResponse, dependencies=[Depends(_require_admin_api_key)])
def alert_delivery_attempt_detail(request: Request, attempt_id: int, db: Session = Depends(get_db)):
    if attempt_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid attempt_id")
    api_key = request.query_params.get("api_key")
    attempt = db.get(AlertDeliveryAttempt, attempt_id)
    if attempt is None:
        raise HTTPException(status_code=404, detail="Delivery attempt not found")
    alert_sent = _matching_alert_sent(db, attempt)
    eligible, eligibility_reason, listing = _manual_retry_eligibility(db, attempt)
    listing_block = "—"
    if listing is not None:
        listing_block = f"<a href='{_html_attr(_admin_url(f'/admin/listings/{listing.id}', api_key))}'>{html.escape(listing.external_id)}</a>"
    fields = [
        ("id", attempt.id),
        ("created_at", attempt.created_at),
        ("updated_at", attempt.updated_at),
        ("listing_external_id", attempt.listing_external_id),
        ("channel", attempt.channel),
        ("dedupe_key", attempt.dedupe_key),
        ("payload_hash prefix", (attempt.payload_hash or "")[:12]),
        ("status", attempt.status),
        ("attempt_count", attempt.attempt_count),
        ("sent_at", attempt.sent_at),
        ("next_retry_at", attempt.next_retry_at),
        ("search_job_id", attempt.search_job_id),
        ("search_name", attempt.search_name),
        ("error_type", attempt.error_type),
        ("last_error", _redact_alert_error(attempt.last_error, 500)),
        ("matching AlertSent", "yes" if alert_sent else "no"),
        ("delivery resolution", "Resolved by later delivery" if attempt.status in NON_SUCCESS_ALERT_DELIVERY_STATUSES and alert_sent is not None and alert_sent.created_at >= attempt.created_at else "—"),
        ("matching listing", listing_block),
    ]
    rows = "".join(
        f"<tr><th>{html.escape(str(key))}</th><td>{value if key == 'matching listing' else html.escape(str(value or '—'))}</td></tr>"
        for key, value in fields
    )
    marker_messages = {
        "retry_success": "Manual retry succeeded. A matching AlertSent was created.",
        "retry_failed": "Manual retry failed. A delivery attempt row was recorded; AlertSent was not created.",
        "retry_unknown": "Manual retry result is unknown. A delivery attempt row was recorded; AlertSent was not created.",
        "retry_skipped": "Manual retry was skipped. A delivery attempt row was recorded; AlertSent was not created.",
        "retry_not_eligible": "Manual retry was not performed because the attempt is not eligible.",
    }
    notice = "".join(
        f"<div class='note'>{html.escape(message)}</div>"
        for key, message in marker_messages.items()
        if request.query_params.get(key) == "1"
    )
    confirm = f"retry_delivery_attempt_{attempt.id}"
    retry_action = _admin_url(f"/admin/alerts/delivery-attempts/{attempt.id}/retry", api_key)
    inactive_warning = (
        "<p class='warning'>Listing is currently inactive; manual retry will still send the current stored listing payload.</p>"
        if listing is not None and hasattr(listing, "is_active") and not listing.is_active
        else ""
    )
    if not eligible:
        retry_block = f"<p>{html.escape(eligibility_reason)}</p>"
    elif not settings.admin_ui_technical_ops_enabled:
        retry_block = "<p>Manual retry is disabled because technical operations are disabled.</p>"
    else:
        retry_block = (
            f"{inactive_warning}"
            "<p class='warning'>This will send an external alert again for this single channel if eligible. "
            "This can create a new AlertSent only after successful delivery.</p>"
            f"<form method='post' action='{_html_attr(retry_action)}'>"
            f"{_technical_auth_fields(confirm, 'Manual retry targets exactly one original channel and regenerates payload from current DB listing data.')}"
            "<button type='submit'>Retry single channel</button></form>"
        )
    retry_section = f"<section><h2>Ручной повтор доставки</h2>{retry_block}</section>"
    body = (
        f"<h1>Alert delivery attempt {attempt.id}</h1>"
        f"<p><a href='{_html_attr(_admin_url('/admin/alerts', api_key))}'>Back to alerts</a></p>"
        f"{notice}<table>{rows}</table>{retry_section}"
    )
    return _render_page("Alert delivery attempt", body)


@router.post('/alerts/delivery-attempts/{attempt_id}/retry', dependencies=[Depends(_require_admin_api_key)])
async def retry_alert_delivery_attempt(request: Request, attempt_id: int, db: Session = Depends(get_db)):
    if attempt_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid attempt_id")
    form, raw_form = await _parse_mutable_form(request)
    _require_technical_write_form(request, form, raw_form)
    _require_technical_confirmation(form, f"retry_delivery_attempt_{attempt_id}")
    attempt = db.get(AlertDeliveryAttempt, attempt_id)
    if attempt is None:
        raise HTTPException(status_code=404, detail="Delivery attempt not found")
    eligible, reason, listing = _manual_retry_eligibility(db, attempt)
    if not eligible or listing is None:
        raise HTTPException(status_code=400, detail=reason)
    if _matching_alert_sent(db, attempt) is not None:
        raise HTTPException(status_code=400, detail="Matching AlertSent already exists")
    result = await _retry_single_delivery_channel(db, attempt=attempt, listing=listing)
    api_key = request.query_params.get("api_key")
    redirect_url = _admin_url(f"/admin/alerts/delivery-attempts/{attempt.id}", api_key)
    separator = "&" if "?" in redirect_url else "?"
    return RedirectResponse(f"{redirect_url}{separator}retry_{result}=1", status_code=303)



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
    return _render_page('New search', f"<h1>New search</h1><form method='post' action='{_admin_url('/admin/searches', api_key)}'>{_job_form(return_url=return_url)}{_technical_auth_fields('create_search')}<button type='submit'>Create</button></form>")


@router.post('/searches')
async def create_search(request: Request, db: Session = Depends(get_db)):
    api_key = request.query_params.get("api_key")
    form, raw_form = await _parse_mutable_form(request)
    _require_technical_write_form(request, form, raw_form)
    _require_technical_confirmation(form, "create_search")
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
        return _render_page('Validation error', f"<h1>New search</h1><div class='error'>Nothing was saved because validation failed.</div>{links}<form method='post' action='{_admin_url('/admin/searches', api_key)}'>{_job_form(type('O',(),form), str(exc), return_url=return_url)}{_technical_auth_fields('create_search')}<button type='submit'>Create</button></form>")
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
    return _render_page('Edit search', f"<h1>Edit search #{search_id}</h1><form method='post' action='{_admin_url(f'/admin/searches/{search_id}', api_key)}'>{_job_form(job, return_url=return_url)}{_technical_auth_fields('edit_search')}<button type='submit'>Save</button></form>")


@router.post('/searches/{search_id}')
async def update_search(search_id: int, request: Request, db: Session = Depends(get_db)):
    api_key = request.query_params.get("api_key")
    repo = SearchRepository(db)
    job = repo.get(search_id)
    if job is None:
        raise HTTPException(404)
    form, raw_form = await _parse_mutable_form(request)
    _require_technical_write_form(request, form, raw_form)
    _require_technical_confirmation(form, "edit_search")
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
        return _render_page('Validation error', f"<h1>Edit search #{search_id}</h1><div class='error'>Nothing was saved because validation failed.</div>{links}<form method='post' action='{_admin_url(f'/admin/searches/{search_id}', api_key)}'>{_job_form(form_job, str(exc), return_url=return_url)}{_technical_auth_fields('edit_search')}<button type='submit'>Save</button></form>")
    job.name = name
    job.source_url = form['source_url'].strip()
    job.poll_interval_sec = poll
    job.filters_json = filters
    job.is_active = 'is_active' in form
    db.commit()
    return _success_redirect(request, api_key, 'updated', form=form)


@router.post('/searches/{search_id}/activate')
async def activate(search_id: int, request: Request, db: Session = Depends(get_db)):
    api_key = request.query_params.get("api_key")
    form, raw_form = await _parse_mutable_form(request)
    _require_technical_write_form(request, form, raw_form)
    _require_technical_confirmation(form, "activate_search")
    job = SearchRepository(db).get(search_id)
    if job is None:
        raise HTTPException(404)
    job.is_active = True
    db.commit()
    return _success_redirect(request, api_key, 'updated', form=form)


@router.post('/searches/{search_id}/deactivate')
async def deactivate(search_id: int, request: Request, db: Session = Depends(get_db)):
    api_key = request.query_params.get("api_key")
    form, raw_form = await _parse_mutable_form(request)
    _require_technical_write_form(request, form, raw_form)
    _require_technical_confirmation(form, "deactivate_search")
    job = SearchRepository(db).get(search_id)
    if job is None:
        raise HTTPException(404)
    job.is_active = False
    db.commit()
    return _success_redirect(request, api_key, 'updated', form=form)


@router.post('/searches/{search_id}/reset-baseline')
async def reset_baseline(search_id: int, request: Request, db: Session = Depends(get_db)):
    api_key = request.query_params.get("api_key")
    form, raw_form = await _parse_mutable_form(request)
    _require_technical_write_form(request, form, raw_form)
    _require_technical_confirmation(form, "reset_baseline")
    job = SearchRepository(db).get(search_id)
    if job is None:
        raise HTTPException(404)
    job.baseline_initialized = False
    job.baseline_initialized_at = None
    job.next_run_at = None
    db.commit()
    return _success_redirect(request, api_key, 'updated', form=form)


@router.post('/searches/{search_id}/run-once', response_class=HTMLResponse)
async def run_once(search_id: int, request: Request):
    api_key = request.query_params.get("api_key")
    form, raw_form = await _parse_mutable_form(request)
    _require_technical_write_form(request, form, raw_form)
    _require_technical_confirmation(form, "run_once")
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
    redacted_result = _redact_obj(result)
    parser_stats = redacted_result.get("parser_stats", {}) if isinstance(redacted_result, dict) else {}
    delivery_source = result if isinstance(result, dict) else {}
    delivery_channels = sorted(
        set((delivery_source.get("delivery_attempted_by_channel") or {}).keys())
        | set((delivery_source.get("delivery_success_by_channel") or {}).keys())
        | set((delivery_source.get("delivery_skipped_by_channel") or {}).keys())
        | set((delivery_source.get("delivery_failed_by_channel") or {}).keys())
        | set((delivery_source.get("delivery_unknown_by_channel") or {}).keys())
        | set((delivery_source.get("delivery_unsuccessful_by_channel") or {}).keys())
    )
    summary_rows = [
        ("ok", redacted_result.get("ok")),
        ("error", redacted_result.get("error")),
        ("created", redacted_result.get("created")),
        ("alerted", redacted_result.get("alerted")),
        ("filtered", redacted_result.get("filtered")),
        ("total_seen", redacted_result.get("total_seen")),
        ("pages_seen", redacted_result.get("pages_seen")),
        ("pages_attempted", redacted_result.get("pages_attempted")),
        ("pagination_stopped_reason", redacted_result.get("pagination_stopped_reason")),
        ("page_errors_count", len(redacted_result.get("page_errors", []) or [])),
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
            attempted = int((delivery_source.get("delivery_attempted_by_channel") or {}).get(channel, 0) or 0)
            success = int((delivery_source.get("delivery_success_by_channel") or {}).get(channel, 0) or 0)
            skipped = int((delivery_source.get("delivery_skipped_by_channel") or {}).get(channel, 0) or 0)
            failed = int((delivery_source.get("delivery_failed_by_channel") or {}).get(channel, 0) or 0)
            unknown = int((delivery_source.get("delivery_unknown_by_channel") or {}).get(channel, 0) or 0)
            unsuccessful = int((delivery_source.get("delivery_unsuccessful_by_channel") or {}).get(channel, 0) or 0)
            delivery_rows.append(
                f"<tr><td>{html.escape(channel)}</td><td>{attempted}</td><td>{success}</td><td>{skipped}</td><td>{failed}</td><td>{unknown}</td><td>{unsuccessful}</td><td>{_delivery_badge(attempted, unsuccessful, failed, unknown)}</td></tr>"
            )
        delivery_table = f"<h2>Delivery counters</h2><table><tr><th>channel</th><th>attempted</th><th>success</th><th>skipped</th><th>failed</th><th>unknown</th><th>unsuccessful</th><th>status</th></tr>{''.join(delivery_rows)}</table>"
    body = (
        "<h1>Run once result</h1>"
        f"<h2>Summary</h2><table><tr><th>metric</th><th>value</th></tr>{summary_table}</table>"
        f"{delivery_table}"
        f"<pre>{html.escape(json.dumps(redacted_result, ensure_ascii=False, default=str, indent=2))}</pre>"
        f"<p><a href='{_admin_url('/admin/searches', api_key)}'>Back</a></p>"
    )
    return _render_page('Run once', body)
