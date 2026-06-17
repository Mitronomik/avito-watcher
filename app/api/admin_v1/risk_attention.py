from __future__ import annotations

import hashlib
import json
from typing import Any


def _hash(data: dict[str, Any]) -> str:
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()

RISK_ATTENTION_DTO_VERSION = "risk-attention-v1"
RISK_ATTENTION_ITEM_DTO_VERSION = "risk-attention-item-v1"
RISK_ATTENTION_MODEL_VERSION = "risk-attention-v1"
RISK_ATTENTION_POLICY_VERSION = "risk-attention-policy-v1"
RISK_ATTENTION_LABEL_VERSION = "risk-attention-labels-v1"
RISK_ATTENTION_MAPPING_VERSION = "risk-attention-mapping-v1"
RISK_ATTENTION_RECOMMENDED_ACTION_VERSION = "risk-attention-actions-v1"

RISK_CATEGORIES = ("data_quality", "market", "financial", "legal", "location", "object_quality", "source_quality", "system")
RISK_SEVERITIES = ("info", "low", "medium", "high", "critical")
_SEVERITY_RANK = {value: rank for rank, value in enumerate(RISK_SEVERITIES, start=1)}

RISK_ATTENTION_MAPPING: dict[str, dict[str, Any]] = {
    "missing_price": {"category": "data_quality", "severity": "high", "severity_score": 0.85, "visual_weight": 0.85, "blocking": True, "recommended_action_id": "request_price"},
    "missing_area_m2": {"category": "data_quality", "severity": "high", "severity_score": 0.85, "visual_weight": 0.85, "blocking": True, "recommended_action_id": "request_area"},
    "analysis_missing": {"category": "system", "severity": "medium", "severity_score": 0.50, "visual_weight": 0.50, "blocking": True, "recommended_action_id": "wait_for_analysis"},
    "freshness_unknown": {"category": "data_quality", "severity": "medium", "severity_score": 0.50, "visual_weight": 0.50, "blocking": False, "recommended_action_id": "verify_freshness"},
    "weak_or_review_verdict": {"category": "system", "severity": "medium", "severity_score": 0.50, "visual_weight": 0.50, "blocking": False, "recommended_action_id": "request_data"},
    "source_trace_limited": {"category": "source_quality", "severity": "medium", "severity_score": 0.50, "visual_weight": 0.50, "blocking": False, "recommended_action_id": "request_data"},
    "human_rejected": {"category": "system", "severity": "critical", "severity_score": 1.00, "visual_weight": 1.00, "blocking": True, "recommended_action_id": "review_human_rejection"},
    "unsafe_listing_url": {"category": "source_quality", "severity": "high", "severity_score": 0.85, "visual_weight": 0.85, "blocking": True, "recommended_action_id": "request_data"},
    "market_evidence_unavailable": {"category": "market", "severity": "info", "severity_score": 0.10, "visual_weight": 0.10, "blocking": False, "recommended_action_id": "request_data"},
    "market_evidence_not_checked": {"category": "market", "severity": "info", "severity_score": 0.10, "visual_weight": 0.10, "blocking": False, "recommended_action_id": "request_data"},
    "unknown_risk": {"category": "system", "severity": "info", "severity_score": 0.10, "visual_weight": 0.10, "blocking": False, "recommended_action_id": "request_data"},
}

_EXPLANATIONS = {
    "missing_price": {"ru": "Без цены невозможно корректно оценить объект для внутреннего workflow.", "en": "Without price, the listing cannot be evaluated reliably for the internal workflow."},
    "missing_area_m2": {"ru": "Без площади невозможно корректно сопоставить объект во внутреннем workflow.", "en": "Without area, the listing cannot be compared reliably in the internal workflow."},
    "analysis_missing": {"ru": "Успешный системный анализ пока отсутствует.", "en": "A successful system analysis is not available yet."},
    "freshness_unknown": {"ru": "Актуальность объявления требует дополнительной проверки.", "en": "Listing freshness needs an additional check."},
    "weak_or_review_verdict": {"ru": "Системный вердикт требует внимания оператора.", "en": "The system verdict needs operator attention."},
    "source_trace_limited": {"ru": "Трассировка источников ограничена для внутреннего workflow.", "en": "Source trace is limited for the internal workflow."},
    "human_rejected": {"ru": "Объект ранее был отклонён человеком.", "en": "The listing was previously rejected by a human reviewer."},
    "unsafe_listing_url": {"ru": "Ссылка объявления не проходит безопасную проверку источника.", "en": "The listing URL does not pass the safe source check."},
    "market_evidence_unavailable": {"ru": "Рыночные данные недоступны в текущем источнике решения.", "en": "Market evidence is unavailable in the current decision source."},
    "market_evidence_not_checked": {"ru": "Рыночные данные не проверялись в текущем контракте.", "en": "Market evidence was not checked in the current contract."},
    "unknown_risk": {"ru": "Риск не классифицирован контрактом Risk Attention v1.", "en": "This risk is not classified by the Risk Attention v1 contract."},
}
_ACTIONS = {
    "request_price": {"label": {"ru": "Запросить цену", "en": "Request price"}, "action_id": "request_data"},
    "request_area": {"label": {"ru": "Запросить площадь", "en": "Request area"}, "action_id": "request_data"},
    "wait_for_analysis": {"label": {"ru": "Дождаться анализа", "en": "Wait for analysis"}, "action_id": None},
    "verify_freshness": {"label": {"ru": "Проверить актуальность", "en": "Verify freshness"}, "action_id": "open_listing"},
    "request_data": {"label": {"ru": "Запросить данные", "en": "Request data"}, "action_id": "request_data"},
    "review_human_rejection": {"label": {"ru": "Проверить причину отклонения", "en": "Review the rejection reason"}, "action_id": None},
}


def _action(action_id: str, workflow: dict[str, Any]) -> dict[str, Any]:
    template = _ACTIONS[action_id]
    pr32_id = template["action_id"]
    actions = {a["id"]: a for a in workflow.get("allowed_actions", []) + workflow.get("blocked_actions", [])}
    executable_now = bool(pr32_id and actions.get(pr32_id, {}).get("available_now", False))
    return {"id": action_id, "label": template["label"], "action_id": pr32_id, "executable_now": executable_now}


def build_risk_attention_from_card(card: dict[str, Any]) -> dict[str, Any]:
    workflow = card.get("workflow", {})
    source_refs = {
        "listing_id": card.get("listing_id"),
        "listing_external_id": card.get("listing_external_id"),
        "listing_analysis_id": workflow.get("source_refs", {}).get("listing_analysis_id"),
        "human_review_id": workflow.get("source_refs", {}).get("human_review_id"),
        "decision_card_input_hash": card.get("input_hashes", {}).get("decision_card_input_hash"),
        "workflow_source_hash": card.get("input_hashes", {}).get("workflow_source_hash"),
    }
    risks = [enrich_top_risk(risk, workflow) for risk in card.get("top_risks", [])]
    max_severity = max((risk["severity"] for risk in risks), key=lambda s: _SEVERITY_RANK[s], default="info")
    max_visual_weight = max((risk["visual_weight"] for risk in risks), default=0.0)
    input_hash = _hash({
        "versions": [RISK_ATTENTION_MODEL_VERSION, RISK_ATTENTION_POLICY_VERSION, RISK_ATTENTION_LABEL_VERSION, RISK_ATTENTION_MAPPING_VERSION, RISK_ATTENTION_RECOMMENDED_ACTION_VERSION],
        "decision_card_input_hash": source_refs["decision_card_input_hash"],
        "workflow_source_hash": source_refs["workflow_source_hash"],
        "top_risks": [{"id": r.get("id"), "rank": r.get("rank"), "evidence_ref": r.get("evidence_ref")} for r in card.get("top_risks", [])],
        "source_refs": source_refs,
    })
    return {
        "schema_version": RISK_ATTENTION_DTO_VERSION,
        "risk_attention_model_version": RISK_ATTENTION_MODEL_VERSION,
        "risk_attention_policy_version": RISK_ATTENTION_POLICY_VERSION,
        "risk_attention_label_version": RISK_ATTENTION_LABEL_VERSION,
        "listing_id": card.get("listing_id"),
        "listing_external_id": card.get("listing_external_id"),
        "risk_count": len(risks),
        "blocking_risk_count": sum(1 for risk in risks if risk["blocking"]),
        "max_severity": max_severity,
        "max_visual_weight": max_visual_weight,
        "risks": risks,
        "source_refs": source_refs,
        "input_hashes": {"risk_attention_input_hash": input_hash, "decision_card_input_hash": source_refs["decision_card_input_hash"], "workflow_source_hash": source_refs["workflow_source_hash"]},
        "limitations": ["risk_attention_v1_enriches_decision_card_top_risks_only", "not_investment_advice", "not_appraisal", "not_valuation_report", "visual_attention_only"],
    }


def enrich_top_risk(risk: dict[str, Any], workflow: dict[str, Any]) -> dict[str, Any]:
    original_id = risk.get("id") or "unknown_risk"
    id_ = original_id if original_id in RISK_ATTENTION_MAPPING else "unknown_risk"
    mapping = RISK_ATTENTION_MAPPING[id_]
    action_id = mapping["recommended_action_id"]
    enriched = {
        **risk,
        "schema_version": RISK_ATTENTION_ITEM_DTO_VERSION,
        "original_risk_id": None if id_ == original_id else original_id,
        "id": id_,
        "label": risk.get("label") if id_ == original_id else {"ru": "Неизвестный риск", "en": "Unknown risk"},
        "label_key": risk.get("label_key") if id_ == original_id else "decision_risk.unknown_risk",
        "category": mapping["category"],
        "severity": mapping["severity"],
        "severity_score": mapping["severity_score"],
        "visual_weight": min(mapping["visual_weight"], mapping["severity_score"]),
        "blocking": mapping["blocking"],
        "blocking_scope": "visual_attention",
        "explanation": _EXPLANATIONS[id_],
        "recommended_action": _action(action_id, workflow),
    }
    return enriched
