"""DuckDuckGo discovery adapter backed by the third-party ``ddgs`` package."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from ipaddress import ip_address
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from porter_forces_ai.domain import ResearchQuery, SearchHit
from porter_forces_ai.egress import EgressPolicy


class SearchUnavailableError(RuntimeError):
    """DuckDuckGo failed or rate-limited; callers decide whether to retry."""


class _DuckDuckGoTransportError(RuntimeError):
    """Status-aware backend failure that never includes response content."""


class _DuckDuckGoProtocolError(RuntimeError):
    """DuckDuckGo returned a response that cannot establish a valid search result."""


_TRACKING_PARAMETERS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "utm_campaign",
    "utm_content",
    "utm_medium",
    "utm_source",
    "utm_term",
}

_CHALLENGE_MARKERS = (
    "anomaly-modal",
    "challenge-form",
    "duckduckgo.com/anomaly.js",
    "bots use duckduckgo too",
)
_NO_RESULTS_XPATH = (
    "//*[contains(concat(' ', normalize-space(@class), ' '), ' no-results ')]"
    " | //*[contains(concat(' ', normalize-space(@class), ' '), "
    "' result--no-result ')]"
)


class _StatusAwareDuckDuckGoClient:
    """Run the installed DuckDuckGo engine without discarding HTTP status."""

    def __init__(self, *, timeout: int, http_client: Any | None = None) -> None:
        from ddgs.engines.duckduckgo import Duckduckgo

        self._engine = Duckduckgo(timeout=timeout)
        required_members = (
            "build_payload",
            "extract_results",
            "post_extract_results",
            "pre_process_html",
            "extract_tree",
            "search_method",
            "search_url",
            "http_client",
        )
        if any(not hasattr(self._engine, member) for member in required_members):
            existing_http_client = getattr(self._engine, "http_client", None)
            if existing_http_client is not None:
                self._close_http_client(existing_http_client)
            raise _DuckDuckGoProtocolError(
                "installed DuckDuckGo backend is incompatible with the status-aware adapter"
            )
        self._owns_http_client = http_client is None
        if http_client is not None:
            # The engine constructor creates its own client. Close that unused
            # client before installing the deterministic/test transport.
            self._close_http_client(self._engine.http_client)
            self._engine.http_client = http_client

    @staticmethod
    def _close_http_client(http_client: Any) -> None:
        nested_client = getattr(http_client, "client", None)
        close = getattr(nested_client, "close", None)
        if callable(close):
            # Cleanup must not replace the status-aware provider outcome or
            # expose transport-specific exception text.
            with suppress(Exception):
                close()

    def close(self) -> None:
        if self._owns_http_client:
            self._close_http_client(self._engine.http_client)

    def text(
        self,
        *,
        query: str,
        region: str,
        safesearch: str,
        timelimit: str | None,
        max_results: int,
        backend: str,
    ) -> list[dict[str, str]]:
        """Return parsed rows only after observing a genuine HTTP 200 response."""

        if backend != "duckduckgo":
            raise _DuckDuckGoProtocolError("only the DuckDuckGo backend is permitted")
        payload = self._engine.build_payload(
            query=query,
            region=region,
            safesearch=safesearch,
            timelimit=timelimit,
            page=1,
        )
        request_values = (
            {"params": payload}
            if self._engine.search_method == "GET"
            else {"data": payload}
        )
        response = self._engine.http_client.request(
            self._engine.search_method,
            self._engine.search_url,
            **request_values,
        )
        status_code = getattr(response, "status_code", None)
        if not isinstance(status_code, int):
            raise _DuckDuckGoProtocolError("DuckDuckGo response omitted its HTTP status")
        if status_code != 200:
            raise _DuckDuckGoTransportError(
                f"DuckDuckGo returned HTTP status {status_code}"
            )
        html_text = getattr(response, "text", None)
        if not isinstance(html_text, str) or not html_text.strip():
            raise _DuckDuckGoProtocolError(
                "DuckDuckGo returned a blank HTTP 200 response"
            )
        normalized_html = html_text.casefold()
        if any(marker in normalized_html for marker in _CHALLENGE_MARKERS):
            raise _DuckDuckGoTransportError("DuckDuckGo returned a challenge response")
        try:
            results = self._engine.post_extract_results(
                self._engine.extract_results(html_text)
            )
            tree = self._engine.extract_tree(
                self._engine.pre_process_html(html_text)
            )
        except Exception:
            # Parser errors may quote source material. Keep response content out
            # of both the public error and its exception chain.
            raise _DuckDuckGoProtocolError(
                "DuckDuckGo response could not be parsed"
            ) from None
        usable_results = [result for result in results if str(result.href).strip()]
        if not usable_results and not tree.xpath(_NO_RESULTS_XPATH):
            raise _DuckDuckGoProtocolError(
                "DuckDuckGo HTTP 200 page was not a recognized results page"
            )
        return [
            {
                "title": str(result.title),
                "href": str(result.href),
                "body": str(result.body),
            }
            for result in usable_results[:max_results]
        ]


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
    """Status-aware DuckDuckGo-only discovery with truthful recency relaxation."""

    policy: EgressPolicy = field(default_factory=EgressPolicy)
    region: str = "us-en"
    timeout_seconds: int = 15
    max_results: int = 8
    client_factory: Callable[..., Any] | None = None

    def _new_client(self) -> Any:
        if self.client_factory is None:
            return _StatusAwareDuckDuckGoClient(timeout=self.timeout_seconds)
        candidate = self.client_factory(timeout=self.timeout_seconds)
        if callable(getattr(candidate, "text", None)):
            # A high-level injected client is an explicit test seam. Empty lists
            # represent a verified empty result; ambiguous DDGS exceptions do not.
            return candidate
        if callable(getattr(candidate, "request", None)):
            return _StatusAwareDuckDuckGoClient(
                timeout=self.timeout_seconds,
                http_client=candidate,
            )
        raise TypeError("DuckDuckGo client_factory returned an unsupported client")

    def _text(
        self,
        client: Any,
        *,
        query: str,
        recency: str | None,
    ) -> list[Mapping[str, Any]]:
        """Execute one request against the explicitly selected DuckDuckGo backend."""

        rows = client.text(
            query=query,
            region=self.region,
            safesearch="moderate",
            timelimit=recency,
            max_results=self.max_results,
            backend="duckduckgo",
        )
        if not isinstance(rows, list) or any(
            not isinstance(row, Mapping) for row in rows
        ):
            raise _DuckDuckGoProtocolError(
                "DuckDuckGo returned an invalid result collection"
            )
        return rows

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
        try:
            client = self._new_client()
        except Exception as exc:
            raise SearchUnavailableError("DuckDuckGo search failed") from exc
        try:
            rows = self._text(client, query=safe_query, recency=recency)
            if recency is not None and not rows:
                # Only the status-aware backend or an explicit injected empty
                # result reaches this branch. Provider errors never relax recency.
                rows = self._text(client, query=safe_query, recency=None)
                if isinstance(request, ResearchQuery):
                    # RecordingSearchProvider observes the request after this call.
                    # Persist the filter that actually produced the final result set.
                    request.recency = None
        except Exception as exc:  # provider details remain behind the adapter boundary
            raise SearchUnavailableError("DuckDuckGo search failed") from exc
        finally:
            if isinstance(client, _StatusAwareDuckDuckGoClient):
                client.close()

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
