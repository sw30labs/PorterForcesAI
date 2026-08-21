"""DuckDuckGo discovery adapter backed by the third-party ``ddgs`` package."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from ipaddress import ip_address
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from porter_forces_ai.domain import ResearchQuery, SearchHit
from porter_forces_ai.egress import EgressPolicy


class SearchUnavailableError(RuntimeError):
    """DuckDuckGo failed or rate-limited; callers decide whether to retry."""


_TRACKING_PARAMETERS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "source",
    "utm_campaign",
    "utm_content",
    "utm_medium",
    "utm_source",
    "utm_term",
}


def _is_no_results_error(exc: Exception) -> bool:
    """Recognize DDGS' empty-result sentinel without masking real outages."""

    from ddgs.exceptions import DDGSException, RatelimitException, TimeoutException

    message = str(exc).strip().rstrip(".").casefold()
    return (
        isinstance(exc, DDGSException)
        and not isinstance(exc, (RatelimitException, TimeoutException))
        and message == "no results found"
    )


def canonicalize_public_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("search result URL must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ValueError("search result URL must not contain credentials")
    host = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("search result URL must not target a local host")
    if (host.replace(".", "").isdigit() or host.startswith("0x")) and ":" not in host:
        try:
            address = ip_address(int(host, 0)) if "." not in host else ip_address(host)
        except ValueError as exc:
            raise ValueError("search result URL uses a non-standard numeric host") from exc
    else:
        try:
            address = ip_address(host)
        except ValueError:
            address = None
    if address is not None and not address.is_global:
        raise ValueError("search result URL must not target a private or non-global IP")
    port = parsed.port
    display_host = f"[{host}]" if address is not None and address.version == 6 else host
    netloc = display_host
    is_default_port = (parsed.scheme == "http" and port == 80) or (
        parsed.scheme == "https" and port == 443
    )
    if port and not is_default_port:
        netloc = f"{display_host}:{port}"
    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if key.casefold() not in _TRACKING_PARAMETERS
        )
    )
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path or "/", query, ""))


@dataclass(slots=True)
class DuckDuckGoSearchProvider:
    """Search provider that stays on DuckDuckGo and records recency relaxation."""

    policy: EgressPolicy = field(default_factory=EgressPolicy)
    region: str = "us-en"
    timeout_seconds: int = 15
    max_results: int = 8
    client_factory: Callable[..., Any] | None = None

    def _new_client(self) -> Any:
        if self.client_factory is not None:
            return self.client_factory(timeout=self.timeout_seconds)
        from ddgs import DDGS

        return DDGS(timeout=self.timeout_seconds)

    def _text(self, client: Any, *, query: str, recency: str | None) -> Any:
        """Execute one request against the explicitly selected DuckDuckGo backend."""

        return client.text(
            query=query,
            region=self.region,
            safesearch="moderate",
            timelimit=recency,
            max_results=self.max_results,
            backend="duckduckgo",
        )

    def search(self, request: ResearchQuery | str) -> list[SearchHit]:
        if isinstance(request, ResearchQuery):
            query_id = request.query_id
            query = request.query
            recency = request.recency
        else:
            query_id = None
            query = request
            recency = None
        safe_query = self.policy.validate(query)
        client = self._new_client()
        relaxed_recency = False
        try:
            rows = self._text(client, query=safe_query, recency=recency)
        except Exception as exc:  # provider exceptions are deliberately hidden at the port
            if recency is None or not _is_no_results_error(exc):
                raise SearchUnavailableError("DuckDuckGo search failed") from exc
            relaxed_recency = True
        else:
            # DDGS currently raises for this case, but alternate client versions may return
            # an empty list. Treat both representations of an empty filtered result alike.
            relaxed_recency = recency is not None and not rows

        if relaxed_recency:
            try:
                rows = self._text(client, query=safe_query, recency=None)
            except Exception as exc:  # provider details remain behind the adapter boundary
                raise SearchUnavailableError("DuckDuckGo search failed") from exc
            if isinstance(request, ResearchQuery):
                # RecordingSearchProvider observes the request after this call. Mutating only
                # the effective filter keeps the persisted discovery record truthful: the
                # successful request was unbounded, while its query text and ID are unchanged.
                request.recency = None

        retrieved_at = datetime.now(UTC)
        hits: list[SearchHit] = []
        seen: set[str] = set()
        for row in rows:
            raw_url = str(row.get("href") or row.get("url") or "")
            try:
                url = canonicalize_public_url(raw_url)
                hit = SearchHit(
                    query_id=query_id,
                    title=str(row.get("title") or url)[:1_000],
                    url=url,
                    snippet=str(row.get("body") or row.get("snippet") or "")[:4_000],
                    provider="duckduckgo",
                    retrieved_at=retrieved_at,
                )
            except (TypeError, ValueError):
                continue
            if url in seen:
                continue
            seen.add(url)
            hits.append(hit)
        return hits

    async def asearch(self, request: ResearchQuery | str) -> list[SearchHit]:
        return await asyncio.to_thread(self.search, request)
