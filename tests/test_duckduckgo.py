import asyncio

import pytest
from ddgs.exceptions import DDGSException, RatelimitException, TimeoutException

from porter_forces_ai.adapters.duckduckgo import (
    DuckDuckGoSearchProvider,
    SearchUnavailableError,
    canonicalize_public_url,
)
from porter_forces_ai.domain import ResearchQuery
from porter_forces_ai.egress import EgressPolicy, EgressViolation

_EMPTY_SEARCH_HTML = """
<!doctype html>
<html><body><p class="no-results">No results.</p></body></html>
"""
_RESULT_SEARCH_HTML = """
<!doctype html>
<html><body>
  <div class="body">
    <h2>Public report</h2>
    <a href="https://example.org/public-report">Public discovery text</a>
  </div>
</body></html>
"""


class FakeHTTPResponse:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text


class FakeRawHTTPClient:
    def __init__(
        self,
        responses: list[FakeHTTPResponse] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.responses = list(responses or [])
        self.error = error
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def request(self, *args: object, **kwargs: object) -> FakeHTTPResponse:
        self.calls.append((args, kwargs))
        if self.error is not None:
            raise self.error
        if not self.responses:
            raise AssertionError("unexpected DuckDuckGo HTTP request")
        return self.responses.pop(0)


class FakeClient:
    def __init__(self) -> None:
        self.kwargs: dict[str, object] = {}

    def text(self, **kwargs: object) -> list[dict[str, str]]:
        self.kwargs = kwargs
        return [
            {
                "title": "Primary",
                "href": "HTTPS://Example.COM/report?utm_source=test&id=7#section",
                "body": "Result snippet",
            },
            {
                "title": "Duplicate",
                "href": "https://example.com/report?id=7",
                "body": "Same canonical source",
            },
        ]


def test_url_canonicalization_removes_tracking_and_fragments() -> None:
    assert (
        canonicalize_public_url("https://Example.com:443/a?utm_source=x&b=2&a=1#part")
        == "https://example.com/a?a=1&b=2"
    )


def test_url_canonicalization_preserves_public_ipv6_brackets() -> None:
    assert (
        canonicalize_public_url("https://[2606:4700:4700::1111]:8443/x")
        == "https://[2606:4700:4700::1111]:8443/x"
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/admin",
        "http://localhost./admin",
        "http://127.0.0.1/private",
        "http://127.1/private",
        "http://2130706433/private",
        "http://0x7f000001/private",
        "http://169.254.169.254/latest/meta-data",
        "https://user:password@example.com/report",
    ],
)
def test_url_canonicalization_rejects_local_or_credentialed_targets(url: str) -> None:
    with pytest.raises(ValueError):
        canonicalize_public_url(url)


def test_adapter_explicitly_uses_duckduckgo_and_deduplicates() -> None:
    client = FakeClient()
    provider = DuckDuckGoSearchProvider(client_factory=lambda **_: client)
    hits = provider.search(
        ResearchQuery(
            query_id="Q-1",
            query="banking AI competition official report",
            rationale="Test current external pressure",
        )
    )
    assert client.kwargs["backend"] == "duckduckgo"
    assert client.kwargs["query"] == "banking AI competition official report"
    assert len(hits) == 1
    assert hits[0].url == "https://example.com/report?id=7"
    assert hits[0].query_id == "Q-1"


def test_adapter_skips_an_unsafe_result_without_losing_safe_results() -> None:
    class MixedClient(FakeClient):
        def text(self, **kwargs: object) -> list[dict[str, str]]:
            self.kwargs = kwargs
            return [
                {
                    "title": "Unsafe",
                    "href": "http://localhost./admin",
                    "body": "Must be ignored",
                },
                {
                    "title": "Safe",
                    "href": "https://example.org/public-report",
                    "body": "Public discovery text",
                },
            ]

    client = MixedClient()
    provider = DuckDuckGoSearchProvider(client_factory=lambda **_: client)

    hits = provider.search("bank AI competition")

    assert [hit.url for hit in hits] == ["https://example.org/public-report"]


def test_adapter_retries_no_results_without_recency_on_same_backend() -> None:
    client = FakeRawHTTPClient(
        [
            FakeHTTPResponse(200, _EMPTY_SEARCH_HTML),
            FakeHTTPResponse(200, _RESULT_SEARCH_HTML),
        ]
    )
    request = ResearchQuery(
        query_id="Q-recency",
        query="generative AI adoption global banks 2024 2025 investment",
        rationale="Find recent competitive investment",
        recency="y",
    )
    provider = DuckDuckGoSearchProvider(client_factory=lambda **_: client)

    hits = provider.search(request)

    assert len(client.calls) == 2
    assert [call[0][:2] for call in client.calls] == [
        ("POST", "https://html.duckduckgo.com/html/"),
        ("POST", "https://html.duckduckgo.com/html/"),
    ]
    first_payload = client.calls[0][1]["data"]
    second_payload = client.calls[1][1]["data"]
    assert isinstance(first_payload, dict)
    assert isinstance(second_payload, dict)
    assert first_payload["df"] == "y"
    assert "df" not in second_payload
    assert first_payload["q"] == request.query
    assert second_payload["q"] == request.query
    assert request.recency is None
    assert [hit.query_id for hit in hits] == ["Q-recency"]


def test_adapter_returns_empty_when_unfiltered_retry_has_no_results() -> None:
    client = FakeRawHTTPClient(
        [
            FakeHTTPResponse(200, _EMPTY_SEARCH_HTML),
            FakeHTTPResponse(200, _EMPTY_SEARCH_HTML),
        ]
    )
    request = ResearchQuery(
        query_id="Q-empty-fallback",
        query="fintech acquires bank charter 2024 2025",
        rationale="Find current entrant evidence",
        recency="y",
    )
    provider = DuckDuckGoSearchProvider(client_factory=lambda **_: client)

    assert provider.search(request) == []
    assert len(client.calls) == 2
    assert request.recency is None


def test_adapter_returns_empty_for_unfiltered_no_results() -> None:
    client = FakeRawHTTPClient([FakeHTTPResponse(200, _EMPTY_SEARCH_HTML)])
    provider = DuckDuckGoSearchProvider(client_factory=lambda **_: client)

    assert provider.search("narrow public evidence query") == []
    assert len(client.calls) == 1


def test_ambiguous_ddgs_no_results_sentinel_fails_closed() -> None:
    sentinel = DDGSException("No results found.")

    class AmbiguousClient:
        def text(self, **_: object) -> list[dict[str, str]]:
            raise sentinel

    provider = DuckDuckGoSearchProvider(client_factory=lambda **_: AmbiguousClient())

    with pytest.raises(SearchUnavailableError) as raised:
        provider.search("narrow public evidence query")

    assert raised.value.__cause__ is sentinel


@pytest.mark.parametrize("status_code", [202, 403, 429, 500, 503])
def test_non_200_duckduckgo_responses_fail_closed_without_body_leak(
    status_code: int,
) -> None:
    sensitive_body = "challenge-content-that-must-not-escape"
    client = FakeRawHTTPClient([FakeHTTPResponse(status_code, sensitive_body)])
    request = ResearchQuery(
        query_id="Q-http-failure",
        query="banking AI competition official report",
        rationale="Find recent external pressure",
        recency="y",
    )
    provider = DuckDuckGoSearchProvider(client_factory=lambda **_: client)

    with pytest.raises(SearchUnavailableError) as raised:
        provider.search(request)

    assert len(client.calls) == 1
    assert request.recency == "y"
    rendered_error = f"{raised.value} {raised.value.__cause__}"
    assert str(status_code) in rendered_error
    assert sensitive_body not in rendered_error


def test_http_200_challenge_page_fails_closed_without_body_leak() -> None:
    sensitive_body = (
        '<html><form id="challenge-form">private challenge token</form></html>'
    )
    client = FakeRawHTTPClient([FakeHTTPResponse(200, sensitive_body)])
    provider = DuckDuckGoSearchProvider(client_factory=lambda **_: client)

    with pytest.raises(SearchUnavailableError) as raised:
        provider.search("banking AI competition official report")

    rendered_error = f"{raised.value} {raised.value.__cause__}"
    assert "challenge response" in rendered_error
    assert "private challenge token" not in rendered_error


@pytest.mark.parametrize(
    "unrecognized_html",
    [
        "<html><body><p>No results.</p></body></html>",
        (
            '<html><body><div class="new-result-layout">'
            '<a href="https://example.org/report">changed layout</a>'
            "</div></body></html>"
        ),
        '<html><body><div class="body"><h2>Maintenance</h2></div></body></html>',
    ],
)
def test_http_200_without_result_or_explicit_empty_marker_fails_closed(
    unrecognized_html: str,
) -> None:
    client = FakeRawHTTPClient([FakeHTTPResponse(200, unrecognized_html)])
    provider = DuckDuckGoSearchProvider(client_factory=lambda **_: client)

    with pytest.raises(SearchUnavailableError) as raised:
        provider.search("banking AI competition official report")

    rendered_error = f"{raised.value} {raised.value.__cause__}"
    assert "not a recognized results page" in rendered_error
    assert unrecognized_html not in rendered_error


def test_raw_transport_timeout_fails_closed() -> None:
    timeout = TimeoutException("timed out")
    client = FakeRawHTTPClient(error=timeout)
    provider = DuckDuckGoSearchProvider(client_factory=lambda **_: client)

    with pytest.raises(SearchUnavailableError) as raised:
        provider.search("banking AI competition official report")

    assert raised.value.__cause__ is timeout
    assert len(client.calls) == 1


def test_async_search_uses_the_same_status_aware_boundary() -> None:
    async def scenario() -> None:
        client = FakeRawHTTPClient([FakeHTTPResponse(403, "do-not-leak-this-body")])
        provider = DuckDuckGoSearchProvider(client_factory=lambda **_: client)

        with pytest.raises(SearchUnavailableError) as raised:
            await provider.asearch("banking AI competition official report")

        assert "do-not-leak-this-body" not in str(raised.value.__cause__)
        assert len(client.calls) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "provider_error",
    [TimeoutException("timed out"), RatelimitException("rate limited")],
)
def test_adapter_does_not_relax_recency_for_provider_outages(
    provider_error: Exception,
) -> None:
    class FailingClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def text(self, **kwargs: object) -> list[dict[str, str]]:
            self.calls.append(kwargs)
            raise provider_error

    client = FailingClient()
    request = ResearchQuery(
        query_id="Q-outage",
        query="banking AI competition official report",
        rationale="Find recent external pressure",
        recency="y",
    )
    provider = DuckDuckGoSearchProvider(client_factory=lambda **_: client)

    with pytest.raises(SearchUnavailableError) as raised:
        provider.search(request)

    assert raised.value.__cause__ is provider_error
    assert len(client.calls) == 1
    assert request.recency == "y"


def test_adapter_reports_unfiltered_retry_failure_without_rewriting_request() -> None:
    fallback_error = RatelimitException("rate limited")

    class FailingFallbackClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def text(self, **kwargs: object) -> list[dict[str, str]]:
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return []
            raise fallback_error

    client = FailingFallbackClient()
    request = ResearchQuery(
        query_id="Q-fallback",
        query="banking AI competition official report",
        rationale="Find recent external pressure",
        recency="y",
    )
    provider = DuckDuckGoSearchProvider(client_factory=lambda **_: client)

    with pytest.raises(SearchUnavailableError) as raised:
        provider.search(request)

    assert raised.value.__cause__ is fallback_error
    assert [call["timelimit"] for call in client.calls] == ["y", None]
    assert request.recency == "y"


def test_egress_policy_still_rejects_before_constructing_a_client() -> None:
    client_constructed = False

    def client_factory(**_: object) -> FakeClient:
        nonlocal client_constructed
        client_constructed = True
        return FakeClient()

    provider = DuckDuckGoSearchProvider(
        policy=EgressPolicy(forbidden_terms=("Project Cedar",)),
        client_factory=client_factory,
    )

    with pytest.raises(EgressViolation, match="run-confidential context"):
        provider.search("Project Cedar banking AI competition")

    assert client_constructed is False
