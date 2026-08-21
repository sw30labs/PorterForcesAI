"""Application settings with a local-inference default and explicit egress knobs."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def normalize_openai_base_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("LLM base URL must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("LLM base URL cannot contain credentials, query, or fragment")
    path = parsed.path.rstrip("/")
    if not path.endswith("/v1"):
        path = f"{path}/v1" if path else "/v1"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PFA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm_model: str = "Qwen3.8-27B-4bit"
    llm_base_url: str = "http://127.0.0.1:8000/v1"
    llm_api_key: SecretStr | None = None
    llm_timeout_seconds: float = Field(default=180, gt=0, le=900)
    llm_max_retries: int = Field(default=2, ge=0, le=6)
    allow_remote_model_endpoint: bool = False
    search_region: str = "us-en"
    search_max_results: int = Field(default=8, ge=1, le=20)
    search_timeout_seconds: int = Field(default=15, ge=1, le=60)
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8765, ge=1, le=65_535)
    ui_origin: str = "http://127.0.0.1:3000"
    database_path: Path = Path("data/porter-forces.db")
    artifacts_dir: Path = Path("runs")
    analysis_max_concurrency: int = Field(default=1, ge=1, le=5)
    ralph_max_attempts: int = Field(default=3, ge=1, le=10)
    ralph_max_budget_units: int = Field(default=6, ge=1, le=100)
    ralph_stall_limit: int = Field(default=2, ge=2, le=10)
    capture_max_sources: int = Field(default=15, ge=1, le=50)
    capture_timeout_seconds: int = Field(default=20, ge=1, le=90)
    capture_max_bytes: int = Field(default=2_000_000, ge=10_000, le=10_000_000)

    @field_validator("llm_base_url")
    @classmethod
    def normalize_url(cls, value: str) -> str:
        return normalize_openai_base_url(value)

    @model_validator(mode="after")
    def keep_model_local_unless_explicit(self) -> Settings:
        host = (urlsplit(self.llm_base_url).hostname or "").casefold()
        is_loopback = host in {"localhost", "127.0.0.1", "::1"}
        if not is_loopback and not self.allow_remote_model_endpoint:
            raise ValueError(
                "remote model endpoint is disabled; set PFA_ALLOW_REMOTE_MODEL_ENDPOINT=true "
                "only after approving the confidentiality boundary"
            )
        if not is_loopback and urlsplit(self.llm_base_url).scheme != "https":
            raise ValueError("remote model endpoints must use HTTPS")
        if self.api_host.casefold() not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("the local API may bind only to a loopback interface")
        ui = urlsplit(self.ui_origin)
        if (
            ui.scheme not in {"http", "https"}
            or (ui.hostname or "").casefold() not in {"localhost", "127.0.0.1", "::1"}
            or ui.path not in {"", "/"}
            or ui.query
            or ui.fragment
        ):
            raise ValueError("PFA_UI_ORIGIN must be an absolute loopback origin")
        return self
