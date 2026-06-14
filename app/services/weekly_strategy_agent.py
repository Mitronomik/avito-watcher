from __future__ import annotations

import hashlib
import json

import httpx
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.agent_task import AgentTask
from app.models.alert_sent import AlertSent
from app.models.knowledge_note import KnowledgeNote
from app.models.listing import Listing
from app.models.listing_analysis import ListingAnalysis
from app.models.listing_search_match import ListingSearchMatch
from app.models.market_evidence import MarketEvidenceItem, MarketResearchRun
from app.models.search_job import SearchJob

WEEKLY_STRATEGY_AGENT_TASK_TYPE = "weekly_strategy_agent"
ALLOWED_NEXT_PR = {"none", *{f"PR{i}" for i in range(18, 46)}}
GUARDRAILS = [
    "agent proposes, human approves",
    "advisory report only",
    "no automatic mutations",
    "stats snapshot is the source of truth",
    "context refs are advisory only",
]


def stable_json(data: Any) -> str:
    return json.dumps(
        data, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")
    )


def sha256_json(data: Any) -> str:
    return hashlib.sha256(stable_json(data).encode()).hexdigest()


class WeeklyStrategyAgentPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    period_days: int = Field(default=7, ge=1, le=30)
    search_ids: list[int] | None = None
    include_system_memory: bool = True
    include_market_evidence_stats: bool = True
    include_agent_task_stats: bool = True
    max_examples_per_section: int = Field(default=10, ge=1, le=25)


class WeeklyStrategyRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    area: Literal[
        "search_filters",
        "parser",
        "data_quality",
        "analysis_rules",
        "market_evidence",
        "agent_usage",
        "operations",
        "roadmap",
    ]
    priority: Literal["low", "medium", "high"]
    recommendation: str
    rationale: str
    expected_impact: str | None = None
    suggested_human_action: str
    requires_code_change: bool = False
    requires_filter_change: bool = False
    requires_manual_review: bool = True
    related_search_ids: list[int] = []
    related_listing_external_ids: list[str] = []
    evidence_refs: list[str] = []


class WeeklyStrategyAgentModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confidence: float = Field(ge=0, le=1)
    executive_summary: str
    health_status: Literal["good", "watch", "degraded", "insufficient_data"]
    key_findings: list[str]
    search_quality_findings: list[str]
    data_quality_findings: list[str]
    market_evidence_findings: list[str]
    agent_task_findings: list[str]
    operational_findings: list[str]
    recommendations: list[WeeklyStrategyRecommendation]
    suggested_next_pr: str | None = None
    limitations: list[str] = []


class WeeklyStrategyAgentResult(WeeklyStrategyAgentModelOutput):
    schema_version: str
    prompt_version: str
    period_days: int
    period_start_at: datetime
    period_end_at: datetime
    report_as_of_datetime: datetime
    report_as_of_date: str
    generated_at: datetime
    provider: str | None = None
    model: str | None = None
    stats_snapshot_hash: str
    context_hash: str | None = None
    used_context_refs: list[str] = []
    human_approval_required: bool = True
    side_effects_performed: bool = False
    allowed_mutation_scope: Literal["agent_tasks_only"] = "agent_tasks_only"
    guardrails_acknowledged: list[str] = []


class WeeklyStrategyProvider(Protocol):
    provider: str
    model: str

    def complete(
        self, *, prompt: str, timeout_sec: int, max_output_chars: int
    ) -> str: ...


class OffWeeklyStrategyProvider:
    provider = "off"
    model = ""

    def complete(self, *, prompt: str, timeout_sec: int, max_output_chars: int) -> str:
        raise RuntimeError("weekly_strategy_agent_provider_disabled")


class OpenAICompatibleWeeklyStrategyProvider:
    provider = "openai_compatible"

    def __init__(self) -> None:
        self.model = settings.weekly_strategy_agent_model or settings.llm_model

    def complete(self, *, prompt: str, timeout_sec: int, max_output_chars: int) -> str:
        if not settings.weekly_strategy_agent_base_url:
            raise RuntimeError("weekly_strategy_agent_config_missing_base_url")
        if not self.model:
            raise RuntimeError("weekly_strategy_agent_config_missing_model")
        headers = {}
        api_key = settings.weekly_strategy_agent_api_key or settings.llm_api_key
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        last_error: Exception | None = None
        for _attempt in range(
            max(int(settings.weekly_strategy_agent_max_retries), 0) + 1
        ):
            try:
                response = httpx.post(
                    f"{settings.weekly_strategy_agent_base_url}/v1/chat/completions",
                    headers=headers,
                    timeout=max(int(timeout_sec), 1),
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": "Return strict JSON only."},
                            {"role": "user", "content": prompt},
                        ],
                        "response_format": {"type": "json_object"},
                    },
                )
                response.raise_for_status()
                content = (
                    response.json()
                    .get("choices", [{}])[0]
                    .get("message", {})
                    .get("content")
                )
                if not isinstance(content, str) or not content.strip():
                    raise RuntimeError("weekly_strategy_agent_empty_provider_result")
                return content[:max_output_chars]
            except httpx.TimeoutException as exc:
                raise TimeoutError("weekly_strategy_agent_provider_timeout") from exc
            except Exception as exc:  # noqa: BLE001 - provider failures fail closed.
                last_error = exc
        raise RuntimeError(
            str(last_error) if last_error else "weekly_strategy_agent_provider_failed"
        )


def resolve_weekly_strategy_provider() -> WeeklyStrategyProvider:
    if settings.weekly_strategy_agent_provider == "openai_compatible":
        return OpenAICompatibleWeeklyStrategyProvider()
    return OffWeeklyStrategyProvider()


class WeeklyStrategyStatsCollector:
    def __init__(self, db: Session) -> None:
        self.db = db

    def collect(
        self,
        *,
        payload: WeeklyStrategyAgentPayload,
        period_start_at: datetime,
        period_end_at: datetime,
    ) -> dict[str, Any]:
        search_stmt = select(SearchJob).order_by(SearchJob.id)
        if payload.search_ids:
            search_stmt = search_stmt.where(SearchJob.id.in_(payload.search_ids))
        else:
            search_stmt = search_stmt.where(SearchJob.is_active.is_(True))
        searches = list(self.db.scalars(search_stmt).all())
        search_ids = [s.id for s in searches]
        search_stats = []
        for s in searches:
            created = (
                self.db.scalar(
                    select(func.count())
                    .select_from(Listing)
                    .where(
                        Listing.first_seen_at >= period_start_at,
                        Listing.first_seen_at <= period_end_at,
                    )
                )
                or 0
            )
            matched_q = (
                select(func.count())
                .select_from(ListingSearchMatch)
                .where(
                    ListingSearchMatch.search_job_id == s.id,
                    ListingSearchMatch.first_seen_at >= period_start_at,
                    ListingSearchMatch.first_seen_at <= period_end_at,
                )
            )
            alerts = dict(
                self.db.execute(
                    select(AlertSent.channel, func.count())
                    .where(
                        AlertSent.created_at >= period_start_at,
                        AlertSent.created_at <= period_end_at,
                    )
                    .group_by(AlertSent.channel)
                ).all()
            )
            search_stats.append(
                {
                    "search_id": s.id,
                    "search_name": s.name,
                    "is_active": s.is_active,
                    "last_run_at": (
                        s.last_checked_at.isoformat() if s.last_checked_at else None
                    ),
                    "last_success_at": (
                        s.last_success_at.isoformat() if s.last_success_at else None
                    ),
                    "last_error": s.last_error,
                    "created_listings_count": created,
                    "matched_listings_count": self.db.scalar(matched_q) or 0,
                    "alerts_count_by_channel": alerts,
                }
            )
        analysis_rows = self.db.execute(
            select(
                ListingAnalysis.profile,
                ListingAnalysis.status,
                ListingAnalysis.verdict,
                func.count(),
                func.avg(ListingAnalysis.score),
            )
            .where(
                ListingAnalysis.created_at >= period_start_at,
                ListingAnalysis.created_at <= period_end_at,
            )
            .group_by(
                ListingAnalysis.profile, ListingAnalysis.status, ListingAnalysis.verdict
            )
        ).all()
        analysis_stats = [
            {
                "profile": r[0],
                "status": r[1],
                "verdict": r[2],
                "count": r[3],
                "avg_score": float(r[4] or 0),
            }
            for r in analysis_rows
        ]
        risk_counts: dict[str, dict[str, Any]] = {}
        for a in self.db.scalars(
            select(ListingAnalysis).where(
                ListingAnalysis.created_at >= period_start_at,
                ListingAnalysis.created_at <= period_end_at,
            )
        ).all():
            risks = a.risks_json or {}
            keys = risks.keys() if isinstance(risks, dict) else []
            for k in keys:
                bucket = risk_counts.setdefault(
                    k, {"risk_flag": k, "count": 0, "example_listing_external_ids": []}
                )
                bucket["count"] += 1
                if (
                    len(bucket["example_listing_external_ids"])
                    < payload.max_examples_per_section
                ):
                    bucket["example_listing_external_ids"].append(a.listing_external_id)
        market = {}
        if payload.include_market_evidence_stats:
            market = {
                "market_research_runs_by_status": dict(
                    self.db.execute(
                        select(MarketResearchRun.status, func.count()).group_by(
                            MarketResearchRun.status
                        )
                    ).all()
                ),
                "market_evidence_items_count": self.db.scalar(
                    select(func.count()).select_from(MarketEvidenceItem)
                )
                or 0,
            }
        task_stats = []
        if payload.include_agent_task_stats:
            rows = self.db.execute(
                select(
                    AgentTask.task_type,
                    AgentTask.status,
                    func.count(),
                    func.max(AgentTask.error_type),
                    func.max(AgentTask.created_at),
                ).group_by(AgentTask.task_type, AgentTask.status)
            ).all()
            task_stats = [
                {
                    "task_type": r[0],
                    "status": r[1],
                    "count": r[2],
                    "last_error_type": r[3],
                    "last_created_at": r[4].isoformat() if r[4] else None,
                }
                for r in rows
            ]
        examples = [
            {
                "external_id": listing.external_id,
                "search_id": listing.id,
                "title": (listing.title or "")[:160],
                "price": listing.price,
                "area_m2": listing.area_m2,
            }
            for listing in self.db.scalars(
                select(Listing)
                .order_by(Listing.first_seen_at.desc())
                .limit(payload.max_examples_per_section)
            ).all()
        ]
        return {
            "period": {
                "start": period_start_at.isoformat(),
                "end": period_end_at.isoformat(),
            },
            "search_ids_scope": search_ids,
            "search_stats": search_stats,
            "analysis_stats": analysis_stats,
            "risk_flags": sorted(
                risk_counts.values(), key=lambda x: (-x["count"], x["risk_flag"])
            ),
            "market_evidence_stats": market,
            "agent_task_stats": task_stats,
            "alert_delivery_stats": {
                "alerts_sent_by_channel": dict(
                    self.db.execute(
                        select(AlertSent.channel, func.count())
                        .where(
                            AlertSent.created_at >= period_start_at,
                            AlertSent.created_at <= period_end_at,
                        )
                        .group_by(AlertSent.channel)
                    ).all()
                ),
                "latest_alert_id": self.db.scalar(select(func.max(AlertSent.id))),
            },
            "examples": examples,
        }


def collect_system_memory_context(
    db: Session, *, max_chars: int
) -> tuple[list[dict[str, Any]], str | None, list[str], list[str]]:
    notes = []
    refs = []
    used = 0
    for n in db.scalars(
        select(KnowledgeNote)
        .where(KnowledgeNote.is_active.is_(True))
        .order_by(KnowledgeNote.priority.desc(), KnowledgeNote.id.desc())
        .limit(20)
    ).all():
        body = (n.body_md or "")[:1000]
        item = {
            "ref": f"knowledge_note:{n.id}",
            "note_type": n.note_type,
            "title": n.title,
            "body": body,
        }
        add = len(stable_json(item))
        if used + add > max_chars:
            break
        used += add
        notes.append(item)
        refs.append(item["ref"])
    return notes, (sha256_json(notes) if notes else None), refs, []


def build_weekly_strategy_prompt(
    *, stats_snapshot: dict[str, Any], context: list[dict[str, Any]], max_chars: int
) -> str:
    prompt = """You are advisory only. Agent proposes, human approves. You must not claim that actions were performed. You must not recommend automatic mutation without human approval. Distinguish facts from hypotheses. Stats snapshot is the source of truth. Context refs are advisory only. Do not invent metrics, listings, searches, evidence, unknown IDs, or evidence refs. Do not change score/verdict, filters, code, or suppress alerts. Return strict JSON only with model-controlled analytical fields."""
    body = stable_json(
        {
            "guardrails": GUARDRAILS,
            "stats_snapshot": stats_snapshot,
            "system_memory_context": context,
            "required_sections": [
                "executive_summary",
                "key_findings",
                "search/filter recommendations",
                "data-quality recommendations",
                "market-evidence recommendations",
                "agent-usage recommendations",
                "operational risks",
                "suggested next human-approved action",
            ],
        }
    )
    out = prompt + "\nINPUT:\n" + body
    return out[:max_chars]


def validate_model_output(
    content: str,
    *,
    known_search_ids: set[int],
    known_listing_external_ids: set[str],
    known_evidence_refs: set[str],
) -> WeeklyStrategyAgentModelOutput:
    try:
        data = json.loads(content)
    except Exception as exc:
        raise ValueError("weekly_strategy_agent_invalid_result") from exc
    model = WeeklyStrategyAgentModelOutput.model_validate(data)
    limitations = list(model.limitations)
    if model.suggested_next_pr and model.suggested_next_pr not in ALLOWED_NEXT_PR:
        limitations.append(
            f"validation_warning_unknown_suggested_next_pr:{model.suggested_next_pr}"
        )
        model.suggested_next_pr = None
    for rec in model.recommendations:
        rec.related_search_ids = [
            i for i in rec.related_search_ids if i in known_search_ids
        ]
        rec.related_listing_external_ids = [
            i
            for i in rec.related_listing_external_ids
            if i in known_listing_external_ids
        ]
        rec.evidence_refs = [i for i in rec.evidence_refs if i in known_evidence_refs]
    model.limitations = limitations
    return model


def build_weekly_strategy_input_hash(
    *,
    payload: WeeklyStrategyAgentPayload,
    period_start_at: datetime,
    period_end_at: datetime,
    report_as_of_date: str,
    stats_snapshot_hash: str,
    context_hash: str | None,
    prompt_version: str,
    schema_version: str,
) -> str:
    return sha256_json(
        {
            "task_type": WEEKLY_STRATEGY_AGENT_TASK_TYPE,
            "normalized_payload": payload.model_dump(),
            "period_days": payload.period_days,
            "period_start_at": period_start_at.isoformat(),
            "period_end_at": period_end_at.isoformat(),
            "report_as_of_date": report_as_of_date,
            "selected_search_ids_scope": payload.search_ids or "active_all",
            "stats_snapshot_hash": stats_snapshot_hash,
            "context_hash": context_hash,
            "prompt_version": prompt_version,
            "schema_version": schema_version,
        }
    )


class WeeklyStrategyAgentService:
    def __init__(
        self, db: Session, provider: WeeklyStrategyProvider | None = None
    ) -> None:
        self.db = db
        self.provider = provider or resolve_weekly_strategy_provider()

    def run(self, task: AgentTask) -> dict[str, Any]:
        if not settings.weekly_strategy_agent_enabled:
            return {
                "status": "skipped",
                "error_type": "weekly_strategy_agent_disabled",
                "reason": "weekly_strategy_agent_disabled",
            }
        if (
            settings.weekly_strategy_agent_provider == "off"
            or self.provider.provider == "off"
        ):
            return {
                "status": "failed",
                "error_type": "weekly_strategy_agent_provider_disabled",
                "error_message": "weekly_strategy_agent_provider_disabled",
            }
        try:
            payload = WeeklyStrategyAgentPayload.model_validate(task.payload_json or {})
        except ValidationError as exc:
            return {
                "status": "failed",
                "error_type": "weekly_strategy_agent_invalid_payload",
                "error_message": str(exc),
            }
        as_of = datetime.now(UTC)
        end = as_of
        start = end - timedelta(days=payload.period_days)
        as_of_date = end.date().isoformat()
        stats = WeeklyStrategyStatsCollector(self.db).collect(
            payload=payload,
            period_start_at=start.replace(tzinfo=None),
            period_end_at=end.replace(tzinfo=None),
        )
        stats_hash = sha256_json(stats)
        context = []
        context_hash = None
        refs = []
        limitations = []
        if payload.include_system_memory:
            try:
                context, context_hash, refs, ctx_lim = collect_system_memory_context(
                    self.db, max_chars=4000
                )
                limitations += ctx_lim
            except Exception:
                limitations.append("system_memory_context_unavailable")
        prompt = build_weekly_strategy_prompt(
            stats_snapshot=stats,
            context=context,
            max_chars=settings.weekly_strategy_agent_max_input_chars,
        )
        input_hash = build_weekly_strategy_input_hash(
            payload=payload,
            period_start_at=start,
            period_end_at=end,
            report_as_of_date=as_of_date,
            stats_snapshot_hash=stats_hash,
            context_hash=context_hash,
            prompt_version=settings.weekly_strategy_agent_prompt_version,
            schema_version=settings.weekly_strategy_agent_schema_version,
        )
        task.payload_json = {
            "normalized_payload": payload.model_dump(),
            "period_start_at": start.isoformat(),
            "period_end_at": end.isoformat(),
            "report_as_of_datetime": as_of.isoformat(),
            "report_as_of_date": as_of_date,
            "stats_snapshot_json": stats,
            "stats_snapshot_hash": stats_hash,
            "context_hash": context_hash,
            "input_hash": input_hash,
        }
        known_listing = {x for x in self.db.scalars(select(Listing.external_id)).all()}
        try:
            content = self.provider.complete(
                prompt=prompt,
                timeout_sec=settings.weekly_strategy_agent_timeout_sec,
                max_output_chars=settings.weekly_strategy_agent_max_output_chars,
            )
            model = validate_model_output(
                content,
                known_search_ids=set(stats.get("search_ids_scope", [])),
                known_listing_external_ids=known_listing,
                known_evidence_refs=set(refs),
            )
        except TimeoutError:
            return {
                "status": "failed",
                "error_type": "weekly_strategy_agent_provider_timeout",
                "error_message": "weekly_strategy_agent_provider_timeout",
            }
        except Exception as exc:
            return {
                "status": "failed",
                "error_type": "weekly_strategy_agent_invalid_result",
                "error_message": str(exc),
            }
        model_data = model.model_dump()
        model_limitations = model_data.pop("limitations", [])
        result = WeeklyStrategyAgentResult(
            **model_data,
            schema_version=settings.weekly_strategy_agent_schema_version,
            prompt_version=settings.weekly_strategy_agent_prompt_version,
            period_days=payload.period_days,
            period_start_at=start,
            period_end_at=end,
            report_as_of_datetime=as_of,
            report_as_of_date=as_of_date,
            generated_at=as_of,
            provider=self.provider.provider,
            model=self.provider.model or settings.weekly_strategy_agent_model,
            stats_snapshot_hash=stats_hash,
            context_hash=context_hash,
            used_context_refs=refs,
            guardrails_acknowledged=GUARDRAILS,
            limitations=[*limitations, *model_limitations],
        )
        out = result.model_dump(mode="json")
        out["status"] = "success"
        out["input_hash"] = input_hash
        return out
