"""Append-only discovery records and SSRF-resistant public source capture.

Search-result snippets are discovery metadata, never evidence.  A URL becomes
eligible for capture only after a search execution registers it in a
``DiscoveryLedger``.  The capture boundary then validates DNS on every hop,
downloads a bounded representation without following redirects automatically,
and labels all extracted text as untrusted external content.
"""

from __future__ import annotations

import hashlib
import json
import re
import socket
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from html import unescape
from html.parser import HTMLParser
from ipaddress import ip_address
from threading import RLock
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

import httpx

from porter_forces_ai.adapters.duckduckgo import canonicalize_public_url
from porter_forces_ai.domain import (
    EvidenceItem,
    EvidenceOrigin,
    ResearchQuery,
    SearchHit,
    SourceClass,
)

UNTRUSTED_EXTERNAL_CONTENT = "untrusted_external_content"


class DiscoveryLedgerError(ValueError):
    """A search execution could not be registered without ambiguity."""


class SourceCaptureError(RuntimeError):
    """Base error for bounded source retrieval and extraction."""


class SourceNotRegisteredError(SourceCaptureError):
    """The requested source ID is absent from the executed-search ledger."""


class UnsafeSourceURLError(SourceCaptureError):
    """A source target could reach a local, private, or otherwise unsafe address."""


class SourceFetchError(SourceCaptureError):
    """An allowed remote source failed to return a successful response."""


class SourceLimitError(SourceCaptureError):
    """A response exceeded a configured redirect or content-size bound."""


class UnsupportedSourceTypeError(SourceCaptureError):
    """A response is not a supported textual media type."""


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DiscoveryLedgerError("ledger timestamps must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class ExecutedQueryRecord:
    """Immutable record of the exact query sent to a named search provider."""

    execution_id: str
    query_id: str
    force: str | None
    query_text: str
    rationale: str
    preferred_source_classes: tuple[str, ...]
    recency: str | None
    provider: str
    executed_at: datetime
    query_sha256: str


@dataclass(frozen=True, slots=True)
class RegisteredSearchHit:
    """Immutable discovery record whose source ID is minted by the ledger."""

    source_id: str
    execution_id: str
    query_id: str
    rank: int
    title: str
    url: str
    snippet: str
    provider: str
    retrieved_at: datetime
    search_hit_sha256: str


class DiscoveryLedger:
    """Thread-safe, append-only in-memory ledger for executed searches.

    This object is the capability boundary for source capture: callers give the
    capture service a minted ``source_id``, not an arbitrary URL. Persistent
    deployments store the same immutable records in ``SQLiteRunRepository``.
    """

    def __init__(self) -> None:
        self._executions: dict[str, ExecutedQueryRecord] = {}
        self._hits: dict[str, RegisteredSearchHit] = {}
        self._lock = RLock()

    @property
    def executions(self) -> tuple[ExecutedQueryRecord, ...]:
        with self._lock:
            return tuple(self._executions.values())

    @property
    def hits(self) -> tuple[RegisteredSearchHit, ...]:
        with self._lock:
            return tuple(self._hits.values())

    def record_execution(
        self,
        query: ResearchQuery,
        hits: Sequence[SearchHit],
        *,
        provider: str,
        executed_at: datetime | None = None,
    ) -> tuple[ExecutedQueryRecord, tuple[RegisteredSearchHit, ...]]:
        """Atomically append one query execution and its ordered result set."""

        provider = provider.strip()
        if not provider:
            raise DiscoveryLedgerError("provider must not be blank")
        timestamp = _aware_utc(executed_at or datetime.now(UTC))
        execution_id = f"QE-{uuid4().hex}"
        query_payload = query.model_dump(mode="json")
        query_record = ExecutedQueryRecord(
            execution_id=execution_id,
            query_id=query.query_id,
            force=query.force.value if query.force is not None else None,
            query_text=query.query,
            rationale=query.rationale,
            preferred_source_classes=tuple(
                source_class.value for source_class in query.preferred_source_classes
            ),
            recency=query.recency,
            provider=provider,
            executed_at=timestamp,
            query_sha256=_digest(query_payload),
        )

        records: list[RegisteredSearchHit] = []
        seen_urls: set[str] = set()
        for rank, hit in enumerate(hits, start=1):
            if hit.query_id not in {None, query.query_id}:
                raise DiscoveryLedgerError(
                    "a search hit query_id does not match the executed query"
                )
            if hit.provider.casefold() != provider.casefold():
                raise DiscoveryLedgerError(
                    "a search hit provider does not match the executed provider"
                )
            canonical_url = canonicalize_public_url(hit.url)
            if canonical_url in seen_urls:
                raise DiscoveryLedgerError(
                    "duplicate canonical URLs are not allowed within one execution"
                )
            seen_urls.add(canonical_url)
            retrieved_at = _aware_utc(hit.retrieved_at)
            hit_payload = {
                "execution_id": execution_id,
                "query_id": query.query_id,
                "rank": rank,
                "title": hit.title,
                "url": canonical_url,
                "snippet": hit.snippet,
                "provider": provider,
                "retrieved_at": retrieved_at.isoformat(),
            }
            hit_hash = _digest(hit_payload)
            records.append(
                RegisteredSearchHit(
                    source_id=f"S-{hashlib.sha256(f'{execution_id}:{rank}:{canonical_url}'.encode()).hexdigest()[:32]}",
                    execution_id=execution_id,
                    query_id=query.query_id,
                    rank=rank,
                    title=hit.title,
                    url=canonical_url,
                    snippet=hit.snippet,
                    provider=provider,
                    retrieved_at=retrieved_at,
                    search_hit_sha256=hit_hash,
                )
            )

        with self._lock:
            if execution_id in self._executions:
                raise DiscoveryLedgerError("execution identifier collision")
            if any(record.source_id in self._hits for record in records):
                raise DiscoveryLedgerError("source identifier collision")
            self._executions[execution_id] = query_record
            self._hits.update((record.source_id, record) for record in records)
        return query_record, tuple(records)

    def require_hit(self, source_id: str) -> RegisteredSearchHit:
        """Resolve a ledger-minted source ID or fail closed."""

        with self._lock:
            try:
                return self._hits[source_id]
            except KeyError as exc:
                raise SourceNotRegisteredError(
                    "source_id is absent from the executed-search ledger"
                ) from exc


Resolver = Callable[[str, int], Iterable[str]]


def system_resolver(host: str, port: int) -> tuple[str, ...]:
    """Resolve every address for a host using the operating-system resolver."""

    try:
        rows = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise UnsafeSourceURLError("source hostname could not be resolved") from exc
    return tuple(sorted({str(row[4][0]) for row in rows}))


@dataclass(frozen=True, slots=True)
class CapturePolicy:
    """Hard limits applied before content is allowed into the evidence pipeline."""

    max_content_bytes: int = 2_000_000
    max_extracted_characters: int = 500_000
    max_redirects: int = 5
    timeout_seconds: float = 15.0
    allowed_media_types: tuple[str, ...] = (
        "text/html",
        "text/plain",
        "application/xhtml+xml",
    )
    allowed_ports: tuple[int, ...] = (80, 443)
    user_agent: str = "PorterForcesAI-SourceCapture/0.1"
    allow_https_downgrade: bool = False

    def __post_init__(self) -> None:
        if self.max_content_bytes < 1:
            raise ValueError("max_content_bytes must be positive")
        if self.max_extracted_characters < 1:
            raise ValueError("max_extracted_characters must be positive")
        if self.max_redirects < 0:
            raise ValueError("max_redirects cannot be negative")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not self.allowed_media_types:
            raise ValueError("allowed_media_types must not be empty")
        if not self.allowed_ports or any(
            port < 1 or port > 65_535 for port in self.allowed_ports
        ):
            raise ValueError("allowed_ports must contain valid network ports")


@dataclass(frozen=True, slots=True)
class CapturedSource:
    """A bounded, content-addressed representation of one registered hit."""

    capture_id: str
    source_id: str
    execution_id: str
    query_id: str
    discovered_url: str
    final_url: str
    title: str
    publisher: str
    retrieved_at: datetime
    media_type: str
    byte_length: int
    content_sha256: str
    extracted_text_sha256: str
    extracted_text: str
    text_truncated: bool
    trust_classification: str = UNTRUSTED_EXTERNAL_CONTENT


_BLOCK_TAGS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "dd",
        "div",
        "dl",
        "dt",
        "figcaption",
        "figure",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "td",
        "th",
        "tr",
        "ul",
    }
)
_IGNORED_TAGS = frozenset({"canvas", "noscript", "script", "style", "svg", "template"})
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class _CleanTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del attrs
        normalized = tag.casefold()
        if normalized in _IGNORED_TAGS:
            self._ignored_depth += 1
        elif self._ignored_depth == 0 and normalized in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del attrs
        if self._ignored_depth == 0 and tag.casefold() in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        if normalized in _IGNORED_TAGS:
            if self._ignored_depth:
                self._ignored_depth -= 1
        elif self._ignored_depth == 0 and normalized in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._ignored_depth == 0:
            self._parts.append(data)

    def text(self) -> str:
        return _normalize_text("".join(self._parts))


def _normalize_text(value: str) -> str:
    value = _CONTROL_CHARACTERS.sub(" ", unescape(value)).replace("\xa0", " ")
    lines = (" ".join(line.split()) for line in value.replace("\r", "\n").split("\n"))
    return "\n".join(line for line in lines if line)


def extract_clean_text(content: bytes, media_type: str, encoding: str = "utf-8") -> str:
    """Decode and strip executable/markup content with the standard library."""

    try:
        decoded = content.decode(encoding, errors="replace")
    except LookupError:
        decoded = content.decode("utf-8", errors="replace")
    if media_type in {"text/html", "application/xhtml+xml"}:
        parser = _CleanTextParser()
        parser.feed(decoded)
        parser.close()
        return parser.text()
    return _normalize_text(decoded)


def _validate_resolved_target(
    url: str,
    resolver: Resolver,
    allowed_ports: Sequence[int],
) -> str:
    try:
        canonical = canonicalize_public_url(url)
        parsed = urlsplit(canonical)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except (TypeError, ValueError) as exc:
        raise UnsafeSourceURLError("source URL is not an allowed absolute HTTP(S) URL") from exc
    if host is None:
        raise UnsafeSourceURLError("source URL has no hostname")
    if port not in allowed_ports:
        raise UnsafeSourceURLError("source URL uses a disallowed network port")
    try:
        resolved = tuple(resolver(host, port))
    except UnsafeSourceURLError:
        raise
    except Exception as exc:
        raise UnsafeSourceURLError("source hostname could not be resolved") from exc
    if not resolved:
        raise UnsafeSourceURLError("source hostname resolved to no addresses")
    for raw_address in resolved:
        try:
            address = ip_address(raw_address)
        except ValueError as exc:
            raise UnsafeSourceURLError("resolver returned an invalid IP address") from exc
        if (
            not address.is_global
            or address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        ):
            raise UnsafeSourceURLError(
                "source hostname resolves to a private, local, reserved, or non-global address"
            )
    return canonical


_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


class SafeSourceCapture:
    """Capture registered public sources under explicit network and size policy."""

    def __init__(
        self,
        ledger: DiscoveryLedger,
        *,
        policy: CapturePolicy | None = None,
        resolver: Resolver = system_resolver,
        client: httpx.Client | None = None,
    ) -> None:
        self._ledger = ledger
        self._policy = policy or CapturePolicy()
        self._resolver = resolver
        self._owns_client = client is None
        self._client = client or httpx.Client(
            follow_redirects=False,
            timeout=self._policy.timeout_seconds,
            trust_env=False,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> SafeSourceCapture:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def capture(self, source_id: str) -> CapturedSource:
        """Fetch a source that was minted by this service's discovery ledger."""

        hit = self._ledger.require_hit(source_id)
        current_url = _validate_resolved_target(
            hit.url,
            self._resolver,
            self._policy.allowed_ports,
        )
        discovered_url = current_url
        visited: set[str] = set()
        redirects = 0

        while True:
            if current_url in visited:
                raise SourceLimitError("source redirect loop detected")
            visited.add(current_url)
            try:
                with self._client.stream(
                    "GET",
                    current_url,
                    headers={
                        "Accept": "text/html,text/plain,application/xhtml+xml",
                        "User-Agent": self._policy.user_agent,
                    },
                    follow_redirects=False,
                    timeout=self._policy.timeout_seconds,
                ) as response:
                    if response.status_code in _REDIRECT_STATUSES:
                        location = response.headers.get("location")
                        if not location:
                            raise SourceFetchError("source redirect has no Location header")
                        if redirects >= self._policy.max_redirects:
                            raise SourceLimitError("source exceeded the redirect limit")
                        candidate = urljoin(current_url, location)
                        next_url = _validate_resolved_target(
                            candidate,
                            self._resolver,
                            self._policy.allowed_ports,
                        )
                        if (
                            not self._policy.allow_https_downgrade
                            and urlsplit(current_url).scheme == "https"
                            and urlsplit(next_url).scheme == "http"
                        ):
                            raise UnsafeSourceURLError("HTTPS-to-HTTP redirects are disabled")
                        current_url = next_url
                        redirects += 1
                        continue
                    if not 200 <= response.status_code < 300:
                        raise SourceFetchError(
                            f"source returned HTTP status {response.status_code}"
                        )
                    media_type = response.headers.get("content-type", "").split(";", 1)[
                        0
                    ].strip().casefold()
                    if media_type not in self._policy.allowed_media_types:
                        raise UnsupportedSourceTypeError(
                            "source response has a disallowed or missing Content-Type"
                        )
                    content_length = response.headers.get("content-length")
                    if content_length is not None:
                        try:
                            declared_length = int(content_length)
                        except ValueError as exc:
                            raise SourceFetchError(
                                "source returned an invalid Content-Length"
                            ) from exc
                        if declared_length < 0:
                            raise SourceFetchError("source returned a negative Content-Length")
                        if declared_length > self._policy.max_content_bytes:
                            raise SourceLimitError("source exceeds the content-size limit")
                    encoding = response.encoding or "utf-8"
                    chunks: list[bytes] = []
                    total = 0
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > self._policy.max_content_bytes:
                            raise SourceLimitError("source exceeds the content-size limit")
                        chunks.append(chunk)
                    content = b"".join(chunks)
            except SourceCaptureError:
                raise
            except (httpx.HTTPError, OSError) as exc:
                raise SourceFetchError("source request failed") from exc
            break

        extracted = extract_clean_text(content, media_type, encoding)
        if not extracted:
            raise SourceFetchError("source contained no extractable text")
        text_truncated = len(extracted) > self._policy.max_extracted_characters
        extracted = extracted[: self._policy.max_extracted_characters]
        content_hash = hashlib.sha256(content).hexdigest()
        text_hash = hashlib.sha256(extracted.encode("utf-8")).hexdigest()
        capture_basis = f"{source_id}:{current_url}:{content_hash}".encode()
        capture_id = f"CAP-{hashlib.sha256(capture_basis).hexdigest()[:32]}"
        publisher = urlsplit(current_url).hostname or "unknown publisher"
        title = _normalize_text(hit.title)[:1_000] or publisher
        return CapturedSource(
            capture_id=capture_id,
            source_id=hit.source_id,
            execution_id=hit.execution_id,
            query_id=hit.query_id,
            discovered_url=discovered_url,
            final_url=current_url,
            title=title,
            publisher=publisher,
            retrieved_at=datetime.now(UTC),
            media_type=media_type,
            byte_length=len(content),
            content_sha256=content_hash,
            extracted_text_sha256=text_hash,
            extracted_text=extracted,
            text_truncated=text_truncated,
        )


_PROMOTABLE_PUBLIC_SOURCE_CLASSES = frozenset(
    {
        SourceClass.REGULATOR,
        SourceClass.LEGISLATION,
        SourceClass.OFFICIAL_STATISTICS,
        SourceClass.AUDITED_FILING,
        SourceClass.COMPANY_DISCLOSURE,
        SourceClass.ACADEMIC,
        SourceClass.INDUSTRY_RESEARCH,
        SourceClass.REPUTABLE_MEDIA,
        SourceClass.VENDOR,
    }
)


def promote_capture_to_evidence(
    capture: CapturedSource,
    *,
    source_class: SourceClass,
    publisher: str | None = None,
    published_at: date | None = None,
    excerpt: str | None = None,
    quality_score: float = 0.5,
    freshness_score: float = 0.5,
    applicability_score: float = 0.5,
    geographies: Sequence[str] = (),
    applicable_entities: Sequence[str] = (),
    applicable_period: str | None = None,
) -> EvidenceItem:
    """Promote a captured representation, never a search snippet, to evidence."""

    if source_class not in _PROMOTABLE_PUBLIC_SOURCE_CLASSES:
        raise ValueError("source_class cannot be used for captured public-web evidence")
    actual_text_hash = hashlib.sha256(capture.extracted_text.encode("utf-8")).hexdigest()
    if actual_text_hash != capture.extracted_text_sha256:
        raise ValueError("captured extracted-text hash does not verify")
    captured_text = _normalize_text(capture.extracted_text)
    requested_excerpt = _normalize_text(excerpt or captured_text)
    if requested_excerpt not in captured_text:
        raise ValueError("evidence excerpt must be an exact passage from the capture")
    selected_excerpt = requested_excerpt[:12_000]
    if not selected_excerpt:
        raise ValueError("evidence excerpt must not be empty")
    evidence_basis = f"{capture.capture_id}:{source_class.value}".encode()
    evidence_id = f"E-{hashlib.sha256(evidence_basis).hexdigest()[:32]}"
    truncation_note = (
        " Extracted text was truncated at the configured capture limit."
        if capture.text_truncated
        else ""
    )
    return EvidenceItem(
        evidence_id=evidence_id,
        origin=EvidenceOrigin.PUBLIC_WEB,
        source_class=source_class,
        title=capture.title,
        publisher=(publisher or capture.publisher).strip(),
        source_url=capture.final_url,
        published_at=published_at,
        retrieved_at=capture.retrieved_at,
        excerpt=selected_excerpt,
        content_sha256=capture.content_sha256,
        geographies=list(geographies),
        applicable_entities=list(applicable_entities),
        applicable_period=applicable_period,
        quality_score=quality_score,
        freshness_score=freshness_score,
        applicability_score=applicability_score,
        notes=(
            f"Captured from registered source {capture.source_id}; content is "
            f"{UNTRUSTED_EXTERNAL_CONTENT}.{truncation_note}"
        ),
    )
