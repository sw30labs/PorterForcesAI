"""Provider ports kept stable while external integrations change."""

from __future__ import annotations

from typing import Protocol

from porter_forces_ai.domain import ResearchQuery, SearchHit


class SearchProvider(Protocol):
    def search(self, request: ResearchQuery | str) -> list[SearchHit]: ...

    async def asearch(self, request: ResearchQuery | str) -> list[SearchHit]: ...

