from app.db.base import Base
from app.db.session import engine
from app.models.search_job import SearchJob
from app.models.listing import Listing
from app.models.listing_snapshot import ListingSnapshot
from app.models.alert_sent import AlertSent


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
