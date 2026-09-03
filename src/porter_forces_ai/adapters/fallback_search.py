"""Keyless fallback discovery engines and an ordered provider chain.

DuckDuckGo remains the primary provider because its adapter can tell a genuine
"no results" page apart from a block. These fallbacks deliberately cannot make
that distinction: their block markup is not modelled here, so they are only ever
trusted when they return at least one parsable result. An empty answer from a
fallback is treated as *unavailable*, never as evidence that nothing exists.

That asymmetry is the point. Guessing at another engine's challenge markup would
let a block be read as "no results", which is exactly the silent failure the
DuckDuckGo adapter exists to prevent.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from porter_forces_ai.adapters.duckduckgo import (
    SearchUnavailableError,
    canonicalize_public_url,
)
from porter_forces_ai.domain import ResearchQuery, SearchHit
from porter_forces_ai.egress import EgressPolicy
from porter_forces_ai.ports import SearchProvider


class _FallbackEngineError(RuntimeError):
    """A fallback engine produced nothing that can be trusted as discovery."""


@dataclass(frozen=True)
class EngineContract:
    """One keyless engine and the module path of its ddgs implementation."""

    provider: str
    module: str
    class_name: str


MOJEEK = EngineContract(
    provider="mojeek", module="ddgs.engines.mojeek", class_name="Mojeek"
)
BRAVE = EngineContract(provider="brave", module="ddgs.engines.brave", class_name="Brave")
BING = EngineContract(provider="bing", module="ddgs.engines.bing", class_name="Bing")

# Verified 2026-08-25 against the installed ddgs 9.15.0 parsers: Brave and Bing
# return usable rows; Mojeek served an HTTP 200 captcha page, and Yahoo,
# Startpage, Google, and Wikipedia parsed to zero rows. Yandex parses but is
# deliberately excluded from a US/EU regulated-banking profile.
DEFAULT_FALLBACK_ENGINES: tuple[EngineContract, ...] = (BRAVE, BING)
FALLBACK_ENGINES: dict[str, EngineContract] = {
    contract.provider: contract for contract in (BRAVE, BING, MOJEEK)
}


def _load_engine(contract: EngineContract, *, timeout: int) -> Any:
    from importlib import import_module

    module = import_module(contract.module)
    engine_class = getattr(module, contract.class_name, None)
    if engine_class is None:
        raise _FallbackEngineError(
            f"installed ddgs has no {contract.class_name} engine"
        )
    engine = engine_class(timeout=timeout)
    required = (
        "build_payload",
        "extract_results",
        "post_extract_results",
        "search_method",
        "search_url",
        "http_client",
    )
    if any(not hasattr(engine, member) for member in required):
        raise _FallbackEngineError(
            f"installed {contract.provider} backend is incompatible with this adapter"
        )
    return engine


@dataclass
class KeylessEngineSearchProvider:
    """Discovery through one keyless ddgs engine, trusted only on real results."""

    contract: EngineContract
    policy: EgressPolicy = field(default_factory=EgressPolicy)
    region: str = "us-en"
    timeout_seconds: int = 15
    max_results: int = 8
    engine_factory: Callable[..., Any] | None = None

    def search(self, request: ResearchQuery | str) -> list[SearchHit]:
        if isinstance(request, ResearchQuery):
            query_id: str | None = request.query_id
            query = request.query
        else:
            query_id = None
            query = request
        safe_query = self.policy.validate(query)
        try:
            rows = self._rows(safe_query)
        except SearchUnavailableError:
            raise
        except Exception as exc:
            raise SearchUnavailableError(
                f"{self.contract.provider} search failed: {type(exc).__name__}"
            ) from exc
        hits = self._hits(rows, query_id=query_id)
        if self.max_results > 0:
            hits = hits[:self.max_results]
        if not hits:
            # Emptiness from a fallback is not a verified finding.
            raise SearchUnavailableError(
                f"{self.contract.provider} returned no usable results and cannot "
                "confirm genuine emptiness"
            )
        return hits

    async def asearch(self, request: ResearchQuery | str) -> list[SearchHit]:
        return self.search(request)

    def _rows(self, safe_query: str) -> Sequence[Any]:
        engine = (
            self.engine_factory(timeout=self.timeout_seconds)
            if self.engine_factory is not None
            else _load_engine(self.contract, timeout=self.timeout_seconds)
        )
        try:
            payload = engine.build_payload(
                query=safe_query,
                region=self.region,
                safesearch="moderate",
                timelimit=None,
                page=1,
            )
            values = (
                {"params": payload}
                if engine.search_method == "GET"
                else {"data": payload}
            )
            response = engine.http_client.request(
                engine.search_method, engine.search_url, **values
            )
            status = getattr(response, "status_code", None)
            if status != 200:
                raise SearchUnavailableError(
                    f"{self.contract.provider} returned HTTP status {status}"
                )
            text = getattr(response, "text", None)
            if not isinstance(text, str) or not text.strip():
                raise SearchUnavailableError(
                    f"{self.contract.provider} returned a blank response"
                )
            try:
                rows = engine.post_extract_results(engine.extract_results(text))
            except Exception:
                # Parser errors can quote source material; keep it out of the
                # message and the exception chain.
                raise SearchUnavailableError(
                    f"{self.contract.provider} response could not be parsed"
                ) from None
            if not isinstance(rows, list):
                raise SearchUnavailableError(
                    f"{self.contract.provider} returned an invalid result collection"
                )
            return rows
        finally:
            with suppress(Exception):
                nested = getattr(engine.http_client, "client", None)
                close = getattr(nested, "close", None)
                if callable(close):
                    close()

    @staticmethod
    def _field(row: Any, *names: str) -> str:
        """Read a field from either a mapping row or a ddgs result object."""

        for name in names:
            value = (
                row.get(name)
                if isinstance(row, Mapping)
                else getattr(row, name, None)
            )
            if value:
                return str(value)
        return ""

    def _hits(
        self,
        rows: Sequence[Any],
        *,
        query_id: str | None,
    ) -> list[SearchHit]:
        retrieved_at = datetime.now(UTC)
        hits: list[SearchHit] = []
        seen: set[str] = set()
        for row in rows:
            raw_url = self._field(row, "href", "url")
            try:
                url = canonicalize_public_url(raw_url)
                hit = SearchHit(
                    query_id=query_id,
                    title=(self._field(row, "title") or url)[:1_000],
                    url=url,
                    snippet=self._field(row, "body", "snippet")[:4_000],
                    provider=self.contract.provider,
                    retrieved_at=retrieved_at,
                )
            except (TypeError, ValueError):
                continue
            if url in seen:
                continue
            seen.add(url)
            hits.append(hit)
        return hits


@dataclass
class FallbackSearchProvider:
    """Try each provider in order; only an unavailable provider advances the chain.

    A verified empty result from the primary provider is a finding and ends the
    chain. Only unavailability moves to the next engine, so a fallback can add
    coverage but can never overturn a truthful "nothing found".
    """

    providers: Sequence[SearchProvider]
    jitter: Callable[[], float] = random.random
    handoff_seconds: float = 1.0
    sleep: Callable[[float], None] = time.sleep

    def __post_init__(self) -> None:
        if not self.providers:
            raise ValueError("FallbackSearchProvider requires at least one provider")

    def search(self, request: ResearchQuery | str) -> list[SearchHit]:
        reasons: list[str] = []
        last: SearchUnavailableError | None = None
        for index, provider in enumerate(self.providers):
            if index:
                self.sleep(self.handoff_seconds * (1 + 0.25 * self.jitter()))
            try:
                return provider.search(request)
            except SearchUnavailableError as exc:
                reasons.append(str(exc))
                last = exc
        raise SearchUnavailableError(
            "every discovery provider was unavailable: " + " | ".join(reasons)
        ) from last

    async def asearch(self, request: ResearchQuery | str) -> list[SearchHit]:
        return self.search(request)
