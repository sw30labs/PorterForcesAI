"""Fail-closed policy for text that is about to leave the local trust boundary."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


class EgressViolation(ValueError):
    """Raised when an outbound search query may contain confidential material."""


_SENSITIVE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email_address", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)),
    ("iban", re.compile(r"\b[A-Z]{2}\d{2}(?:[ -]?[A-Z0-9]){11,30}\b", re.I)),
    ("ssn", re.compile(r"(?<!\d)\d{3}[- ]\d{2}[- ]\d{4}(?!\d)")),
    (
        "long_account_or_card_number",
        re.compile(r"(?<!\d)(?:\d[ -]?){11,18}\d(?!\d)"),
    ),
    (
        "credential",
        re.compile(
            r"\b(?:bearer\s+|sk-|api[_ -]?key\s*[:=]|token\s*[:=]|password\s*[:=])"
            r"[A-Z0-9._~+/=-]{8,}",
            re.I,
        ),
    ),
    (
        "labelled_confidential_data",
        re.compile(
            r"\b(?:mnpi|confidential|strictly private|internal only)\s*[:=]\s*\S+",
            re.I,
        ),
    ),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+\b")),
)


@dataclass(frozen=True, slots=True)
class EgressPolicy:
    """Outbound search policy populated with run-specific sensitive terms."""

    forbidden_terms: tuple[str, ...] = ()
    max_query_chars: int = 240

    def validate(self, query: str) -> str:
        normalized = unicodedata.normalize("NFKC", query)
        if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
            raise EgressViolation("search query contains control characters")
        normalized = " ".join(normalized.split())
        if not normalized:
            raise EgressViolation("search query is empty")
        if len(normalized) > self.max_query_chars:
            raise EgressViolation(
                f"search query exceeds the {self.max_query_chars}-character public limit"
            )
        folded = normalized.casefold()
        for term in self.forbidden_terms:
            clean_term = " ".join(unicodedata.normalize("NFKC", term).split())
            if len(clean_term) >= 4 and clean_term.casefold() in folded:
                # Do not echo the offending value into logs or agent-visible errors.
                raise EgressViolation("search query contains run-confidential context")

        for label, pattern in _SENSITIVE_PATTERNS:
            if pattern.search(normalized):
                raise EgressViolation(f"search query matches blocked data class: {label}")
        return normalized
