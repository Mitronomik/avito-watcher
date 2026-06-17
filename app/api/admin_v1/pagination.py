from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, status

DEFAULT_LIMIT = 50
MAX_LIMIT = 100


@dataclass(frozen=True)
class Pagination:
    limit: int
    offset: int

    def meta(self, *, has_more: bool = False) -> dict[str, object]:
        return {"pagination": {"limit": self.limit, "offset": self.offset, "has_more": has_more}}


def parse_pagination(limit: int | None = None, offset: int | None = None) -> Pagination:
    parsed_limit = DEFAULT_LIMIT if limit is None else limit
    parsed_offset = 0 if offset is None else offset
    if parsed_limit < 0 or parsed_offset < 0:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="limit and offset must be non-negative")
    if parsed_limit > MAX_LIMIT:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="pagination limit exceeded")
    return Pagination(limit=parsed_limit, offset=parsed_offset)
