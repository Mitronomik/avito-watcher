from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.orm import Session

from app.models.human_review import (
    ACTION_TYPES,
    DECISION_STATUSES,
    DECISION_TYPES,
    HUMAN_VERDICTS,
    NEXT_ACTIONS,
    OUTCOME_STATUSES,
    REJECTED_REASONS,
    REVIEW_STATUSES,
    HumanReview,
    HumanReviewAction,
    InvestmentDecision,
    utcnow,
)
from app.models.listing import Listing
from app.models.listing_analysis import ListingAnalysis
from app.models.search_job import SearchJob
from app.repositories.human_reviews import HumanReviewRepository

NOTES_MAX = 5000
TEXT_MAX = 255
JSON_MAX_BYTES = 20000
CONFIRMED_NUMERIC = {
    "confirmed_purchase_price_rub", "confirmed_monthly_rent_rub", "confirmed_area_m2",
    "confirmed_opex_monthly_rub", "confirmed_opex_ratio", "confirmed_capex_initial_rub", "confirmed_vacancy_rate",
}
MONEY_FIELDS = {"amount_rub", "expected_monthly_rent_rub", "actual_monthly_rent_rub", "actual_purchase_price_rub"}


class HumanReviewValidationError(ValueError):
    pass


def build_review_context_key(listing_external_id: str, search_job_id: int | None = None, listing_analysis_id: int | None = None, context_type: str = "listing") -> str:
    if not listing_external_id or not str(listing_external_id).strip():
        raise HumanReviewValidationError("listing_external_id is required")
    context = (context_type or "listing").strip()
    if not context:
        raise HumanReviewValidationError("context_type is required")
    return f"listing:{str(listing_external_id).strip()}:search:{search_job_id if search_job_id is not None else 'none'}:analysis:{listing_analysis_id if listing_analysis_id is not None else 'none'}:context:{context}"


def _validate_choice(name: str, value: str | None, allowed: set[str]) -> None:
    if value is not None and value not in allowed:
        raise HumanReviewValidationError(f"unknown {name}: {value}")


def _decimal(name: str, value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise HumanReviewValidationError(f"{name} must be numeric") from exc
    if result < 0:
        raise HumanReviewValidationError(f"{name} must be non-negative")
    return result


def _bounded_text(name: str, value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise HumanReviewValidationError(f"{name} must be a string")
    if len(value) > limit:
        raise HumanReviewValidationError(f"{name} is too long")
    return value


def _bounded_json(name: str, value: Any) -> Any:
    if value is None:
        return None
    try:
        payload = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError) as exc:
        raise HumanReviewValidationError(f"{name} must be JSON-serializable") from exc
    if len(payload.encode("utf-8")) > JSON_MAX_BYTES:
        raise HumanReviewValidationError(f"{name} is too large")
    return value


def _validate_common(data: dict[str, Any]) -> dict[str, Any]:
    data = dict(data)
    if "listing_external_id" in data:
        if not str(data.get("listing_external_id") or "").strip():
            raise HumanReviewValidationError("listing_external_id is required")
        data["listing_external_id"] = str(data["listing_external_id"]).strip()
    for name, allowed in [("review_status", REVIEW_STATUSES), ("human_verdict", HUMAN_VERDICTS), ("next_action", NEXT_ACTIONS), ("rejected_reason", REJECTED_REASONS), ("outcome_status", OUTCOME_STATUSES)]:
        _validate_choice(name, data.get(name), allowed)
    if data.get("false_positive") and data.get("false_negative"):
        raise HumanReviewValidationError("false_positive and false_negative cannot both be true")
    if data.get("human_verdict") == "false_positive":
        data["false_positive"] = True
    if data.get("human_verdict") == "false_negative":
        data["false_negative"] = True
    for name in CONFIRMED_NUMERIC:
        if name in data:
            data[name] = _decimal(name, data[name])
    for ratio_name in ("confirmed_opex_ratio", "confirmed_vacancy_rate"):
        if data.get(ratio_name) is not None and data[ratio_name] > Decimal("1"):
            raise HumanReviewValidationError(f"{ratio_name} must be between 0 and 1")
    for name in ("reviewer", "confirmed_source"):
        if name in data:
            data[name] = _bounded_text(name, data[name], TEXT_MAX)
    if "notes" in data:
        data["notes"] = _bounded_text("notes", data["notes"], NOTES_MAX)
    if "payload_json" in data:
        data["payload_json"] = _bounded_json("payload_json", data["payload_json"])
    if "review_context_key" in data and not str(data.get("review_context_key") or "").strip():
        raise HumanReviewValidationError("review_context_key is required")
    return data


class HumanReviewService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = HumanReviewRepository(db)

    def _validate_links(self, data: dict[str, Any]) -> None:
        if data.get("listing_id") is not None and self.db.get(Listing, data["listing_id"]) is None:
            raise HumanReviewValidationError("listing_id does not exist")
        if data.get("search_job_id") is not None and self.db.get(SearchJob, data["search_job_id"]) is None:
            raise HumanReviewValidationError("search_job_id does not exist")
        if data.get("listing_analysis_id") is not None and self.db.get(ListingAnalysis, data["listing_analysis_id"]) is None:
            raise HumanReviewValidationError("listing_analysis_id does not exist")

    def create_review(self, **kwargs: Any) -> HumanReview:
        data = _validate_common(kwargs)
        if "listing_external_id" not in data:
            raise HumanReviewValidationError("listing_external_id is required")
        self._validate_links(data)
        data.setdefault("review_status", "new")
        data.setdefault("watchlist", False)
        data["review_context_key"] = data.get("review_context_key") or build_review_context_key(data["listing_external_id"], data.get("search_job_id"), data.get("listing_analysis_id"), data.pop("context_type", "listing"))
        if self.repo.get_by_context_key(data["review_context_key"]):
            raise HumanReviewValidationError("review_context_key already exists")
        now = utcnow()
        if data.get("human_verdict") or data.get("review_status") in {"reviewed", "closed"}:
            data.setdefault("reviewed_at", now)
        review = self.repo.add_review(HumanReview(**data))
        self.record_action(review.id, "created", actor=data.get("reviewer"), note=data.get("notes"), after_json={"review_status": review.review_status, "human_verdict": review.human_verdict, "next_action": review.next_action})
        return review

    def update_review(self, review_id: int, **kwargs: Any) -> HumanReview:
        review = self.get_review(review_id)
        data = _validate_common(kwargs)
        allowed = {c.name for c in HumanReview.__table__.columns} - {"id", "created_at", "updated_at"}
        before, after = {}, {}
        for key, value in data.items():
            if key not in allowed:
                continue
            old = getattr(review, key)
            if old != value:
                before[key] = str(old) if isinstance(old, Decimal) else old
                after[key] = str(value) if isinstance(value, Decimal) else value
                setattr(review, key, value)
        if (data.get("human_verdict") or data.get("review_status") in {"reviewed", "closed"}) and review.reviewed_at is None:
            review.reviewed_at = utcnow()
            before["reviewed_at"] = None
            after["reviewed_at"] = review.reviewed_at.isoformat()
        review.updated_at = utcnow()
        self.db.flush()
        if before or after:
            self.record_action(review.id, self._infer_action_type(after), actor=data.get("reviewer"), note=data.get("notes"), before_json=before, after_json=after)
        return review

    def _infer_action_type(self, after: dict[str, Any]) -> str:
        if "outcome_status" in after:
            return "outcome_updated"
        if any(k.startswith("confirmed_") for k in after):
            return "confirmed_facts_updated"
        if "human_verdict" in after:
            return "verdict_set"
        if "next_action" in after:
            return "next_action_set"
        if "review_status" in after and after["review_status"] == "closed":
            return "closed"
        return "updated"

    def record_action(self, human_review_id: int, action_type: str, actor: str | None = None, note: str | None = None, before_json: Any = None, after_json: Any = None, payload_json: Any = None) -> HumanReviewAction:
        _validate_choice("action_type", action_type, ACTION_TYPES)
        if self.repo.get_review(human_review_id) is None:
            raise HumanReviewValidationError("human_review_id does not exist")
        return self.repo.add_action(HumanReviewAction(human_review_id=human_review_id, action_type=action_type, actor=_bounded_text("actor", actor, TEXT_MAX), note=_bounded_text("note", note, NOTES_MAX), before_json=_bounded_json("before_json", before_json), after_json=_bounded_json("after_json", after_json), payload_json=_bounded_json("payload_json", payload_json)))

    def record_investment_decision(self, human_review_id: int, **kwargs: Any) -> InvestmentDecision:
        review = self.get_review(human_review_id)
        _validate_choice("decision_type", kwargs.get("decision_type"), DECISION_TYPES)
        _validate_choice("decision_status", kwargs.get("decision_status"), DECISION_STATUSES)
        data = dict(kwargs)
        for name in MONEY_FIELDS:
            if name in data:
                data[name] = _decimal(name, data[name])
        data["actor"] = _bounded_text("actor", data.get("actor"), TEXT_MAX)
        data["note"] = _bounded_text("note", data.get("note"), NOTES_MAX)
        data["payload_json"] = _bounded_json("payload_json", data.get("payload_json"))
        data.setdefault("listing_external_id", review.listing_external_id)
        decision = self.repo.add_decision(InvestmentDecision(human_review_id=human_review_id, **data))
        self.record_action(human_review_id, "investment_decision_recorded", actor=data.get("actor"), note=data.get("note"), payload_json={"investment_decision_id": decision.id, "decision_type": decision.decision_type, "decision_status": decision.decision_status})
        return decision

    def get_review(self, review_id: int) -> HumanReview:
        review = self.repo.get_review(review_id)
        if review is None:
            raise HumanReviewValidationError("human review not found")
        return review

    def list_reviews(self, **filters: Any) -> list[HumanReview]:
        return self.repo.list_reviews(**filters)

    def get_latest_review_for_listing(self, listing_external_id: str, review_context_key: str | None = None) -> HumanReview | None:
        return self.repo.latest_for_listing(listing_external_id, review_context_key)

    def get_reviews_by_context(self, review_context_key: str) -> list[HumanReview]:
        review = self.repo.get_by_context_key(review_context_key)
        return [review] if review else []

    def get_review_by_context_key(self, review_context_key: str) -> HumanReview | None:
        return self.repo.get_by_context_key(review_context_key)
