from __future__ import annotations

import hashlib
import json
from typing import Any

from app.api.admin_v1.workflow import is_safe_public_listing_url
from app.models.human_review import HumanReview
from app.models.listing import Listing
from app.models.listing_analysis import ListingAnalysis

READINESS_CHECKLIST_DTO_VERSION = "readiness-checklist-v1"
READINESS_CHECKLIST_ITEM_DTO_VERSION = "readiness-checklist-item-v1"
READINESS_CHECKLIST_MODEL_VERSION = "readiness-checklist-v1"
READINESS_CHECKLIST_POLICY_VERSION = "readiness-policy-v1"
READINESS_CHECKLIST_LABEL_VERSION = "readiness-labels-v1"

READINESS_STATUSES = ("ready", "partial", "blocked", "not_applicable")
READINESS_ITEM_STATUSES = ("ok", "warning", "missing", "blocked", "not_applicable")
READINESS_GROUPS = (
    "listing_data",
    "freshness",
    "price_area",
    "market_evidence",
    "source_quality",
    "financial_assumptions",
    "object_quality",
    "human_confirmation",
    "report_readiness",
)
READINESS_ITEM_IDS = (
    "listing_exists",
    "listing_url_present",
    "analysis_available",
    "freshness_known",
    "price_present",
    "area_present",
    "market_evidence_checked",
    "source_trace_available",
    "financial_assumptions_present",
    "object_quality_available",
    "human_review_available",
    "report_inputs_ready",
)

_STATUS_LABELS = {
    "ready": {"ru": "Данные готовы для внутреннего разбора", "en": "Data is ready for internal review"},
    "partial": {"ru": "Можно разобрать, но нужны уточнения", "en": "Can be reviewed, but needs clarification"},
    "blocked": {"ru": "Не хватает критичных данных для внутреннего разбора", "en": "Critical data is missing for internal review"},
    "not_applicable": {"ru": "Чеклист неприменим", "en": "Checklist is not applicable"},
}

_LABELS = {
    "listing_exists": ("Объявление найдено", "Listing exists", "Строка объявления найдена в базе.", "The listing row exists in the database."),
    "listing_url_present": ("Ссылка объявления доступна", "Listing URL is present", "Ссылка есть и соответствует безопасному публичному формату Avito.", "The URL is present and matches the safe public Avito format."),
    "analysis_available": ("Анализ доступен", "Analysis is available", "Найден последний успешный системный анализ.", "The latest successful system analysis is available."),
    "freshness_known": ("Свежесть известна", "Freshness is known", "Дата или текст публикации доступны либо требуется ручная проверка свежести.", "Publication date or label is available, or freshness needs manual verification."),
    "price_present": ("Цена указана", "Price is present", "Цена есть в структурированных данных объявления.", "Price is present in the structured listing data."),
    "area_present": ("Площадь указана", "Area is present", "Площадь есть в структурированных данных объявления.", "Area is present in the structured listing data."),
    "market_evidence_checked": ("Рыночные данные проверены", "Market evidence checked", "В PR35 рыночные данные не проверяются, если нет безопасного признака применимости.", "In PR35 market evidence is not checked unless a safe applicability flag exists."),
    "source_trace_available": ("Трассировка источников доступна", "Source trace is available", "Decision Card содержит безопасную трассировку листинга и анализа.", "The Decision Card contains safe listing and analysis source trace."),
    "financial_assumptions_present": ("Финансовые допущения доступны", "Financial assumptions are present", "Финансовые сценарии не реализованы в PR35.", "Financial scenarios are not implemented in PR35."),
    "object_quality_available": ("Качество объекта доступно", "Object quality is available", "Модель качества объекта не реализована в PR35.", "Object quality model is not implemented in PR35."),
    "human_review_available": ("Человеческая проверка доступна", "Human review is available", "Последняя человеческая проверка учитывается как некритичный сигнал готовности.", "Latest human review is treated as a non-critical readiness signal."),
    "report_inputs_ready": ("Входные данные отчёта готовы", "Report inputs are ready", "Отчёты и экспорт не реализованы в PR35.", "Reports and export are not implemented in PR35."),
}

_ACTION_LABELS = {
    "open_listing": {"ru": "Открыть объявление", "en": "Open listing"},
    "wait_for_analysis": {"ru": "Дождаться анализа", "en": "Wait for analysis"},
    "verify_freshness": {"ru": "Проверить актуальность", "en": "Verify freshness"},
    "request_price": {"ru": "Запросить цену", "en": "Request price"},
    "request_area": {"ru": "Запросить площадь", "en": "Request area"},
    "request_data": {"ru": "Запросить данные", "en": "Request data"},
    "call_owner": {"ru": "Связаться с владельцем", "en": "Call owner"},
    "review_human_confirmation": {"ru": "Проверить подтверждение человеком", "en": "Review human confirmation"},
    "prepare_report_later": {"ru": "Подготовить отчёт позже", "en": "Prepare report later"},
    "none": {"ru": "Действие не требуется", "en": "No action required"},
}


def _hash(data: dict[str, Any]) -> str:
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _action(action_id: str, workflow: dict[str, Any]) -> dict[str, Any]:
    workflow_action_id = action_id if action_id in {"open_listing", "call_owner", "request_data"} else ("request_data" if action_id in {"request_price", "request_area", "verify_freshness", "review_human_confirmation"} else None)
    action_map = {a["id"]: a for a in workflow.get("allowed_actions", []) + workflow.get("blocked_actions", [])}
    executable = bool(workflow_action_id and action_map.get(workflow_action_id, {}).get("available_now", False))
    return {"id": action_id, "label": _ACTION_LABELS[action_id], "action_id": workflow_action_id, "executable_now": executable}


def _item(id_: str, group: str, status: str, critical: bool, rank: int, source_ref: str | None, evidence_refs: list[str], recommended_action: str, workflow: dict[str, Any]) -> dict[str, Any]:
    ru, en, exp_ru, exp_en = _LABELS[id_]
    return {
        "schema_version": READINESS_CHECKLIST_ITEM_DTO_VERSION,
        "id": id_,
        "group": group,
        "status": status,
        "critical": critical,
        "label": {"ru": ru, "en": en},
        "label_key": f"readiness_item.{id_}",
        "explanation": {"ru": exp_ru, "en": exp_en},
        "source_ref": source_ref,
        "evidence_refs": evidence_refs,
        "recommended_action": _action(recommended_action, workflow),
        "rank": rank,
    }


def build_readiness_checklist(listing: Listing, analysis: ListingAnalysis | None, review: HumanReview | None, workflow: dict[str, Any], decision_card: dict[str, Any]) -> dict[str, Any]:
    listing_ref = f"listing:{listing.id}"
    analysis_ref = f"listing_analysis:{analysis.id}" if analysis else None
    review_ref = f"human_review:{review.id}" if review else None
    source_trace = decision_card.get("source_trace", {})
    missing_ids = {item.get("id") for item in decision_card.get("missing_data", [])}

    items = [
        _item("listing_exists", "listing_data", "ok", True, 1, listing_ref, [listing_ref], "none", workflow),
        _item("listing_url_present", "listing_data", "ok" if is_safe_public_listing_url(listing.url) else "warning", False, 2, listing_ref, [listing_ref], "request_data", workflow),
        _item("analysis_available", "listing_data", "ok" if analysis and "analysis" not in missing_ids else "blocked", True, 3, analysis_ref or listing_ref, [ref for ref in [analysis_ref, listing_ref] if ref], "wait_for_analysis", workflow),
        _item("freshness_known", "freshness", "warning" if "freshness_unknown" in workflow.get("state_reasons", []) else ("ok" if listing.published_at is not None or listing.published_label else "missing"), False, 4, listing_ref, [listing_ref], "verify_freshness", workflow),
        _item("price_present", "price_area", "missing" if listing.price is None or "price" in missing_ids else "ok", True, 5, listing_ref, [listing_ref], "request_price", workflow),
        _item("area_present", "price_area", "missing" if listing.area_m2 is None or "area_m2" in missing_ids else "ok", True, 6, listing_ref, [listing_ref], "request_area", workflow),
    ]

    market_trace = source_trace.get("market_evidence") if isinstance(source_trace, dict) else None
    market_status = "not_applicable"
    if isinstance(market_trace, dict) and market_trace.get("present") is True:
        market_status = "ok"
    items.append(_item("market_evidence_checked", "market_evidence", market_status, False, 7, listing_ref if market_status != "not_applicable" else None, [listing_ref] if market_status != "not_applicable" else [], "request_data", workflow))

    listing_trace = source_trace.get("listing") if isinstance(source_trace, dict) else None
    analysis_trace = source_trace.get("analysis") if isinstance(source_trace, dict) else None
    source_ok = isinstance(listing_trace, dict) and listing_trace.get("present") is True and isinstance(analysis_trace, dict) and analysis_trace.get("present") in {True, False}
    items.append(_item("source_trace_available", "source_quality", "ok" if source_ok else "warning", False, 8, listing_ref, [listing_ref], "request_data", workflow))
    items.append(_item("financial_assumptions_present", "financial_assumptions", "not_applicable", False, 9, None, [], "none", workflow))
    items.append(_item("object_quality_available", "object_quality", "not_applicable", False, 10, None, [], "none", workflow))

    wf_state = workflow.get("workflow_state")
    if review:
        human_status = "ok"
    elif wf_state == "analysis_pending":
        human_status = "not_applicable"
    else:
        human_status = "warning" if wf_state in {"ready_for_work", "needs_review"} else "not_applicable"
    human_action = "call_owner" if any(a.get("id") == "call_owner" and a.get("business_applicable") for a in workflow.get("allowed_actions", [])) else "request_data"
    items.append(_item("human_review_available", "human_confirmation", human_status, False, 11, review_ref if review else (listing_ref if human_status != "not_applicable" else None), [ref for ref in [review_ref, listing_ref] if ref] if human_status != "not_applicable" else [], human_action, workflow))
    items.append(_item("report_inputs_ready", "report_readiness", "not_applicable", False, 12, None, [], "none", workflow))

    applicable = [item for item in items if item["status"] != "not_applicable"]
    if not applicable:
        status = "not_applicable"
    elif any(item["critical"] and item["status"] in {"missing", "blocked"} for item in items):
        status = "blocked"
    elif any(item["status"] in {"warning", "missing"} for item in applicable):
        status = "partial"
    else:
        status = "ready"

    critical_missing_count = sum(1 for item in items if item["critical"] and item["status"] in {"missing", "blocked"})
    limitations = ["readiness_checklist_v1_deterministic", "recommendation_scope_internal_workflow", "not_investment_advice", "not_appraisal", "not_valuation_report", "readiness_is_not_action_authorization"]
    if status == "blocked":
        limitations.extend(["readiness_blocks_do_not_mutate_workflow_in_pr35", "workflow_state_is_read_only_from_pr32"])
    source_refs = {
        "listing_id": listing.id,
        "listing_external_id": listing.external_id,
        "listing_analysis_id": analysis.id if analysis else None,
        "human_review_id": review.id if review else None,
        "decision_card_input_hash": decision_card.get("input_hashes", {}).get("decision_card_input_hash"),
        "risk_attention_input_hash": decision_card.get("risk_attention", {}).get("input_hashes", {}).get("risk_attention_input_hash"),
        "workflow_source_hash": decision_card.get("input_hashes", {}).get("workflow_source_hash"),
    }
    hash_input = {
        "versions": [READINESS_CHECKLIST_MODEL_VERSION, READINESS_CHECKLIST_POLICY_VERSION, READINESS_CHECKLIST_LABEL_VERSION],
        "listing": {"id": listing.id, "external_id": listing.external_id, "url_safe": is_safe_public_listing_url(listing.url), "price_present": listing.price is not None, "area_present": listing.area_m2 is not None, "published_present": listing.published_at is not None or bool(listing.published_label)},
        "workflow_state": workflow.get("workflow_state"),
        "state_reasons": workflow.get("state_reasons"),
        "source_hashes": source_refs,
        "source_trace_states": source_trace,
        "items": [{"id": item["id"], "status": item["status"], "rank": item["rank"], "critical": item["critical"]} for item in items],
    }
    return {
        "schema_version": READINESS_CHECKLIST_DTO_VERSION,
        "readiness_model_version": READINESS_CHECKLIST_MODEL_VERSION,
        "readiness_policy_version": READINESS_CHECKLIST_POLICY_VERSION,
        "readiness_label_version": READINESS_CHECKLIST_LABEL_VERSION,
        "listing_id": listing.id,
        "listing_external_id": listing.external_id,
        "status": status,
        "label": _STATUS_LABELS[status],
        "label_key": f"readiness_status.{status}",
        "checked_count": sum(1 for item in items if item["status"] in {"ok", "warning"}),
        "total_count": len(applicable),
        "critical_missing_count": critical_missing_count,
        "blocking_item_count": critical_missing_count,
        "items": items,
        "source_refs": source_refs,
        "input_hashes": {"readiness_checklist_input_hash": _hash(hash_input), "decision_card_input_hash": source_refs["decision_card_input_hash"], "workflow_source_hash": source_refs["workflow_source_hash"], "risk_attention_input_hash": source_refs["risk_attention_input_hash"]},
        "limitations": limitations,
    }
