"""No provider calls in these tests: all API calls are mocked."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock
import json

import pytest
from pydantic import BaseModel

from app.intelligence import free_api_fallback as free
from app.intelligence import gemini_client
from app.intelligence.gemini_client import (
    GeminiAPIError, GeminiClient, GeminiConfig,
)
from app.intelligence.gemini_diagnostics import GeminiDiagnostic


class Sample(BaseModel):
    value: int


def configured(name="groq", **overrides):
    d = dict(
        name=name, model="openai/gpt-oss-120b" if name == "groq" else "openai/gpt-oss-20b",
        base_url="https://api.groq.com/openai/v1"
        if name == "groq" else "https://integrate.api.nvidia.com/v1",
        api_key="fake-only", rpm=25, tpm=7000, rpd=900, tpd=180000,
    )
    d.update(overrides)
    return free.ProviderConfig(**d)


def mock_response(value=11, *, input_tokens=300, output_tokens=32):
    return SimpleNamespace(
        choices=[SimpleNamespace(
            finish_reason="stop",
            message=SimpleNamespace(content=json.dumps({"value": value})),
        )],
        usage=SimpleNamespace(
            prompt_tokens=input_tokens, completion_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
        ),
    )


def test_all_community_providers_off_without_explicit_opt_in(monkeypatch):
    for provider in ("GROQ", "NVIDIA"):
        monkeypatch.setenv(f"{provider}_API_KEY", "secret-test-only")
        monkeypatch.delenv(f"{provider}_FALLBACK_ENABLED", raising=False)
    assert not free.any_enabled()
    assert free.enabled_configs() == []
    with pytest.raises(free.FreeAPIError):
        free.generate_structured("document", Sample)


def test_groq_free_defaults_under_its_published_limits(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake")
    monkeypatch.setenv("GROQ_FALLBACK_ENABLED", "true")
    monkeypatch.delenv("NVIDIA_FALLBACK_ENABLED", raising=False)
    (config,) = free.enabled_configs()
    assert config.name == "groq"
    assert config.model == "openai/gpt-oss-120b"
    assert (config.rpm, config.tpm, config.rpd, config.tpd) == (
        25, 7000, 900, 180000,
    )


def test_budget_enforces_rpm_tpm_rpd_tpd_and_rollover():
    now = [1000.0]
    budget = free.RequestBudget(
        configured(rpm=2, tpm=4000, rpd=3, tpd=4500),
        clock=lambda: now[0],
    )
    assert budget.reserve(2000)
    assert budget.reserve(1900)
    assert not budget.reserve(1)  # RPM
    now[0] += 61
    assert budget.reserve(600)
    assert not budget.reserve(1)  # RPD
    now[0] += 86401
    assert budget.reserve(2000)


def test_large_document_skips_free_api_without_network_call(monkeypatch):
    config = configured(tpm=7000)
    monkeypatch.setattr(free, "enabled_configs", lambda: [config])
    import openai
    openai_sdk = Mock()
    monkeypatch.setattr(openai, "OpenAI", openai_sdk)
    with pytest.raises(free.FreeAPIError):
        free.generate_structured("x" * 40000, Sample)
    openai_sdk.assert_not_called()


def test_groq_uses_json_schema_and_validates_response(monkeypatch):
    config = configured()
    monkeypatch.setattr(free, "enabled_configs", lambda: [config])
    monkeypatch.setattr(free, "_get_budget", lambda _: free.RequestBudget(config))
    import openai
    capture = {}
    def create(**kwargs):
        capture.update(kwargs)
        return mock_response(72)
    monkeypatch.setattr(
        openai, "OpenAI",
        lambda **kwargs: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        ),
    )
    calls = []
    import app.beta_security as security
    monkeypatch.setattr(security, "reserve_ai_attempt", lambda: calls.append("reserve"))
    actual = free.generate_structured(
        "Evidence only", Sample,
        system_instruction="Do not invent facts",
        before_attempt=lambda: calls.append("callback"),
    )
    assert actual.value == 72
    assert capture["model"] == "openai/gpt-oss-120b"
    assert capture["response_format"]["type"] == "json_schema"
    assert capture["response_format"]["json_schema"]["strict"] is False
    assert calls == ["reserve", "callback"]
    assert capture["messages"][0]["content"] == "Do not invent facts"


def test_nvidia_uses_available_model_and_validates_locally(monkeypatch):
    config = configured(name="nvidia")
    monkeypatch.setattr(free, "enabled_configs", lambda: [config])
    monkeypatch.setattr(free, "_get_budget", lambda _: free.RequestBudget(config))
    import openai
    capture = {}
    def create(**kwargs):
        capture.update(kwargs)
        return mock_response(13)
    monkeypatch.setattr(
        openai, "OpenAI",
        lambda **kwargs: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        ),
    )
    assert free.generate_structured("some notice", Sample).value == 13
    assert capture["model"] == "openai/gpt-oss-20b"
    assert "response_format" not in capture
    assert "JSON schema" in capture["messages"][0]["content"]


def test_provider_rate_limit_opens_cooldown_and_falls_through(monkeypatch):
    primary = configured("groq")
    secondary = configured("nvidia")
    monkeypatch.setattr(free, "enabled_configs", lambda: [primary, secondary])
    first_budget = free.RequestBudget(primary, clock=lambda: 1000)
    second_budget = free.RequestBudget(secondary, clock=lambda: 1000)
    monkeypatch.setattr(
        free, "_get_budget",
        lambda c: first_budget if c.name == "groq" else second_budget,
    )
    import openai
    class RateLimitError(Exception):
        status_code = 429
    calls = []
    def create(**kwargs):
        calls.append(kwargs["model"])
        if kwargs["model"] == primary.model:
            raise RateLimitError("simulated")
        return mock_response(22)
    monkeypatch.setattr(
        openai, "OpenAI",
        lambda **kwargs: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        ),
    )
    assert free.generate_structured("document 1", Sample).value == 22
    assert free.generate_structured("document 2", Sample).value == 22
    assert calls == [primary.model, secondary.model, secondary.model]


def test_gemini_failure_calls_enabled_free_api_before_openai(monkeypatch):
    from app.intelligence import openai_fallback
    monkeypatch.setenv("GROQ_API_KEY", "test")
    monkeypatch.setenv("GROQ_FALLBACK_ENABLED", "true")
    monkeypatch.setenv("OPENAI_FALLBACK_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    client = GeminiClient(GeminiConfig(api_key="fake"), max_attempts=1)
    monkeypatch.setattr(
        client, "_generate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            GeminiAPIError(
                "temporarily down",
                diagnostic=GeminiDiagnostic("MODEL_UNAVAILABLE", 503, True),
            )
        ),
    )
    monkeypatch.setattr(free, "generate_structured",
                        lambda *_args, **_kwargs: Sample(value=14))
    monkeypatch.setattr(
        openai_fallback, "generate_structured",
        lambda *_args, **_kwargs: pytest.fail("OpenAI should not be called"),
    )
    assert client.generate_structured("notice", Sample).value == 14


def test_free_api_error_preserves_existing_paid_fallback(monkeypatch):
    from app.intelligence import openai_fallback
    monkeypatch.setenv("GROQ_API_KEY", "test")
    monkeypatch.setenv("GROQ_FALLBACK_ENABLED", "true")
    monkeypatch.setenv("OPENAI_FALLBACK_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    client = GeminiClient(GeminiConfig(api_key="fake"), max_attempts=1)
    monkeypatch.setattr(
        client, "_generate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            GeminiAPIError(
                "down", diagnostic=GeminiDiagnostic("MODEL_UNAVAILABLE", 503, True)
            )
        ),
    )
    monkeypatch.setattr(free, "generate_structured",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(
                            free.FreeAPIError("budget exhausted")
                        ))
    monkeypatch.setattr(
        openai_fallback, "generate_structured",
        lambda *_args, **_kwargs: Sample(value=19),
    )
    assert client.generate_structured("notice", Sample).value == 19
