import pytest

from porter_forces_ai.adapters.duckduckgo import (
    DuckDuckGoSearchProvider,
    canonicalize_public_url,
)
from porter_forces_ai.domain import ResearchQuery


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
