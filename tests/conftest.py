# ruff: noqa: E402
import os

if os.environ.get("RUN_ALEMBIC_SMOKE") != "1":
    # Keep normal unit tests hermetic even when CI exports a PostgreSQL
    # DATABASE_URL for the dedicated Alembic migration step.
    os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
else:
    os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
# Force admin UI off before app.core.config.settings can be imported so
# local .env values such as ADMIN_UI_ENABLED=true cannot affect tests.
os.environ["ADMIN_UI_ENABLED"] = "false"

_TEST_ISOLATED_ENV_VARS = (
    "ALERT_CHANNELS",
    "ALERT_DELIVERY_BULK_GUARD_ENABLED",
    "ALERT_DELIVERY_MAX_NEW_PER_CYCLE",
    "SCORING_ENABLED",
    "DETERMINISTIC_ANALYSIS_ON_MONITOR",
    "SCRAPE_PREFERRED_ENGINE",
    "SCRAPE_ALLOWED_ENGINES",
    "SCRAPE_HEADLESS",
    "SCRAPE_MAX_PAGES",
    "SCRAPE_CARDS_PER_PAGE_LIMIT",
    "SCRAPE_STOP_ON_DUPLICATE_PAGE",
    "SCRAPE_PAGE_DELAY_MS",
    "SCRAPE_PAGE_JITTER_MS",
    "SCRAPE_ENRICH_MISSING_PUBLISHED_AT",
    "SCRAPE_ITEM_PAGE_LIMIT_PER_RUN",
    "SCRAPE_ITEM_PAGE_DELAY_MS",
    "SCRAPE_ITEM_PAGE_JITTER_MS",
    "SCRAPE_DEBUG_DUMP_HTML",
    "SCRAPE_DEBUG_DUMP_DIR",
    "SCRAPE_DEBUG_DUMP_MAX_BYTES",
    "PROXY_QUARANTINE_SECONDS",
    "EMAIL_ENABLED",
    "GOOGLE_SHEETS_WEBHOOK_ENABLED",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "LLM_REVIEW_COPILOT_RAG_ENABLED",
    "LLM_REVIEW_COPILOT_RAG_LIMIT",
    "LLM_REVIEW_COPILOT_RAG_MAX_CHARS",
    "LLM_REVIEW_COPILOT_RAG_QUERY_MAX_CHARS",
    "LLM_REVIEW_COPILOT_RAG_NOTE_TYPES",
)

for _env_var in _TEST_ISOLATED_ENV_VARS:
    os.environ.pop(_env_var, None)

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.agent_task import AgentTask  # noqa: F401
from app.models.agent_artifact import AgentArtifact  # noqa: F401
from app.models.alert_sent import AlertSent  # noqa: F401
from app.models.alert_delivery_attempt import AlertDeliveryAttempt  # noqa: F401
from app.models.listing_analysis import ListingAnalysis  # noqa: F401
from app.models.listing_search_match import ListingSearchMatch  # noqa: F401
from app.models.knowledge_note import KnowledgeNote  # noqa: F401
from app.models.listing import Listing  # noqa: F401
from app.models.listing_snapshot import ListingSnapshot  # noqa: F401
from app.models.listing_detail_snapshot import ListingDetailSnapshot  # noqa: F401
from app.models.listing_enrichment import ListingEnrichment  # noqa: F401
from app.models.market_evidence import MarketEvidenceItem, MarketResearchRun  # noqa: F401
from app.models.human_review import HumanReview, HumanReviewAction, InvestmentDecision  # noqa: F401

from app.models.search_job import SearchJob  # noqa: F401
from app.models.monitor_cycle_run import MonitorCycleRun  # noqa: F401


@pytest.fixture(autouse=True)
def isolate_runtime_env(monkeypatch):
    for env_var in _TEST_ISOLATED_ENV_VARS:
        monkeypatch.delenv(env_var, raising=False)
    monkeypatch.setenv("ADMIN_UI_ENABLED", "false")


@pytest.fixture
def db_session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with TestingSessionLocal() as session:
        yield session
    Base.metadata.drop_all(bind=engine)
