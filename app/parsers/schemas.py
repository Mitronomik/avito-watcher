from dataclasses import dataclass, field


@dataclass
class ListingCard:
    external_id: str
    url: str
    title: str = ""
    price: float | None = None
    address: str = ""
    area_m2: float | None = None
    rooms: str = ""
    raw: dict = field(default_factory=dict)
