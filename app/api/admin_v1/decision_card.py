from __future__ import annotations

import hashlib
import json
from typing import Any

from app.api.admin_v1.listing_dtos import iso
from app.api.admin_v1.workflow import WORKFLOW_STATE_DTO_VERSION
from app.api.admin_v1.risk_attention import build_risk_attention_from_card, enrich_top_risk
from app.api.admin_v1.readiness_checklist import build_readiness_checklist
from app.api.admin_v1.price_position import build_price_position
from app.models.human_review import HumanReview
from app.models.listing import Listing
from app.models.listing_analysis import ListingAnalysis

DECISION_CARD_DTO_VERSION = "decision-card-v1"
DECISION_CARD_MODEL_VERSION = "decision-card-v1"
DECISION_CARD_TEMPLATE_VERSION = "decision-card-templates-v1"
DECISION_CARD_POLICY_VERSION = "decision-card-policy-v1"

RECOMMENDATIONS = ("take_in_work", "needs_data", "watchlist", "reject", "analysis_pending", "insufficient_evidence")
_RECOMMENDATION_BY_WORKFLOW = {
    "analysis_pending": "analysis_pending",
    "needs_data": "needs_data",
    "rejected": "reject",
    "watchlist": "watchlist",
    "ready_for_work": "take_in_work",
}
_LABELS = {
    "take_in_work": {"ru": "Взять в работу", "en": "Take into work"},
    "needs_data": {"ru": "Нужны данные", "en": "Needs data"},
    "watchlist": {"ru": "В наблюдение", "en": "Watchlist"},
    "reject": {"ru": "Отклонить", "en": "Reject"},
    "analysis_pending": {"ru": "Ожидать анализа", "en": "Wait for analysis"},
    "insufficient_evidence": {"ru": "Недостаточно данных", "en": "Insufficient evidence"},
}
_HEADLINES = {
    "analysis_pending": {"ru": "Объект ещё не проанализирован системой.", "en": "The listing has not been analyzed by the system yet."},
    "needs_data": {"ru": "Нужны ключевые данные перед решением.", "en": "Key data is needed before a decision."},
    "take_in_work": {"ru": "Сильный кандидат для ручной проверки и контакта.", "en": "A strong candidate for manual review and contact."},
    "watchlist": {"ru": "Объект стоит наблюдать, но не брать в работу сразу.", "en": "The listing is worth watching, but not taking into work immediately."},
    "reject": {"ru": "Объект отклонён по данным человеческой проверки.", "en": "The listing was rejected based on human review data."},
    "insufficient_evidence": {"ru": "Данных недостаточно для уверенного действия.", "en": "There is not enough evidence for a confident action."},
}
_REASON_LABELS = {
    "strong_verdict": {"ru": "Сильный системный вердикт", "en": "Strong system verdict"},
    "required_data_present": {"ru": "Ключевые структурные данные есть", "en": "Key structured data is present"},
    "workflow_ready_for_work": {"ru": "Workflow готов к работе", "en": "Workflow is ready for work"},
    "human_watchlist": {"ru": "Человек добавил в наблюдение", "en": "Human review put it on watchlist"},
    "human_positive_signal": {"ru": "Положительный сигнал человека", "en": "Positive human-review signal"},
    "analysis_available": {"ru": "Системный анализ доступен", "en": "System analysis is available"},
}
_RISK_LABELS = {
    "missing_price": {"ru": "Нет цены", "en": "Price is missing"},
    "missing_area_m2": {"ru": "Нет площади", "en": "Area is missing"},
    "freshness_unknown": {"ru": "Неясная свежесть объявления", "en": "Listing freshness is unclear"},
    "analysis_missing": {"ru": "Нет успешного анализа", "en": "Successful analysis is missing"},
    "weak_or_review_verdict": {"ru": "Слабый или требующий проверки вердикт", "en": "Weak or review verdict"},
    "source_trace_limited": {"ru": "Ограниченная трассировка источников", "en": "Source trace is limited"},
    "human_rejected": {"ru": "Отклонено человеком", "en": "Rejected by human review"},
    "unsafe_listing_url": {"ru": "Небезопасная ссылка объявления", "en": "Unsafe listing URL"},
}
_STEP_LABELS = {
    "open_listing": {"ru": "Открыть объявление и проверить актуальность", "en": "Open the listing and check whether it is still actual"},
    "wait_for_analysis": {"ru": "Дождаться системного анализа", "en": "Wait for system analysis"},
    "request_price": {"ru": "Запросить цену", "en": "Request price"},
    "request_area": {"ru": "Запросить площадь", "en": "Request area"},
    "verify_freshness": {"ru": "Проверить актуальность", "en": "Verify freshness"},
    "call_owner": {"ru": "Связаться с владельцем", "en": "Call owner"},
    "request_data": {"ru": "Запросить данные", "en": "Request data"},
    "watchlist": {"ru": "Добавить в наблюдение", "en": "Watchlist"},
    "reject": {"ru": "Отклонить", "en": "Reject"},
}
_MISSING_LABELS = {"price": {"ru": "Цена", "en": "Price"}, "area_m2": {"ru": "Площадь", "en": "Area"}, "freshness": {"ru": "Свежесть", "en": "Freshness"}, "analysis": {"ru": "Анализ", "en": "Analysis"}, "market_evidence": {"ru": "Рыночные данные", "en": "Market evidence"}, "human_review": {"ru": "Проверка человеком", "en": "Human review"}}


def _ref(prefix: str, id_: int | None) -> str | None:
    return f"{prefix}:{id_}" if id_ is not None else None


def _hash(data: dict[str, Any]) -> str:
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _confidence(listing: Listing | None, analysis: ListingAnalysis | None, review: HumanReview | None, workflow: dict[str, Any]) -> str:
    if listing is None or not workflow:
        return "unknown"
    if analysis is None or listing.price is None or listing.area_m2 is None or workflow["workflow_state"] == "analysis_pending":
        return "low"
    if review and ((workflow["workflow_state"] == "watchlist" and review.watchlist) or (workflow["workflow_state"] == "rejected" and (review.human_verdict == "not_interesting" or review.rejected_reason)) or (workflow["workflow_state"] == "ready_for_work" and review.human_verdict == "interesting")):
        return "high"
    return "medium"


def _item(id_: str, labels: dict[str, dict[str, str]], prefix: str, rank: int, evidence_ref: str | None = None) -> dict[str, Any]:
    data = {"id": id_, "label": labels[id_], "label_key": f"{prefix}.{id_}", "rank": rank}
    if evidence_ref is not None:
        data["evidence_ref"] = evidence_ref
    return data


def build_decision_card(listing: Listing, analysis: ListingAnalysis | None, review: HumanReview | None, workflow: dict[str, Any]) -> dict[str, Any]:
    recommendation = _RECOMMENDATION_BY_WORKFLOW.get(workflow["workflow_state"], "insufficient_evidence")
    listing_ref = _ref("listing", listing.id)
    analysis_ref = _ref("listing_analysis", analysis.id if analysis else None)
    review_ref = _ref("human_review", review.id if review else None)

    reasons: list[dict[str, Any]] = []
    candidates = []
    if analysis and analysis.verdict == "strong":
        candidates.append(("strong_verdict", analysis_ref))
    if listing.price is not None and listing.area_m2 is not None:
        candidates.append(("required_data_present", listing_ref))
    if workflow["workflow_state"] == "ready_for_work":
        candidates.append(("workflow_ready_for_work", analysis_ref or listing_ref))
    if review and review.watchlist:
        candidates.append(("human_watchlist", review_ref))
    if review and review.human_verdict == "interesting":
        candidates.append(("human_positive_signal", review_ref))
    if analysis:
        candidates.append(("analysis_available", analysis_ref))
    for rank, (id_, ref) in enumerate(candidates[:3], start=1):
        reasons.append(_item(id_, _REASON_LABELS, "decision_reason", rank, ref))

    risk_ids: list[tuple[str, str | None]] = []
    if listing.price is None:
        risk_ids.append(("missing_price", listing_ref))
    if listing.area_m2 is None:
        risk_ids.append(("missing_area_m2", listing_ref))
    if "freshness_unknown" in workflow.get("state_reasons", []):
        risk_ids.append(("freshness_unknown", listing_ref))
    if analysis is None:
        risk_ids.append(("analysis_missing", listing_ref))
    if analysis and analysis.verdict in {"weak", "review"}:
        risk_ids.append(("weak_or_review_verdict", analysis_ref))
    if workflow["workflow_state"] == "rejected":
        risk_ids.append(("human_rejected", review_ref))
    risks = [_item(id_, _RISK_LABELS, "decision_risk", rank, ref) for rank, (id_, ref) in enumerate(risk_ids[:3], start=1)]

    missing_ids = []
    if listing.price is None:
        missing_ids.append("price")
    if listing.area_m2 is None:
        missing_ids.append("area_m2")
    if listing.published_at is None and not listing.published_label:
        missing_ids.append("freshness")
    if analysis is None:
        missing_ids.append("analysis")
    missing_ids.append("market_evidence")
    if review is None:
        missing_ids.append("human_review")
    missing_data = [{"id": id_, "label": _MISSING_LABELS[id_], "label_key": f"missing_data.{id_}", "required": id_ in {"price", "area_m2", "freshness", "analysis"}, "source_ref": listing_ref} for id_ in missing_ids[:5]]

    action_map = {a["id"]: a for a in workflow.get("allowed_actions", []) + workflow.get("blocked_actions", [])}
    step_ids = []
    if "open_listing" in action_map:
        step_ids.append("open_listing")
    if analysis is None:
        step_ids.append("wait_for_analysis")
    if listing.price is None:
        step_ids.append("request_price")
    if listing.area_m2 is None:
        step_ids.append("request_area")
    if listing.published_at is None and not listing.published_label:
        step_ids.append("verify_freshness")
    for candidate in ("call_owner", "request_data", "watchlist", "reject"):
        if candidate in action_map:
            step_ids.append(candidate)
    next_steps = []
    for rank, id_ in enumerate(dict.fromkeys(step_ids).keys(), start=1):
        if rank > 3:
            break
        action_id = "request_data" if id_ in {"request_price", "request_area"} else id_
        action = action_map.get(action_id, {})
        next_steps.append({"id": id_, "label": _STEP_LABELS[id_], "label_key": f"decision_next_step.{id_}", "action_id": action_id, "executable_now": bool(action.get("available_now", False)), "rank": rank})

    flags = [m["id"] for m in missing_data if m["id"] != "market_evidence"]
    if analysis is None:
        status = "poor"
    elif listing.price is None or listing.area_m2 is None:
        status = "poor"
    elif flags or workflow["workflow_state"] in {"needs_review", "insufficient_evidence"}:
        status = "partial"
    else:
        status = "ok"
    limitations = ["decision_card_v1_deterministic", "recommendation_scope_internal_workflow", "not_investment_advice", "not_certified_appraisal", "not_valuation_report", "no_valuation_opinion", "no_llm_wording_in_v1", "write_actions_not_executable_in_pr33", "risk_visual_severity_not_implemented_in_pr33", "market_evidence_not_checked_in_pr33"]
    if analysis is None:
        limitations.append("analysis_missing")
    if review is None:
        limitations.append("human_review_missing")
    if "freshness" in flags:
        limitations.append("freshness_unknown")

    source_trace = {"listing": {"present": True, "ref": listing_ref}, "analysis": {"present": analysis is not None, "ref": analysis_ref}, "human_review": {"present": review is not None, "ref": review_ref}, "market_evidence": {"present": None, "ref": None, "status": "not_checked_in_pr33"}}
    hash_input = {"versions": [DECISION_CARD_MODEL_VERSION, DECISION_CARD_TEMPLATE_VERSION, DECISION_CARD_POLICY_VERSION], "listing": {"id": listing.id, "external_id": listing.external_id, "url": listing.url, "title": listing.title, "price": listing.price, "area_m2": listing.area_m2, "address": listing.address, "published_at": iso(listing.published_at), "published_label": listing.published_label, "first_seen_at": iso(listing.first_seen_at), "last_seen_at": iso(listing.last_seen_at)}, "analysis": None if analysis is None else {"id": analysis.id, "status": analysis.status, "profile": analysis.profile, "score": analysis.score, "verdict": analysis.verdict, "input_hash": analysis.input_hash, "created_at": iso(analysis.created_at)}, "review": None if review is None else {"id": review.id, "review_status": review.review_status, "human_verdict": review.human_verdict, "next_action": review.next_action, "outcome_status": review.outcome_status, "watchlist": review.watchlist, "reviewed_at": iso(review.reviewed_at), "updated_at": iso(review.updated_at)}, "workflow": workflow, "source_trace": source_trace, "limitations": limitations}
    workflow_source_hash = _hash({"workflow_state": workflow.get("workflow_state"), "allowed_actions": workflow.get("allowed_actions"), "blocked_actions": workflow.get("blocked_actions"), "state_reasons": workflow.get("state_reasons"), "source_refs": workflow.get("source_refs")})

    price_position = build_price_position(listing, analysis)
    card = {"schema_version": DECISION_CARD_DTO_VERSION, "decision_card_model_version": DECISION_CARD_MODEL_VERSION, "decision_card_template_version": DECISION_CARD_TEMPLATE_VERSION, "decision_card_policy_version": DECISION_CARD_POLICY_VERSION, "recommendation_scope": "internal_workflow", "listing_id": listing.id, "listing_external_id": listing.external_id, "primary_recommendation": {"code": recommendation, "label": _LABELS[recommendation], "label_key": f"decision_recommendation.{recommendation}", "confidence": _confidence(listing, analysis, review, workflow), "reason": f"workflow_{workflow['workflow_state']}" if recommendation != "insufficient_evidence" else "workflow_insufficient_evidence"}, "headline": {"code": recommendation, "text": _HEADLINES[recommendation]}, "top_reasons": reasons, "top_risks": risks, "next_steps": next_steps, "missing_data": missing_data, "data_quality": {"status": status, "flags": flags, "limitations": ["raw_quality_facts_not_exposed_in_pr33"]}, "source_trace": source_trace, "workflow": workflow, "model_versions": {"decision_card_model_version": DECISION_CARD_MODEL_VERSION, "decision_card_template_version": DECISION_CARD_TEMPLATE_VERSION, "decision_card_policy_version": DECISION_CARD_POLICY_VERSION, "workflow_state_model_version": WORKFLOW_STATE_DTO_VERSION, "analysis_model_version": None, "selection_policy_version": None, "adjustment_model_version": None, "source_quality_model_version": None, "sale_evidence_model_version": None}, "input_hashes": {"decision_card_input_hash": _hash(hash_input), "analysis_input_hash": analysis.input_hash if analysis else None, "workflow_source_hash": workflow_source_hash, "price_position_input_hash": price_position["input_hashes"]["price_position_input_hash"]}, "price_position": price_position, "limitations": limitations}
    risk_attention = build_risk_attention_from_card(card)
    card["risk_attention"] = risk_attention
    card["top_risks"] = [enrich_top_risk(risk, workflow) for risk in risks]
    card["readiness_checklist"] = build_readiness_checklist(listing, analysis, review, workflow, card)
    return card
