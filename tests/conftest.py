import os

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.alert_sent import AlertSent  # noqa: F401
from app.models.listing import Listing  # noqa: F401
from app.models.listing_snapshot import ListingSnapshot  # noqa: F401
from app.models.search_job import SearchJob  # noqa: F401


@pytest.fixture
def db_session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with TestingSessionLocal() as session:
        yield session
    Base.metadata.drop_all(bind=engine)
