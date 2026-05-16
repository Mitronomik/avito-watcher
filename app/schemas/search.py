from pydantic import BaseModel, HttpUrl


class SearchCreate(BaseModel):
    name: str
    source_url: HttpUrl
    min_price: int | None = None
    max_price: int | None = None
    min_area: float | None = None
    location: str | None = None
