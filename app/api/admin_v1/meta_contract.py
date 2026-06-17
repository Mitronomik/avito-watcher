from __future__ import annotations

from typing import Any

from app.analysis.market_comps import ALLOWED_SOURCE_TYPES, ALLOWED_VERIFICATION_STATUSES
from app.api.admin_v1.schemas import API_VERSION
from app.models.agent_task import ALLOWED_AGENT_TASK_STATUSES
from app.models.human_review import HUMAN_VERDICTS, NEXT_ACTIONS, OUTCOME_STATUSES, REVIEW_STATUSES

META_CONTRACT_VERSION = "v1"

ROLE_READER = "reader"
ROLE_REVIEWER = "reviewer"
ROLE_TECHNICAL = "technical"
ROLE_IDS = (ROLE_READER, ROLE_REVIEWER, ROLE_TECHNICAL)

PERMISSION_API_STATUS_READ = "api.status.read"
PERMISSION_API_META_READ = "api.meta.read"
PERMISSION_ADMIN_SYSTEM_READ = "admin.system.read"
PERMISSION_ADMIN_LISTING_ANALYSES_READ = "admin.listing_analyses.read"
PERMISSION_ADMIN_REVIEW_QUEUE_READ = "admin.review_queue.read"
PERMISSION_ADMIN_HUMAN_REVIEW_WRITE = "admin.human_review.write"
PERMISSION_ADMIN_RUN_ONCE_WRITE = "admin.run_once.write"
PERMISSION_ADMIN_RETRY_WRITE = "admin.retry.write"
PERMISSION_ADMIN_TECHNICAL_ACTIONS_WRITE = "admin.technical_actions.write"

PERMISSION_IDS = (
    PERMISSION_API_STATUS_READ,
    PERMISSION_API_META_READ,
    PERMISSION_ADMIN_SYSTEM_READ,
    PERMISSION_ADMIN_LISTING_ANALYSES_READ,
    PERMISSION_ADMIN_REVIEW_QUEUE_READ,
    PERMISSION_ADMIN_HUMAN_REVIEW_WRITE,
    PERMISSION_ADMIN_RUN_ONCE_WRITE,
    PERMISSION_ADMIN_RETRY_WRITE,
    PERMISSION_ADMIN_TECHNICAL_ACTIONS_WRITE,
)

ANALYSIS_VERDICTS = ("strong", "medium", "weak", "review")
RISK_LEVELS = ("high", "medium", "low")


def _text(ru: str, en: str) -> dict[str, str]:
    return {"ru": ru, "en": en}


ROLE_LABELS = {
    ROLE_READER: _text("Читатель", "Reader"),
    ROLE_REVIEWER: _text("Ревьюер", "Reviewer"),
    ROLE_TECHNICAL: _text("Технический доступ", "Technical"),
}

ROLE_DESCRIPTIONS = {
    ROLE_READER: _text("Может читать статус, meta contract и будущие read-only представления.", "Can read status, meta contract, and future read-only views."),
    ROLE_REVIEWER: _text("Может читать данные и использовать будущие действия ревью, когда соответствующие endpoints появятся.", "Can read data and use future review actions when corresponding endpoints exist."),
    ROLE_TECHNICAL: _text("Может читать статус и meta contract; будущие технические действия недоступны в PR30.", "Can read status and meta contract; future technical actions are unavailable in PR30."),
}

LABELS: dict[str, dict[str, dict[str, str]]] = {
    "analysis_verdict": {
        "strong": _text("Интересно", "Strong"),
        "medium": _text("Средний интерес", "Medium"),
        "weak": _text("Слабый интерес", "Weak"),
        "review": _text("На проверку", "Review"),
    },
    "roles": ROLE_LABELS,
    "review_status": {
        "new": _text("Новый", "New"),
        "needs_review": _text("Нужна проверка", "Needs review"),
        "reviewed": _text("Проверено", "Reviewed"),
        "closed": _text("Закрыто", "Closed"),
    },
    "outcome_status": {
        "sent_to_expert": _text("Сформировано экспертное заключение системы", "System expert memo prepared"),
    },
}

LEGACY_LABELS = {
    "sent_to_expert": _text("Сформировать экспертное заключение системы", "Prepare system expert memo"),
}

CAPABILITIES = {
    "admin_api_v1": True,
    "read_api": True,
    "write_api": False,
    "technical_actions": False,
    "technical_api_actions": False,
    "domain_endpoints": False,
    "decision_card": False,
    "report_export": False,
}


def _roles(value: bool, reviewer: bool | None = None, technical: bool | None = None) -> dict[str, bool]:
    return {ROLE_READER: value, ROLE_REVIEWER: value if reviewer is None else reviewer, ROLE_TECHNICAL: value if technical is None else technical}


_PERMISSION_ROWS = (
    (PERMISSION_API_STATUS_READ, _roles(True), True, True, False, "PR29", _text("Читать статус API", "Read API status")),
    (PERMISSION_API_META_READ, _roles(True), True, True, False, "PR29", _text("Читать meta contract", "Read meta contract")),
    (PERMISSION_ADMIN_SYSTEM_READ, _roles(True), False, False, True, "future", _text("Читать системные сведения API", "Read API system information")),
    (PERMISSION_ADMIN_LISTING_ANALYSES_READ, _roles(True), False, False, True, "future", _text("Читать анализы объявлений", "Read listing analyses")),
    (PERMISSION_ADMIN_REVIEW_QUEUE_READ, _roles(True), False, False, True, "future", _text("Читать очередь ревью", "Read review queue")),
    (PERMISSION_ADMIN_HUMAN_REVIEW_WRITE, _roles(False, True, True), False, False, True, "future", _text("Записать решение человека", "Record human review")),
    (PERMISSION_ADMIN_RUN_ONCE_WRITE, _roles(False, False, True), False, False, True, "future", _text("Запустить разовый мониторинг", "Run one monitoring cycle")),
    (PERMISSION_ADMIN_RETRY_WRITE, _roles(False, False, True), False, False, True, "future", _text("Повторить техническую операцию", "Retry technical operation")),
    (PERMISSION_ADMIN_TECHNICAL_ACTIONS_WRITE, _roles(False, False, True), False, False, True, "future", _text("Выполнить техническое действие", "Run technical action")),
)

ERRORS = (
    ("unauthorized", 401, _text("Не авторизовано", "Unauthorized"), _text("Запрос не прошёл проверку доступа.", "The request did not pass access validation."), False),
    ("forbidden", 403, _text("Доступ запрещён", "Forbidden"), _text("Запрос не имеет доступа к этому API.", "The request is not allowed to access this API."), False),
    ("not_found", 404, _text("Не найдено", "Not found"), _text("Запрошенный API route не найден.", "The requested API route was not found."), False),
    ("validation_error", 422, _text("Ошибка валидации", "Validation error"), _text("Запрос содержит некорректные параметры.", "The request contains invalid parameters."), False),
    ("pagination_limit_exceeded", 400, _text("Превышен лимит пагинации", "Pagination limit exceeded"), _text("Запрошенный размер страницы превышает допустимый контрактом лимит.", "The requested page size exceeds the contract limit."), False),
    ("internal_error", 500, _text("Внутренняя ошибка", "Internal error"), _text("Сервис не смог обработать запрос.", "The service could not process the request."), True),
)


def _enum_values(values: tuple[str, ...] | list[str] | set[str], descriptions: dict[str, dict[str, str]] | None = None) -> list[dict[str, Any]]:
    descriptions = descriptions or {}
    return [{"value": value, "label": LABELS.get("analysis_verdict", {}).get(value, _text(value, value.replace("_", " ").title())), "description": descriptions.get(value, _text("Статическое значение контракта.", "Static contract value."))} for value in sorted(values)]


def _enum(id_: str, values: tuple[str, ...] | list[str] | set[str], descriptions: dict[str, dict[str, str]] | None = None) -> dict[str, Any]:
    return {"id": id_, "values": _enum_values(values, descriptions), "unknown_value": {"label": _text("Неизвестно", "Unknown"), "display": "fallback"}}


def build_meta_contract() -> dict[str, Any]:
    return {
        "api_version": API_VERSION,
        "meta_contract_version": META_CONTRACT_VERSION,
        "service": "avito-watcher",
        "status": "ok",
        "roles": [{"id": role, "label": ROLE_LABELS[role], "description": ROLE_DESCRIPTIONS[role]} for role in ROLE_IDS],
        "permissions": {pid: {"id": pid, "roles": roles, "implemented": implemented, "available_now": available_now, "requires_endpoint": requires_endpoint, "introduced_in": introduced_in, "label": label} for pid, roles, implemented, available_now, requires_endpoint, introduced_in, label in _PERMISSION_ROWS},
        "enums": {
            "analysis_verdict": _enum("analysis_verdict", ANALYSIS_VERDICTS),
            "review_status": _enum("review_status", REVIEW_STATUSES),
            "human_verdict": _enum("human_verdict", HUMAN_VERDICTS),
            "next_action": _enum("next_action", NEXT_ACTIONS),
            "outcome_status": _enum("outcome_status", OUTCOME_STATUSES),
            "agent_task_status": _enum("agent_task_status", ALLOWED_AGENT_TASK_STATUSES),
            "source_type": _enum("source_type", ALLOWED_SOURCE_TYPES),
            "verification_status": _enum("verification_status", ALLOWED_VERIFICATION_STATUSES),
            "risk_level": _enum("risk_level", RISK_LEVELS),
        },
        "labels": LABELS,
        "legacy_labels": LEGACY_LABELS,
        "errors": {code: {"code": code, "http_status": http_status, "label": label, "description": description, "retryable": retryable} for code, http_status, label, description, retryable in ERRORS},
        "capabilities": CAPABILITIES,
    }
