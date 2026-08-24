from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ListResponse(BaseModel, Generic[T]):
    """Envelope for collection endpoints.

    An object rather than a bare array leaves room for pagination metadata
    without a breaking change, and lets clients distinguish "no results" from a
    malformed response.
    """

    items: list[T]
    total: int
