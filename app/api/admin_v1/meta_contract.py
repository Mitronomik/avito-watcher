from __future__ import annotations

from typing import Any

from app.analysis.market_comps import ALLOWED_SOURCE_TYPES, ALLOWED_VERIFICATION_STATUSES
from app.api.admin_v1.decision_card import DECISION_CARD_DTO_VERSION, RECOMMENDATIONS
from app.api.admin_v1.price_position import (
    PRICE_POSITION_CHART_REASONS,
    PRICE_POSITION_CONFIDENCE,
    PRICE_POSITION_DTO_VERSION,
    PRICE_POSITION_LOCATION_BASIS,
    PRICE_POSITION_METRICS,
    PRICE_POSITION_CODES,
    PRICE_POSITION_RANGE_BASIS,
    CHART_REASON_LABELS,
    CONFIDENCE_LABELS,
    LOCATION_LABELS,
    METRIC_LABELS,
    POSITION_LABELS,
    RANGE_BASIS_LABELS,
)
from app.api.admin_v1.risk_attention import RISK_ATTENTION_DTO_VERSION, RISK_CATEGORIES, RISK_SEVERITIES
from app.api.admin_v1.readiness_checklist import READINESS_CHECKLIST_DTO_VERSION, READINESS_GROUPS, READINESS_ITEM_IDS, READINESS_ITEM_STATUSES, READINESS_STATUSES
from app.api.admin_v1.schemas import API_VERSION
from app.api.admin_v1.workflow import WORKFLOW_ACTIONS, WORKFLOW_STATE_DTO_VERSION, WORKFLOW_STATES
from app.agents.contracts import (
    AGENT_TASK_REGISTRY_VERSION,
    AGENT_WORKFLOW_REGISTRY_VERSION,
    AgentSafetyCategory,
    AgentSideEffect,
    AgentTaskClass,
)
from app.agents.registry import get_agent_task_registry, get_agent_workflow_registry
from app.models.agent_task import ALLOWED_AGENT_TASK_STATUSES
from app.models.human_review import HUMAN_VERDICTS, NEXT_ACTIONS, OUTCOME_STATUSES, REVIEW_STATUSES

META_CONTRACT_VERSION = "v1"

ROLE_READER = "reader"
ROLE_REVIEWER = "reviewer"
ROLE_TECHNICAL = "technical"
ROLE_IDS = (ROLE_READER, ROLE_REVIEWER, ROLE_TECHNICAL)

PERMISSION_API_STATUS_READ = "api.status.read"
PERMISSION_API_META_READ = "api.meta.read"
PERMISSION_API_SYSTEM_READ = "api.system.read"
PERMISSION_API_LISTING_ANALYSES_READ = "api.listing_analyses.read"
PERMISSION_API_REVIEW_QUEUE_READ = "api.review_queue.read"
PERMISSION_ADMIN_HUMAN_REVIEW_WRITE = "admin.human_review.write"
PERMISSION_ADMIN_RUN_ONCE_WRITE = "admin.run_once.write"
PERMISSION_ADMIN_RETRY_WRITE = "admin.retry.write"
PERMISSION_ADMIN_TECHNICAL_ACTIONS_WRITE = "admin.technical_actions.write"

PERMISSION_IDS = (
    PERMISSION_API_STATUS_READ,
    PERMISSION_API_META_READ,
    PERMISSION_API_SYSTEM_READ,
    PERMISSION_API_LISTING_ANALYSES_READ,
    PERMISSION_API_REVIEW_QUEUE_READ,
    PERMISSION_ADMIN_HUMAN_REVIEW_WRITE,
    PERMISSION_ADMIN_RUN_ONCE_WRITE,
    PERMISSION_ADMIN_RETRY_WRITE,
    PERMISSION_ADMIN_TECHNICAL_ACTIONS_WRITE,
)



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
    "workflow_state": {
        "new": _text("Новый", "New"),
        "analysis_pending": _text("Ожидает анализа", "Analysis pending"),
        "needs_review": _text("Нужна проверка", "Needs review"),
        "needs_data": _text("Нужны данные", "Needs data"),
        "ready_for_work": _text("Готово к работе", "Ready for work"),
        "watchlist": _text("В наблюдении", "Watchlist"),
        "rejected": _text("Отклонено", "Rejected"),
        "report_ready": _text("Отчёт готов", "Report ready"),
        "closed": _text("Закрыто", "Closed"),
    },
    "risk_category": {
        "data_quality": _text("Качество данных", "Data quality"),
        "market": _text("Рынок", "Market"),
        "financial": _text("Финансы", "Financial"),
        "legal": _text("Юридическое", "Legal"),
        "location": _text("Локация", "Location"),
        "object_quality": _text("Качество объекта", "Object quality"),
        "source_quality": _text("Качество источника", "Source quality"),
        "system": _text("Система", "System"),
    },
    "risk_severity": {
        "info": _text("Информация", "Info"),
        "low": _text("Низкая", "Low"),
        "medium": _text("Средняя", "Medium"),
        "high": _text("Высокая", "High"),
        "critical": _text("Критическая", "Critical"),
    },
    "readiness_status": {
        "ready": _text("Данные готовы для внутреннего разбора", "Data is ready for internal review"),
        "partial": _text("Можно разобрать, но нужны уточнения", "Can be reviewed, but needs clarification"),
        "blocked": _text("Не хватает критичных данных для внутреннего разбора", "Critical data is missing for internal review"),
        "not_applicable": _text("Чеклист неприменим", "Checklist is not applicable"),
    },
    "readiness_item_status": {
        "ok": _text("ОК", "OK"),
        "warning": _text("Требует внимания", "Needs attention"),
        "missing": _text("Отсутствует", "Missing"),
        "blocked": _text("Блокирует", "Blocked"),
        "not_applicable": _text("Неприменимо", "Not applicable"),
    },
    "readiness_group": {key: _text(key, key.replace("_", " ").title()) for key in READINESS_GROUPS},
    "readiness_item_id": {key: _text(key, key.replace("_", " ").title()) for key in READINESS_ITEM_IDS},
    "decision_recommendation": {
        "analysis_pending": _text("Ожидать анализа", "Wait for analysis"),
        "needs_data": _text("Нужны данные", "Needs data"),
        "watchlist": _text("В наблюдение", "Watchlist"),
        "reject": _text("Отклонить", "Reject"),
        "take_in_work": _text("Взять в работу", "Take into work"),
        "insufficient_evidence": _text("Недостаточно данных", "Insufficient evidence"),
    },
    "workflow_action": {
        "open_listing": _text("Открыть объявление", "Open listing"),
        "take_in_work": _text("Взять в работу", "Take in work"),
        "request_data": _text("Запросить данные", "Request data"),
        "call_owner": _text("Связаться с владельцем", "Call owner"),
        "watchlist": _text("Добавить в наблюдение", "Watchlist"),
        "reject": _text("Отклонить", "Reject"),
        "generate_memo": _text("Сформировать memo", "Generate memo"),
        "generate_commercial_offer": _text("Сформировать КП", "Generate commercial offer"),
        "export_report": _text("Экспортировать отчёт", "Export report"),
        "close": _text("Закрыть", "Close"),
    },
}

LEGACY_LABELS = {
    "sent_to_expert": _text("Сформировать экспертное заключение системы", "Prepare system expert memo"),
}

CAPABILITIES = {
    "admin_api_v1": True,
    "read_api": True,
    "write_api": False,
    "technical_api_actions": False,
    "decision_card": True,
    "risk_attention": True,
    "readiness_checklist": True,
    "price_position": True,
    "report_export": False,
    "workflow_state_read": True,
    "workflow_actions_execute": False,
}


def _roles(value: bool, reviewer: bool | None = None, technical: bool | None = None) -> dict[str, bool]:
    return {ROLE_READER: value, ROLE_REVIEWER: value if reviewer is None else reviewer, ROLE_TECHNICAL: value if technical is None else technical}


_PERMISSION_ROWS = (
    (PERMISSION_API_STATUS_READ, _roles(True), True, True, False, "PR29", _text("Читать статус API", "Read API status")),
    (PERMISSION_API_META_READ, _roles(True), True, True, False, "PR29", _text("Читать meta contract", "Read meta contract")),
    (PERMISSION_API_SYSTEM_READ, _roles(True), False, False, True, "future", _text("Читать системные сведения API", "Read API system information")),
    (PERMISSION_API_LISTING_ANALYSES_READ, _roles(True), False, False, True, "future", _text("Читать анализы объявлений через API", "Read listing analyses through API")),
    (PERMISSION_API_REVIEW_QUEUE_READ, _roles(True), False, False, True, "future", _text("Читать очередь ревью через API", "Read review queue through API")),
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


ENUM_LABELS: dict[str, dict[str, dict[str, str]]] = {
    "review_status": LABELS["review_status"],
    "human_verdict": {
        "false_negative": _text("Ложно отрицательное", "False negative"),
        "false_positive": _text("Ложно положительное", "False positive"),
        "interesting": _text("Интересно", "Interesting"),
        "needs_more_data": _text("Нужно больше данных", "Needs more data"),
        "neutral": _text("Нейтрально", "Neutral"),
        "not_interesting": _text("Не интересно", "Not interesting"),
    },
    "next_action": {
        "add_to_watchlist": _text("Добавить в наблюдение", "Add to watchlist"),
        "call_owner": _text("Связаться с владельцем", "Contact owner"),
        "do_nothing": _text("Ничего не делать", "Do nothing"),
        "open_listing": _text("Открыть объявление", "Open listing"),
        "reject": _text("Отклонить", "Reject"),
        "request_documents": _text("Запросить документы", "Request documents"),
        "run_data_quality_review": _text("Проверить качество данных", "Run data quality review"),
        "run_market_research": _text("Запустить анализ рынка", "Run market research"),
        "send_to_expert": _text("Подготовить системное заключение", "Prepare system memo"),
    },
    "outcome_status": {
        "closed": _text("Закрыто", "Closed"),
        "contacted_owner": _text("Связались с владельцем", "Contacted owner"),
        "deal_candidate": _text("Кандидат в сделку", "Deal candidate"),
        "deal_done": _text("Сделка завершена", "Deal done"),
        "deal_lost": _text("Сделка потеряна", "Deal lost"),
        "documents_requested": _text("Документы запрошены", "Documents requested"),
        "not_started": _text("Не начато", "Not started"),
        "offer_made": _text("Предложение сделано", "Offer made"),
        "rejected_after_call": _text("Отклонено после звонка", "Rejected after call"),
        "sent_to_expert": _text("Сформировано экспертное заключение системы", "System expert memo prepared"),
        "under_review": _text("На проверке", "Under review"),
        "waiting_response": _text("Ожидает ответа", "Waiting response"),
        "watchlist": _text("В наблюдении", "Watchlist"),
    },
    "agent_task_status": {
        "canceled": _text("Отменено", "Canceled"),
        "failed": _text("Ошибка", "Failed"),
        "pending": _text("Ожидает", "Pending"),
        "running": _text("Выполняется", "Running"),
        "skipped": _text("Пропущено", "Skipped"),
        "success": _text("Успешно", "Success"),
    },
    "source_type": {
        "asking": _text("Цена предложения", "Asking"),
        "confirmed": _text("Подтверждено", "Confirmed"),
        "effective": _text("Эффективная цена", "Effective"),
        "manual": _text("Вручную", "Manual"),
        "unknown": _text("Неизвестно", "Unknown"),
    },
    "workflow_state": LABELS["workflow_state"],
    "workflow_action": LABELS["workflow_action"],
    "decision_recommendation": LABELS["decision_recommendation"],
    "risk_category": LABELS["risk_category"],
    "risk_severity": LABELS["risk_severity"],
    "readiness_status": LABELS["readiness_status"],
    "readiness_item_status": LABELS["readiness_item_status"],
    "readiness_group": LABELS["readiness_group"],
    "readiness_item_id": LABELS["readiness_item_id"],
    "price_position": POSITION_LABELS,
    "price_position_confidence": CONFIDENCE_LABELS,
    "price_position_location_basis": LOCATION_LABELS,
    "price_position_chart_reason": CHART_REASON_LABELS,
    "price_position_metric": METRIC_LABELS,
    "price_position_range_basis": RANGE_BASIS_LABELS,
    "verification_status": {
        "human_verified": _text("Проверено человеком", "Human verified"),
        "unknown": _text("Неизвестно", "Unknown"),
        "unverified": _text("Не проверено", "Unverified"),
        "verified": _text("Проверено", "Verified"),
    },
}


def _enum_values(id_: str, values: tuple[str, ...] | list[str] | set[str], descriptions: dict[str, dict[str, str]] | None = None) -> list[dict[str, Any]]:
    descriptions = descriptions or {}
    labels = ENUM_LABELS[id_]
    return [{"value": value, "label": labels[value], "description": descriptions.get(value, _text("Статическое значение контракта.", "Static contract value."))} for value in sorted(values)]


def _enum(id_: str, values: tuple[str, ...] | list[str] | set[str], descriptions: dict[str, dict[str, str]] | None = None) -> dict[str, Any]:
    return {"id": id_, "values": _enum_values(id_, values, descriptions), "unknown_value": {"label": _text("Неизвестно", "Unknown"), "display": "fallback"}}


def build_meta_contract() -> dict[str, Any]:
    agent_task_registry = get_agent_task_registry()
    agent_workflow_registry = get_agent_workflow_registry()
    return {
        "api_version": API_VERSION,
        "meta_contract_version": META_CONTRACT_VERSION,
        "workflow_contract_version": WORKFLOW_STATE_DTO_VERSION,
        "decision_card_contract_version": DECISION_CARD_DTO_VERSION,
        "risk_attention_contract_version": RISK_ATTENTION_DTO_VERSION,
        "readiness_checklist_contract_version": READINESS_CHECKLIST_DTO_VERSION,
        "price_position_contract_version": PRICE_POSITION_DTO_VERSION,
        "service": "avito-watcher",
        "status": "ok",
        "roles": [{"id": role, "label": ROLE_LABELS[role], "description": ROLE_DESCRIPTIONS[role]} for role in ROLE_IDS],
        "permissions": {pid: {"id": pid, "roles": roles, "implemented": implemented, "available_now": available_now, "requires_endpoint": requires_endpoint, "introduced_in": introduced_in, "label": label} for pid, roles, implemented, available_now, requires_endpoint, introduced_in, label in _PERMISSION_ROWS},
        "enums": {
            "review_status": _enum("review_status", REVIEW_STATUSES),
            "human_verdict": _enum("human_verdict", HUMAN_VERDICTS),
            "next_action": _enum("next_action", NEXT_ACTIONS),
            "outcome_status": _enum("outcome_status", OUTCOME_STATUSES),
            "agent_task_status": _enum("agent_task_status", ALLOWED_AGENT_TASK_STATUSES),
            "source_type": _enum("source_type", ALLOWED_SOURCE_TYPES),
            "verification_status": _enum("verification_status", ALLOWED_VERIFICATION_STATUSES),
            "workflow_state": _enum("workflow_state", WORKFLOW_STATES),
            "workflow_action": _enum("workflow_action", WORKFLOW_ACTIONS),
            "decision_recommendation": _enum("decision_recommendation", RECOMMENDATIONS),
            "risk_category": _enum("risk_category", RISK_CATEGORIES),
            "risk_severity": _enum("risk_severity", RISK_SEVERITIES),
            "readiness_status": _enum("readiness_status", READINESS_STATUSES),
            "readiness_item_status": _enum("readiness_item_status", READINESS_ITEM_STATUSES),
            "readiness_group": _enum("readiness_group", READINESS_GROUPS),
            "readiness_item_id": _enum("readiness_item_id", READINESS_ITEM_IDS),
            "price_position_code": _enum("price_position", PRICE_POSITION_CODES),
            "price_position_confidence": _enum("price_position_confidence", PRICE_POSITION_CONFIDENCE),
            "price_position_location_basis": _enum("price_position_location_basis", PRICE_POSITION_LOCATION_BASIS),
            "price_position_chart_reason": _enum("price_position_chart_reason", PRICE_POSITION_CHART_REASONS),
            "price_position_metric": _enum("price_position_metric", PRICE_POSITION_METRICS),
            "price_position_range_basis": _enum("price_position_range_basis", PRICE_POSITION_RANGE_BASIS),
        },
        "labels": LABELS,
        "legacy_labels": LEGACY_LABELS,
        "errors": {code: {"code": code, "http_status": http_status, "label": label, "description": description, "retryable": retryable} for code, http_status, label, description, retryable in ERRORS},
        "capabilities": CAPABILITIES,
        "agent_contracts": {
            "enabled": True,
            "registry_version": AGENT_TASK_REGISTRY_VERSION,
            "workflow_registry_version": AGENT_WORKFLOW_REGISTRY_VERSION,
            "contract_versions": sorted({contract.agent_contract_version for contract in agent_task_registry.values()}),
            "task_classes": [item.value for item in AgentTaskClass],
            "safety_categories": [item.value for item in AgentSafetyCategory],
            "side_effects": [item.value for item in AgentSideEffect],
            "task_types": {
                task_type: {
                    "task_type": contract.task_type,
                    "task_class": contract.task_class.value,
                    "schema_version": contract.schema_version,
                    "agent_contract_version": contract.agent_contract_version,
                    "implemented": contract.implemented,
                    "handler_required": contract.handler_required,
                    "safety_category": contract.safety_category.value,
                    "blocking": contract.blocking,
                    "required_capabilities": list(contract.required_capabilities),
                    "legacy_compatibility": contract.legacy_compatibility,
                    "legacy_semantic_label": contract.legacy_semantic_label,
                    "limitations": list(contract.limitations),
                }
                for task_type, contract in agent_task_registry.items()
            },
            "workflows": {
                workflow_id: {
                    "workflow_id": workflow.workflow_id,
                    "workflow_label": workflow.workflow_label,
                    "implemented": workflow.implemented,
                    "task_classes": [item.value for item in workflow.task_classes],
                    "max_chain_depth": workflow.max_chain_depth,
                    "blocking_policy": workflow.blocking_policy,
                    "required_capabilities": list(workflow.required_capabilities),
                    "limitations": list(workflow.limitations),
                }
                for workflow_id, workflow in agent_workflow_registry.items()
            },
        },
    }
