from pydantic import BaseModel, HttpUrl


class SearchCreate(BaseModel):
    name: str
    source_url: HttpUrl
    min_price: int | None = None
    max_price: int | None = None
    min_area: float | None = None
    location: str | None = None
    max_age_hours: float | None = None
    published_after: str | None = None
    published_on_date: str | None = None
    require_published_at: bool | None = None
