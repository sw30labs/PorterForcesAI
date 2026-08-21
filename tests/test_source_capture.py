from __future__ import annotations

import hashlib
import ssl
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import httpcore
import httpx
import pytest

from porter_forces_ai.domain import (
    ForceName,
    ResearchQuery,
    SearchHit,
    SourceClass,
)
from porter_forces_ai.source_capture import (
    CapturePolicy,
    DiscoveryLedger,
    DiscoveryLedgerError,
    SafeSourceCapture,
    SourceFetchError,
    SourceLimitError,
    SourceNotRegisteredError,
    UnsafeSourceURLError,
    UnsupportedSourceTypeError,
    promote_capture_to_evidence,
)


def _query() -> ResearchQuery:
    return ResearchQuery(
        query_id="Q-rivalry-1",
        force=ForceName.RIVALRY,
        query="bank AI investment audited filings",
        rationale="Find public evidence of competitor investment",
        preferred_source_classes=[SourceClass.AUDITED_FILING],
    )


def _registered_source(
    url: str = "https://example.org/report?utm_source=test&a=1#section",
) -> tuple[DiscoveryLedger, str]:
    ledger = DiscoveryLedger()
    _, hits = ledger.record_execution(
        _query(),
        [
            SearchHit(
                query_id="Q-rivalry-1",
                title="Annual report",
                url=url,
                snippet="Discovery metadata only",
                provider="duckduckgo",
                retrieved_at=datetime(2026, 8, 20, tzinfo=UTC),
            )
        ],
        provider="duckduckgo",
        executed_at=datetime(2026, 8, 20, tzinfo=UTC),
    )
    return ledger, hits[0].source_id


def _public_resolver(host: str, port: int) -> tuple[str, ...]:
    assert host
    assert port in {80, 443}
    return ("93.184.216.34",)


class _ScriptedNetworkStream(httpcore.NetworkStream):
    def __init__(self) -> None:
        self._response = bytearray(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/plain\r\n"
            b"Content-Length: 15\r\n"
            b"Connection: close\r\n\r\n"
            b"Pinned response"
        )
        self.writes: list[bytes] = []
        self.tls_server_names: list[str | None] = []
        self.tls_contexts: list[ssl.SSLContext] = []

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        del timeout
        chunk = bytes(self._response[:max_bytes])
        del self._response[:max_bytes]
        return chunk

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        del timeout
        self.writes.append(buffer)

    def close(self) -> None:
        return None

    def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.NetworkStream:
        del timeout
        self.tls_contexts.append(ssl_context)
        self.tls_server_names.append(server_hostname)
        return self


def test_discovery_ledger_mints_immutable_content_addressed_records() -> None:
    ledger, source_id = _registered_source()

    execution = ledger.executions[0]
    hit = ledger.require_hit(source_id)

    assert execution.query_id == "Q-rivalry-1"
    assert execution.query_sha256 != ""
    assert hit.url == "https://example.org/report?a=1"
    assert hit.source_id.startswith("S-")
    assert hit.search_hit_sha256 != ""
    assert isinstance(ledger.executions, tuple)
    with pytest.raises(FrozenInstanceError):
        hit.title = "rewritten"  # type: ignore[misc]


def test_ledger_rejects_mismatched_query_provider_and_duplicates() -> None:
    ledger = DiscoveryLedger()
    wrong_query_hit = SearchHit(
        query_id="Q-other",
        title="Result",
        url="https://example.org/a",
        provider="duckduckgo",
    )
    with pytest.raises(DiscoveryLedgerError, match="query_id"):
        ledger.record_execution(_query(), [wrong_query_hit], provider="duckduckgo")

    wrong_provider_hit = wrong_query_hit.model_copy(
        update={"query_id": "Q-rivalry-1", "provider": "other"}
    )
    with pytest.raises(DiscoveryLedgerError, match="provider"):
        ledger.record_execution(_query(), [wrong_provider_hit], provider="duckduckgo")

    duplicate_hits = [
        SearchHit(title="First", url="https://example.org/a?utm_source=x"),
        SearchHit(title="Second", url="https://example.org/a"),
    ]
    with pytest.raises(DiscoveryLedgerError, match="duplicate canonical"):
        ledger.record_execution(_query(), duplicate_hits, provider="duckduckgo")
    assert ledger.executions == ()


def test_capture_requires_a_ledger_registered_source_id() -> None:
    capture = SafeSourceCapture(
        DiscoveryLedger(),
        resolver=_public_resolver,
        client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))),
    )
    with pytest.raises(SourceNotRegisteredError):
        capture.capture("S-user-supplied")


def test_html_capture_is_bounded_hashed_clean_and_untrusted() -> None:
    ledger, source_id = _registered_source()
    body = (
        b"<html><head><style>.hidden{}</style><script>ignore()</script></head>"
        b"<body><h1>AI &amp; banking</h1><p>Material public fact.</p>"
        b"<svg><text>hidden</text></svg></body></html>"
    )
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=body,
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    service = SafeSourceCapture(ledger, resolver=_public_resolver, client=client)
    captured = service.capture(source_id)

    assert requested_urls == ["https://example.org/report?a=1"]
    assert captured.content_sha256 == hashlib.sha256(body).hexdigest()
    assert captured.byte_length == len(body)
    assert captured.extracted_text == "AI & banking\nMaterial public fact."
    assert "ignore" not in captured.extracted_text
    assert "hidden" not in captured.extracted_text
    assert captured.trust_classification == "untrusted_external_content"
    assert captured.capture_id.startswith("CAP-")

    evidence = promote_capture_to_evidence(
        captured,
        source_class=SourceClass.AUDITED_FILING,
        publisher="Example Bank",
        quality_score=0.9,
        freshness_score=0.8,
        applicability_score=0.7,
    )
    assert evidence.evidence_id.startswith("E-")
    assert evidence.content_sha256 == captured.content_sha256
    assert evidence.source_url == captured.final_url
    assert evidence.publisher == "Example Bank"
    assert "untrusted_external_content" in (evidence.notes or "")

    selected = promote_capture_to_evidence(
        captured,
        source_class=SourceClass.AUDITED_FILING,
        excerpt="Material public fact.",
    )
    assert selected.excerpt == "Material public fact."
    with pytest.raises(ValueError, match="exact passage"):
        promote_capture_to_evidence(
            captured,
            source_class=SourceClass.AUDITED_FILING,
            excerpt="A statement absent from the captured page.",
        )


def test_plain_text_capture_normalizes_control_characters() -> None:
    ledger, source_id = _registered_source()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain; charset=utf-8"},
            content=b" First\x00 line\r\n\r\nSecond   line ",
            request=request,
        )

    service = SafeSourceCapture(
        ledger,
        resolver=_public_resolver,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert service.capture(source_id).extracted_text == "First line\nSecond line"


def test_every_redirect_is_revalidated_before_the_next_request() -> None:
    ledger, source_id = _registered_source("https://example.org/start")
    requested_hosts: list[str] = []

    def resolver(host: str, port: int) -> tuple[str, ...]:
        del port
        if host == "private.example":
            return ("10.0.0.8",)
        return ("93.184.216.34",)

    def handler(request: httpx.Request) -> httpx.Response:
        requested_hosts.append(request.url.host)
        return httpx.Response(
            302,
            headers={"location": "https://private.example/admin"},
            request=request,
        )

    service = SafeSourceCapture(
        ledger,
        resolver=resolver,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(UnsafeSourceURLError, match="private, local"):
        service.capture(source_id)
    assert requested_hosts == ["example.org"]


def test_initial_dns_must_resolve_only_to_global_addresses() -> None:
    ledger, source_id = _registered_source()
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, request=request)

    service = SafeSourceCapture(
        ledger,
        resolver=lambda host, port: ("93.184.216.34", "169.254.169.254"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(UnsafeSourceURLError):
        service.capture(source_id)
    assert requests == 0


def test_default_transport_pins_validated_ip_and_preserves_tls_hostname(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger, source_id = _registered_source()
    resolver_calls: list[tuple[str, int]] = []
    socket_targets: list[tuple[str, int]] = []
    stream = _ScriptedNetworkStream()

    def resolver(host: str, port: int) -> tuple[str, ...]:
        resolver_calls.append((host, port))
        return ("93.184.216.34", "2606:4700:4700::1111")

    def connect_tcp(
        backend: httpcore.SyncBackend,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: object = None,
    ) -> httpcore.NetworkStream:
        del backend, timeout, local_address, socket_options
        socket_targets.append((host, port))
        return stream

    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", connect_tcp)

    with SafeSourceCapture(ledger, resolver=resolver) as service:
        captured = service.capture(source_id)

    assert captured.extracted_text == "Pinned response"
    assert resolver_calls == [("example.org", 443), ("example.org", 443)]
    assert socket_targets == [("93.184.216.34", 443)]
    assert stream.tls_server_names == ["example.org"]
    assert len(stream.tls_contexts) == 1
    assert b"host: example.org\r\n" in b"".join(stream.writes).lower()


def test_default_transport_rejects_private_dns_rebinding_before_socket_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger, source_id = _registered_source()
    resolver_calls = 0
    socket_targets: list[str] = []

    def rebinding_resolver(host: str, port: int) -> tuple[str, ...]:
        nonlocal resolver_calls
        assert host == "example.org"
        assert port == 443
        resolver_calls += 1
        if resolver_calls == 1:
            return ("93.184.216.34",)
        return ("93.184.216.34", "169.254.169.254")

    def connect_tcp(
        backend: httpcore.SyncBackend,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: object = None,
    ) -> httpcore.NetworkStream:
        del backend, port, timeout, local_address, socket_options
        socket_targets.append(host)
        raise AssertionError("socket connection must not be attempted")

    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", connect_tcp)

    with (
        SafeSourceCapture(ledger, resolver=rebinding_resolver) as service,
        pytest.raises(UnsafeSourceURLError, match="private, local"),
    ):
        service.capture(source_id)

    assert resolver_calls == 2
    assert socket_targets == []


def test_capture_rejects_non_web_ports_before_dns_or_http() -> None:
    ledger, source_id = _registered_source("https://example.org:8443/report")
    dns_calls = 0
    requests = 0

    def resolver(host: str, port: int) -> tuple[str, ...]:
        del host, port
        nonlocal dns_calls
        dns_calls += 1
        return ("93.184.216.34",)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, request=request)

    service = SafeSourceCapture(
        ledger,
        resolver=resolver,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(UnsafeSourceURLError, match="disallowed network port"):
        service.capture(source_id)
    assert dns_calls == 0
    assert requests == 0


def test_safe_redirect_is_manually_followed_and_canonicalized() -> None:
    ledger, source_id = _registered_source("https://example.org/start")
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if request.url.path == "/start":
            return httpx.Response(
                302,
                headers={"location": "/final?utm_medium=web&a=1#fragment"},
                request=request,
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            content=b"Final public document",
            request=request,
        )

    service = SafeSourceCapture(
        ledger,
        resolver=_public_resolver,
        client=httpx.Client(
            transport=httpx.MockTransport(handler),
            follow_redirects=True,
        ),
    )
    captured = service.capture(source_id)
    assert requested == [
        "https://example.org/start",
        "https://example.org/final?a=1",
    ]
    assert captured.final_url == "https://example.org/final?a=1"


def test_https_downgrade_redirect_is_rejected() -> None:
    ledger, source_id = _registered_source("https://example.org/start")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"location": "http://example.org/plain"},
            request=request,
        )

    service = SafeSourceCapture(
        ledger,
        resolver=_public_resolver,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(UnsafeSourceURLError, match="HTTPS-to-HTTP"):
        service.capture(source_id)


@pytest.mark.parametrize("declared", [True, False])
def test_capture_enforces_content_size_with_or_without_length(declared: bool) -> None:
    ledger, source_id = _registered_source()

    def handler(request: httpx.Request) -> httpx.Response:
        headers = {"content-type": "text/plain"}
        if declared:
            headers["content-length"] = "20"
        return httpx.Response(200, headers=headers, content=b"0123456789", request=request)

    service = SafeSourceCapture(
        ledger,
        policy=CapturePolicy(max_content_bytes=5),
        resolver=_public_resolver,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(SourceLimitError, match="content-size"):
        service.capture(source_id)


def test_capture_rejects_unsupported_media_type_and_http_errors() -> None:
    ledger, source_id = _registered_source()
    media_service = SafeSourceCapture(
        ledger,
        resolver=_public_resolver,
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"content-type": "application/pdf"},
                    content=b"pdf",
                    request=request,
                )
            )
        ),
    )
    with pytest.raises(UnsupportedSourceTypeError):
        media_service.capture(source_id)

    error_service = SafeSourceCapture(
        ledger,
        resolver=_public_resolver,
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(503, request=request)
            )
        ),
    )
    with pytest.raises(SourceFetchError, match="503"):
        error_service.capture(source_id)


def test_redirect_limit_and_transport_timeout_fail_closed() -> None:
    ledger, source_id = _registered_source("https://example.org/start")

    redirect_service = SafeSourceCapture(
        ledger,
        policy=CapturePolicy(max_redirects=0),
        resolver=_public_resolver,
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    302,
                    headers={"location": "/next"},
                    request=request,
                )
            )
        ),
    )
    with pytest.raises(SourceLimitError, match="redirect limit"):
        redirect_service.capture(source_id)

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    timeout_service = SafeSourceCapture(
        ledger,
        resolver=_public_resolver,
        client=httpx.Client(transport=httpx.MockTransport(timeout_handler)),
    )
    with pytest.raises(SourceFetchError, match="request failed"):
        timeout_service.capture(source_id)


def test_extracted_text_limit_is_recorded_and_evidence_cannot_be_a_snippet() -> None:
    ledger, source_id = _registered_source()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            content=b"A long public document",
            request=request,
        )

    service = SafeSourceCapture(
        ledger,
        policy=CapturePolicy(max_extracted_characters=6),
        resolver=_public_resolver,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    captured = service.capture(source_id)
    assert captured.text_truncated is True
    assert captured.extracted_text == "A long"
    with pytest.raises(ValueError, match="source_class"):
        promote_capture_to_evidence(captured, source_class=SourceClass.SEARCH_SNIPPET)
