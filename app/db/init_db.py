from app.db.base import Base
from app.db.session import engine
from app.models.search_job import SearchJob  # noqa: F401
from app.models.listing import Listing  # noqa: F401
from app.models.listing_snapshot import ListingSnapshot  # noqa: F401
from app.models.alert_sent import AlertSent  # noqa: F401
from app.models.alert_delivery_attempt import AlertDeliveryAttempt  # noqa: F401
from app.models.listing_analysis import ListingAnalysis  # noqa: F401
from app.models.listing_search_match import ListingSearchMatch  # noqa: F401
from app.models.knowledge_note import KnowledgeNote  # noqa: F401
from app.models.listing_detail_snapshot import ListingDetailSnapshot  # noqa: F401
from app.models.listing_enrichment import ListingEnrichment  # noqa: F401
from app.models.market_evidence import MarketEvidenceItem, MarketResearchRun  # noqa: F401
from app.models.human_review import HumanReview, HumanReviewAction, InvestmentDecision  # noqa: F401



def init_db() -> None:
    Base.metadata.create_all(bind=engine)
