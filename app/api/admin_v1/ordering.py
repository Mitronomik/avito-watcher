from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from fastapi import HTTPException, status


@dataclass(frozen=True)
class Ordering:
    field: str
    direction: str
    expression: object


def parse_ordering(
    *,
    order_by: str | None,
    order_dir: str | None,
    allowed_fields: Mapping[str, object],
    default_field: str,
    default_direction: str = "desc",
) -> Ordering:
    field = order_by or default_field
    direction = (order_dir or default_direction).lower()
    if field not in allowed_fields:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="unknown order field")
    if direction not in {"asc", "desc"}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid order direction")
    return Ordering(field=field, direction=direction, expression=allowed_fields[field])
