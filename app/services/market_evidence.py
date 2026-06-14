from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.agent_task import AgentTask
from app.models.market_evidence import ALLOWED_EVIDENCE_TYPES, MarketEvidenceItem
from app.repositories.market_evidence import MarketEvidenceRepository
from app.services.research_agent import (
    DEFAULT_RESEARCH_PROFILE,
    MARKET_RESEARCH_TASK_TYPE,
    validate_research_agent_response,
)

TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "yclid",
    "gclid",
    "fbclid",
}


class MarketEvidenceError(Exception):
    def __init__(self, message: str, error_type: str) -> None:
        super().__init__(message)
        self.error_type = error_type


@dataclass
class MarketEvidenceIngestResult:
    run_id: int
    agent_task_id: int
    listing_external_id: str | None
    created_run: bool
    created_items: int
    reused_items: int
    skipped_items: int
    non_reusable_items: int
    confidence: float | None
    checked_at: str
    expires_at: str | None

    def model_dump(self) -> dict:
        return asdict(self)


def normalize_source_url(url: str | None) -> str | None:
    if not url:
        return None
    parts = urlsplit(str(url)[:1000].strip())
    scheme = parts.scheme.lower() or "https"
    host = parts.netloc.lower()
    query = urlencode(
        [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k.lower() not in TRACKING_PARAMS
        ],
        doseq=True,
    )
    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit((scheme, host, path, query, ""))[:1000]


def _norm_text(value: object) -> str:
    return " ".join(("" if value is None else str(value)).lower().split())


def content_hash(payload: dict) -> str:
    stable = {k: payload.get(k) for k in sorted(payload)}
    return hashlib.sha256(
        json.dumps(
            stable, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


class MarketEvidenceService:
    def __init__(self, db: Session, *, now: datetime | None = None) -> None:
        self.db = db
        self.repo = MarketEvidenceRepository(db)
        self.now = now or datetime.now(UTC).replace(tzinfo=None)

    def ingest_agent_task(self, task_id: int) -> MarketEvidenceIngestResult:
        task = self.db.get(AgentTask, task_id)
        if task is None:
            raise MarketEvidenceError(
                "AgentTask not found", "market_evidence_task_not_found"
            )
        if task.task_type != MARKET_RESEARCH_TASK_TYPE:
            raise MarketEvidenceError(
                "Wrong task type", "market_evidence_wrong_task_type"
            )
        if task.status != "success":
            raise MarketEvidenceError(
                "Task is not successful", "market_evidence_task_not_success"
            )
        raw = (task.result_json or {}).get("result", task.result_json or {})
        try:
            result = validate_research_agent_response(
                raw,
                schema_version=str(
                    raw.get("schema_version", settings.research_agent_schema_version)
                )
                if isinstance(raw, dict)
                else settings.research_agent_schema_version,
                research_profile=str(
                    raw.get("research_profile", DEFAULT_RESEARCH_PROFILE)
                )
                if isinstance(raw, dict)
                else DEFAULT_RESEARCH_PROFILE,
                listing_external_id=str(
                    raw.get("listing_external_id", task.listing_external_id or "")
                )
                if isinstance(raw, dict)
                else str(task.listing_external_id or ""),
                max_output_chars=settings.research_agent_max_output_chars,
            )
        except Exception as exc:
            raise MarketEvidenceError(
                str(exc), "market_evidence_invalid_result"
            ) from exc
        if result.get("schema_version") != settings.research_agent_schema_version:
            raise MarketEvidenceError(
                "Invalid schema version", "market_evidence_invalid_schema_version"
            )

        checked_at = self.now
        expires_at = checked_at + timedelta(
            days=settings.market_evidence_default_ttl_days
        )
        output_hash = content_hash(result)
        run = self.repo.get_run_by_agent_task_id(task.id)
        created_run = run is None
        if run is None:
            run = self.repo.create_run(
                agent_task_id=task.id,
                listing_external_id=result.get("listing_external_id")
                or task.listing_external_id,
                listing_analysis_id=task.listing_analysis_id,
                research_profile=result.get("research_profile")
                or DEFAULT_RESEARCH_PROFILE,
                status="success",
                provider=(task.payload_json or {}).get("provider"),
                model=(task.payload_json or {}).get("model"),
                schema_version=result["schema_version"],
                prompt_version=(task.payload_json or {}).get("prompt_version"),
                input_hash=(task.payload_json or {}).get("input_hash"),
                output_hash=output_hash,
                query_plan_json=result.get("query_plan", [])[:5],
                sources_json=result.get("sources", [])[:10],
                summary=result.get("summary", "")[:1000],
                limitations_json=result.get("limitations", [])[:10],
                confidence=result.get("confidence"),
                checked_at=checked_at,
                expires_at=expires_at,
            )

        created = reused = skipped = non_reusable = 0
        for values in self._extract_items(run.id, result, task, checked_at, expires_at):
            if self.repo.get_item_by_run_type_hash(
                run.id, values["evidence_type"], values["content_hash"]
            ):
                reused += 1
                continue
            item = self.repo.create_item(**values)
            created += 1
            if not item.is_reusable:
                non_reusable += 1
        return MarketEvidenceIngestResult(
            run.id,
            task.id,
            run.listing_external_id,
            created_run,
            created,
            reused,
            skipped,
            non_reusable,
            run.confidence,
            checked_at.isoformat(),
            expires_at.isoformat(),
        )

    def _source(self, sources: list[dict], indexes: list[int]) -> dict | None:
        if not indexes:
            return None
        return sources[indexes[0]]

    def _reuse(
        self, evidence_type: str, confidence: float, source: dict | None
    ) -> tuple[bool, str | None]:
        if confidence < settings.market_evidence_min_confidence_for_reuse:
            return False, "low_confidence"
        if source is None:
            return False, "missing_source"
        if evidence_type not in ALLOWED_EVIDENCE_TYPES:
            return False, "not_reusable_type"
        return True, None

    def _base(
        self,
        run_id: int,
        result: dict,
        task: AgentTask,
        evidence_type: str,
        item: dict,
        checked_at: datetime,
        expires_at: datetime,
    ) -> dict:
        sources = result.get("sources", [])
        indexes = item.get("source_indexes", [])
        source = self._source(sources, indexes)
        confidence = float(item.get("confidence", result.get("confidence", 0.0)) or 0.0)
        reusable, reason = self._reuse(evidence_type, confidence, source)
        norm_url = normalize_source_url((source or {}).get("url"))
        loc = item.get("location_text")
        location_key = _norm_text(loc) if loc else None
        hash_payload = {
            "evidence_type": evidence_type,
            "asset_type": item.get("asset_type") or "unknown",
            "deal_type": item.get("deal_type") or "unknown",
            "location": location_key,
            "claim": item.get("claim")
            or item.get("assumption")
            or item.get("description")
            or item.get("similarity_notes"),
            "area_m2": item.get("area_m2"),
            "price_rub": item.get("price_rub"),
            "rent_rub_per_month": item.get("rent_rub_per_month"),
            "price_per_m2_rub": item.get("price_per_m2_rub"),
            "rent_per_m2_rub": item.get("rent_per_m2_rub"),
            "source_url_normalized": norm_url,
            "source_title": _norm_text((source or {}).get("title")),
            "source_publisher": _norm_text((source or {}).get("publisher")),
            "source_published_at": (source or {}).get("published_at"),
        }
        return {
            "run_id": run_id,
            "listing_external_id": result.get("listing_external_id")
            or task.listing_external_id,
            "listing_analysis_id": task.listing_analysis_id,
            "evidence_type": evidence_type,
            "research_profile": result.get("research_profile")
            or DEFAULT_RESEARCH_PROFILE,
            "asset_type": item.get("asset_type") or "unknown",
            "deal_type": item.get("deal_type") or "unknown",
            "location_text": loc,
            "location_key": location_key,
            "title": (source or {}).get("title"),
            "claim": item.get("claim")
            or item.get("assumption")
            or item.get("similarity_notes"),
            "description": item.get("evidence")
            or item.get("description")
            or item.get("why_it_matters"),
            "area_m2": item.get("area_m2"),
            "price_rub": item.get("price_rub"),
            "rent_rub_per_month": item.get("rent_rub_per_month"),
            "price_per_m2_rub": item.get("price_per_m2_rub"),
            "rent_per_m2_rub": item.get("rent_per_m2_rub"),
            "source_url": (source or {}).get("url"),
            "source_url_normalized": norm_url,
            "source_title": (source or {}).get("title"),
            "source_publisher": (source or {}).get("publisher"),
            "source_published_at": (source or {}).get("published_at"),
            "source_indexes_json": indexes,
            "evidence_json": item,
            "confidence": confidence,
            "is_reusable": reusable,
            "reuse_block_reason": reason,
            "checked_at": checked_at,
            "expires_at": expires_at,
            "content_hash": content_hash(hash_payload),
        }

    def _extract_items(
        self,
        run_id: int,
        result: dict,
        task: AgentTask,
        checked_at: datetime,
        expires_at: datetime,
    ) -> list[dict]:
        items = []
        for comp in result.get("comparable_candidates", []):
            items.append(
                self._base(
                    run_id,
                    result,
                    task,
                    "comparable_candidate",
                    comp,
                    checked_at,
                    expires_at,
                )
            )
        for finding in result.get("findings", []):
            items.append(
                self._base(
                    run_id, result, task, "finding", finding, checked_at, expires_at
                )
            )
        for assumption in result.get("market_assumptions_to_verify", []):
            items.append(
                self._base(
                    run_id,
                    result,
                    task,
                    "assumption_to_verify",
                    assumption,
                    checked_at,
                    expires_at,
                )
            )
        for risk in result.get("risks", []):
            items.append(
                self._base(run_id, result, task, "risk", risk, checked_at, expires_at)
            )
        for opportunity in result.get("opportunities", []):
            items.append(
                self._base(
                    run_id,
                    result,
                    task,
                    "opportunity",
                    opportunity,
                    checked_at,
                    expires_at,
                )
            )
        return items


class MarketEvidenceRetriever:
    def __init__(self, db: Session) -> None:
        self.db = db

    def retrieve(self, **kwargs) -> list[MarketEvidenceItem]:
        now = datetime.now(UTC).replace(tzinfo=None)
        limit = (
            kwargs.pop("limit", None) or settings.market_evidence_max_retrieval_items
        )
        min_conf = kwargs.pop("min_confidence", None)
        if min_conf is None and not kwargs.get("include_non_reusable", False):
            min_conf = settings.market_evidence_min_confidence_for_reuse
        return MarketEvidenceRepository(self.db).retrieve_items(
            **kwargs, min_confidence=min_conf, limit=limit, now=now
        )


class MarketResearchRagContextBuilder:
    def __init__(
        self, db: Session, retriever: MarketEvidenceRetriever | None = None
    ) -> None:
        self.retriever = retriever or MarketEvidenceRetriever(db)

    def build_context(self, **kwargs) -> dict:
        items = self.retriever.retrieve(**kwargs)
        return {
            "context_type": "market_research_rag_v0",
            "retrieval_backend": "sql",
            "items": [self._item(i) for i in items],
            "limitations": [
                "SQL-backed advisory market evidence only; not scoring input in PR15."
            ],
        }

    def _item(self, item: MarketEvidenceItem) -> dict:
        return {
            "evidence_item_id": item.id,
            "evidence_type": item.evidence_type,
            "asset_type": item.asset_type,
            "deal_type": item.deal_type,
            "location_text": item.location_text,
            "claim": item.claim,
            "metrics": {
                "area_m2": item.area_m2,
                "price_rub": item.price_rub,
                "rent_rub_per_month": item.rent_rub_per_month,
                "price_per_m2_rub": item.price_per_m2_rub,
                "rent_per_m2_rub": item.rent_per_m2_rub,
            },
            "confidence": item.confidence,
            "checked_at": item.checked_at.isoformat() if item.checked_at else None,
            "expires_at": item.expires_at.isoformat() if item.expires_at else None,
            "source_url": item.source_url,
        }
