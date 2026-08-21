import pytest
from ddgs.exceptions import DDGSException, RatelimitException, TimeoutException

from porter_forces_ai.adapters.duckduckgo import (
    DuckDuckGoSearchProvider,
    SearchUnavailableError,
    canonicalize_public_url,
)
from porter_forces_ai.domain import ResearchQuery
from porter_forces_ai.egress import EgressPolicy, EgressViolation


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
    class FilterSensitiveClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def text(self, **kwargs: object) -> list[dict[str, str]]:
            self.calls.append(kwargs)
            if kwargs["timelimit"] == "y":
                raise DDGSException("No results found.")
            return [
                {
                    "title": "Public report",
                    "href": "https://example.org/public-report",
                    "body": "Public discovery text",
                }
            ]

    client = FilterSensitiveClient()
    request = ResearchQuery(
        query_id="Q-recency",
        query="generative AI adoption global banks 2024 2025 investment",
        rationale="Find recent competitive investment",
        recency="y",
    )
    provider = DuckDuckGoSearchProvider(client_factory=lambda **_: client)

    hits = provider.search(request)

    assert [call["timelimit"] for call in client.calls] == ["y", None]
    assert all(call["backend"] == "duckduckgo" for call in client.calls)
    assert all(call["query"] == request.query for call in client.calls)
    assert request.recency is None
    assert [hit.query_id for hit in hits] == ["Q-recency"]


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
                raise DDGSException("No results found.")
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
