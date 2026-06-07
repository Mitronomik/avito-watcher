# ruff: noqa: E402
import os

if os.environ.get("RUN_ALEMBIC_SMOKE") != "1":
    # Keep normal unit tests hermetic even when CI exports a PostgreSQL
    # DATABASE_URL for the dedicated Alembic migration step.
    os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
else:
    os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

_TEST_ISOLATED_ENV_VARS = (
    "ALERT_CHANNELS",
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
)

for _env_var in _TEST_ISOLATED_ENV_VARS:
    os.environ.pop(_env_var, None)

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.alert_sent import AlertSent  # noqa: F401
from app.models.listing_analysis import ListingAnalysis  # noqa: F401
from app.models.listing_search_match import ListingSearchMatch  # noqa: F401
from app.models.listing import Listing  # noqa: F401
from app.models.listing_snapshot import ListingSnapshot  # noqa: F401
from app.models.search_job import SearchJob  # noqa: F401


@pytest.fixture(autouse=True)
def isolate_runtime_env(monkeypatch):
    for env_var in _TEST_ISOLATED_ENV_VARS:
        monkeypatch.delenv(env_var, raising=False)


@pytest.fixture
def db_session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with TestingSessionLocal() as session:
        yield session
    Base.metadata.drop_all(bind=engine)
