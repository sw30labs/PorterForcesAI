import pytest
from pydantic import ValidationError

from porter_forces_ai.settings import Settings, normalize_openai_base_url


def test_default_profile_uses_requested_qwen_model_and_loopback() -> None:
    settings = Settings(_env_file=None)

    assert settings.llm_model == "Qwen3.8-27B-4bit"
    assert settings.llm_base_url == "http://127.0.0.1:8000/v1"
    assert settings.api_host == "127.0.0.1"


def test_openai_base_url_is_normalized_to_v1() -> None:
    assert normalize_openai_base_url("http://127.0.0.1:8000") == (
        "http://127.0.0.1:8000/v1"
    )


@pytest.mark.parametrize(
    "updates",
    [
        {"llm_base_url": "http://model.example/v1"},
        {"api_host": "0.0.0.0"},
        {"ui_origin": "https://console.example"},
    ],
)
def test_remote_boundaries_fail_closed_without_explicit_authority(
    updates: dict[str, str],
) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **updates)

