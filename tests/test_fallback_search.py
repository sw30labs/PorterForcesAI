from __future__ import annotations

import pytest

from porter_forces_ai.adapters.duckduckgo import SearchUnavailableError
from porter_forces_ai.adapters.fallback_search import (
    BING,
    BRAVE,
    FALLBACK_ENGINES,
    FallbackSearchProvider,
    KeylessEngineSearchProvider,
)
from porter_forces_ai.domain import ResearchQuery, SearchHit
from porter_forces_ai.egress import EgressPolicy


class _Row:
    """Mirrors the ddgs TextResult shape: attributes, not mapping keys."""

    def __init__(self, title: str, href: str, body: str) -> None:
        self.title = title
        self.href = href
        self.body = body


class _FakeEngine:
    search_method = "GET"
    search_url = "https://engine.test/search"

    def __init__(self, *, status: int = 200, text: str = "<html/>", rows=None) -> None:
        self._status = status
        self._text = text
        self._rows = rows if rows is not None else []
        self.http_client = self

    def build_payload(self, **_: object) -> dict[str, str]:
        return {"q": "x"}

    def request(self, *_: object, **__: object) -> _FakeEngine:
        return self

    @property
    def status_code(self) -> int:
        return self._status

    @property
    def text(self) -> str:
        return self._text

    def extract_results(self, _: str) -> list[_Row]:
        return list(self._rows)

    def post_extract_results(self, rows: list[_Row]) -> list[_Row]:
        return rows


def _query() -> ResearchQuery:
    return ResearchQuery(
        query_id="Q-1", query="public bank ai evidence", rationale="test", recency=None
    )


def _provider(engine: _FakeEngine) -> KeylessEngineSearchProvider:
    return KeylessEngineSearchProvider(
        contract=BRAVE,
        policy=EgressPolicy(forbidden_terms=()),
        engine_factory=lambda **_: engine,
    )


def test_fallback_engine_returns_hits_tagged_with_its_own_provider() -> None:
    engine = _FakeEngine(
        rows=[_Row("Public report", "https://example.org/report", "A snippet.")]
    )

    hits = _provider(engine).search(_query())

    assert [hit.url for hit in hits] == ["https://example.org/report"]
    assert hits[0].provider == "brave"
    assert hits[0].query_id == "Q-1"


def test_fallback_engine_never_reports_emptiness_as_a_finding() -> None:
    # A captcha page served as HTTP 200 parses to zero rows. Mojeek did exactly
    # this on 2026-08-25. Treating that as "no results" would silently produce a
    # brief with no evidence behind it.
    engine = _FakeEngine(text="<html><title>Captcha</title></html>", rows=[])

    with pytest.raises(SearchUnavailableError) as raised:
        _provider(engine).search(_query())

    assert "cannot confirm genuine emptiness" in str(raised.value)


def test_fallback_engine_rejects_a_non_200_status() -> None:
    with pytest.raises(SearchUnavailableError) as raised:
        _provider(_FakeEngine(status=429)).search(_query())

    assert "HTTP status 429" in str(raised.value)


class _StubProvider:
    def __init__(self, *, hits: list[SearchHit] | None = None, error: str | None = None):
        self.hits = hits
        self.error = error
        self.calls = 0

    def search(self, request: ResearchQuery | str) -> list[SearchHit]:
        self.calls += 1
        if self.error is not None:
            raise SearchUnavailableError(self.error)
        return list(self.hits or [])

    async def asearch(self, request: ResearchQuery | str) -> list[SearchHit]:
        return self.search(request)


def _hit(provider: str) -> SearchHit:
    return SearchHit(
        query_id="Q-1",
        title="Public report",
        url="https://example.org/report",
        snippet="A snippet.",
        provider=provider,
    )


def test_chain_advances_only_when_a_provider_is_unavailable() -> None:
    primary = _StubProvider(error="duckduckgo search failed")
    secondary = _StubProvider(hits=[_hit("brave")])
    chain = FallbackSearchProvider(
        providers=[primary, secondary], jitter=lambda: 0.0, sleep=lambda _: None
    )

    hits = chain.search(_query())

    assert [hit.provider for hit in hits] == ["brave"]
    assert primary.calls == 1
    assert secondary.calls == 1


def test_a_verified_empty_primary_result_ends_the_chain() -> None:
    # DuckDuckGo is the only provider that can confirm genuine emptiness, so a
    # fallback must never be given the chance to overturn that finding.
    primary = _StubProvider(hits=[])
    secondary = _StubProvider(hits=[_hit("brave")])
    chain = FallbackSearchProvider(
        providers=[primary, secondary], jitter=lambda: 0.0, sleep=lambda _: None
    )

    assert chain.search(_query()) == []
    assert secondary.calls == 0


def test_chain_fails_closed_and_names_every_provider_that_refused() -> None:
    chain = FallbackSearchProvider(
        providers=[
            _StubProvider(error="duckduckgo search failed"),
            _StubProvider(error="brave returned HTTP status 429"),
        ],
        jitter=lambda: 0.0,
        sleep=lambda _: None,
    )

    with pytest.raises(SearchUnavailableError) as raised:
        chain.search(_query())

    message = str(raised.value)
    assert "every discovery provider was unavailable" in message
    assert "duckduckgo" in message
    assert "brave" in message


def test_configured_fallback_names_resolve_to_engine_contracts() -> None:
    assert FALLBACK_ENGINES["brave"] is BRAVE
    assert FALLBACK_ENGINES["bing"] is BING
    # Yandex parses but is deliberately absent from a US/EU banking profile.
    assert "yandex" not in FALLBACK_ENGINES


def test_max_results_truncates_hits() -> None:
    rows = [_Row(f"Title {i}", f"https://example.org/{i}", f"Snippet {i}.") for i in range(20)]
    provider = KeylessEngineSearchProvider(
        contract=BRAVE,
        policy=EgressPolicy(forbidden_terms=()),
        max_results=3,
        engine_factory=lambda **_: _FakeEngine(rows=rows),
    )
    hits = provider.search(_query())
    assert len(hits) == 3
    assert hits[0].url == "https://example.org/0"
    assert hits[2].url == "https://example.org/2"


def test_max_results_zero_returns_all() -> None:
    rows = [_Row(f"Title {i}", f"https://example.org/{i}", f"Snippet {i}.") for i in range(5)]
    provider = KeylessEngineSearchProvider(
        contract=BRAVE,
        policy=EgressPolicy(forbidden_terms=()),
        max_results=0,
        engine_factory=lambda **_: _FakeEngine(rows=rows),
    )
    hits = provider.search(_query())
    assert len(hits) == 5


def test_max_results_never_exceeds_cap() -> None:
    rows = [_Row(f"Title {i}", f"https://example.org/{i}", f"Snippet {i}.") for i in range(100)]
    provider = KeylessEngineSearchProvider(
        contract=BRAVE,
        policy=EgressPolicy(forbidden_terms=()),
        max_results=8,
        engine_factory=lambda **_: _FakeEngine(rows=rows),
    )
    hits = provider.search(_query())
    assert len(hits) == 8
