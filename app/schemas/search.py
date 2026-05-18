from pydantic import BaseModel, HttpUrl


_FILTER_KEYS = frozenset({
    "min_price",
    "max_price",
    "min_area",
    "location",
    "max_age_hours",
    "published_after",
    "published_on_date",
    "require_published_at",
})


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

    def filters_only(self) -> dict:
        """Return only the filter fields (excludes name, source_url)."""
        return {
            k: v
            for k, v in self.model_dump().items()
            if k in _FILTER_KEYS and v is not None
        }
