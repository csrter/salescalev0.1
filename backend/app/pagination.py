"""Reusable limit/offset pagination for list endpoints.

Returns plain lists (first page) so existing clients keep working; callers add
`?limit=&offset=` to page. The capped default is the safety net against a
single request pulling an unbounded number of rows.
"""
from dataclasses import dataclass

from fastapi import Depends, Query


@dataclass
class Page:
    limit: int
    offset: int


def paginator(default: int = 50, maximum: int = 200):
    """Build a pagination dependency with endpoint-specific defaults/caps."""

    def dep(
        limit: int = Query(default, ge=1, le=maximum),
        offset: int = Query(0, ge=0),
    ) -> Page:
        return Page(limit=limit, offset=offset)

    return Depends(dep)
