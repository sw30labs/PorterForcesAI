"""LangChain model adapter for an oMLX OpenAI-compatible server."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import httpx

from porter_forces_ai.settings import Settings


class OmlxConfigurationError(ValueError):
    """The local endpoint or selected model is not usable."""


def _api_key(settings: Settings) -> str:
    if settings.llm_api_key is None:
        return "local-no-auth"
    return settings.llm_api_key.get_secret_value()


def create_chat_model(settings: Settings) -> Any:
    """Return the native LangChain model object expected by Deep Agents."""

    if not settings.llm_model.strip():
        raise OmlxConfigurationError("PFA_LLM_MODEL must name an oMLX model id or alias")
    from langchain_openai import ChatOpenAI

    host = (urlsplit(settings.llm_base_url).hostname or "").casefold()
    kwargs: dict[str, Any] = {
        "model": settings.llm_model,
        "base_url": settings.llm_base_url,
        "api_key": _api_key(settings),
        "temperature": 0,
        "timeout": settings.llm_timeout_seconds,
        "max_retries": settings.llm_max_retries,
        "use_responses_api": False,
        "stream_usage": False,
    }
    if host in {"localhost", "127.0.0.1", "::1"}:
        # Do not send local model traffic through corporate/system proxies.
        kwargs["http_client"] = httpx.Client(trust_env=False, follow_redirects=False)
        kwargs["http_async_client"] = httpx.AsyncClient(
            trust_env=False,
            follow_redirects=False,
        )
    return ChatOpenAI(**kwargs)


def list_models(settings: Settings) -> list[str]:
    endpoint = f"{settings.llm_base_url.rstrip('/')}/models"
    headers = {"Authorization": f"Bearer {_api_key(settings)}"}
    try:
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            response = client.get(endpoint, headers=headers, timeout=10)
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise OmlxConfigurationError(f"could not query oMLX model inventory at {endpoint}") from exc
    rows = payload.get("data", []) if isinstance(payload, dict) else []
    return sorted(
        str(row.get("id")).strip()
        for row in rows
        if isinstance(row, dict) and str(row.get("id") or "").strip()
    )

