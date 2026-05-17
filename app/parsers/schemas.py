from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ListingCard:
    external_id: str
    url: str
    title: str = ""
    price: float | None = None
    address: str = ""
    area_m2: float | None = None
    rooms: str = ""
    published_label: str = ""
    published_at: datetime | None = None
    raw: dict = field(default_factory=dict)
