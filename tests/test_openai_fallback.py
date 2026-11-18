from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from app.intelligence import openai_fallback
from app.intelligence.gemini_client import (
    GeminiAPIError, GeminiClient, GeminiConfig,
)
from app.intelligence.gemini_diagnostics import GeminiDiagnostic


class CheckSchema(BaseModel):
    value: int


def quota_failure() -> GeminiAPIError:
    return GeminiAPIError(
        "Gemini generation failed.",
        diagnostic=GeminiDiagnostic("QUOTA_EXCEEDED", 429, True),
    )


def client_with_gemini_failure(monkeypatch, category="QUOTA_EXCEEDED"):
    client = GeminiClient(GeminiConfig(api_key="not-real"), max_attempts=1)

    def fail(*args, **kwargs):
        raise GeminiAPIError(
            "Gemini generation failed.",
            diagnostic=GeminiDiagnostic(category, 429, False),
        )

    monkeypatch.setattr(client, "_generate", fail)
    return client


def test_fallback_off_by_default(monkeypatch):
    monkeypatch.delenv("OPENAI_FALLBACK_ENABLED", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    client = client_with_gemini_failure(monkeypatch)
    with pytest.raises(GeminiAPIError, match="Gemini generation failed"):
        client.generate_structured("sample", CheckSchema)


def test_fallback_requires_api_key(monkeypatch):
    monkeypatch.setenv("OPENAI_FALLBACK_ENABLED", "true")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert openai_fallback.fallback_enabled() is False


def test_structured_quota_uses_openai(monkeypatch):
    monkeypatch.setenv("OPENAI_FALLBACK_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    client = client_with_gemini_failure(monkeypatch)
    captured = {}

    def fake_fallback(prompt, schema, **kwargs):
        captured.update(prompt=prompt, schema=schema, **kwargs)
        return CheckSchema(value=7)

    monkeypatch.setattr(openai_fallback, "generate_structured", fake_fallback)
    actual = client.generate_structured(
        "evidence", CheckSchema, system_instruction="Keep it accurate"
    )
    assert actual.value == 7
    assert captured["schema"] is CheckSchema
    assert captured["system_instruction"] == "Keep it accurate"


@pytest.mark.parametrize("category", ["RATE_LIMIT", "AUTHENTICATION_ERROR", "TIMEOUT"])
def test_other_gemini_failures_do_not_trigger_paid_fallback(monkeypatch, category):
    monkeypatch.setenv("OPENAI_FALLBACK_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    client = client_with_gemini_failure(monkeypatch, category)

    def fail_if_called(*args, **kwargs):
        pytest.fail("OpenAI backup must only be used for confirmed quota exhaustion")

    monkeypatch.setattr(openai_fallback, "generate_structured", fail_if_called)
    with pytest.raises(GeminiAPIError):
        client.generate_structured("evidence", CheckSchema)


def test_text_quota_uses_openai(monkeypatch):
    monkeypatch.setenv("OPENAI_FALLBACK_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    client = client_with_gemini_failure(monkeypatch)
    monkeypatch.setattr(
        openai_fallback, "generate_text", lambda *args, **kwargs: "Backup result"
    )
    assert client.generate_text("evidence") == "Backup result"


def test_openai_failure_preserves_safe_gemini_error(monkeypatch):
    monkeypatch.setenv("OPENAI_FALLBACK_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    client = client_with_gemini_failure(monkeypatch)

    def fake_failure(*args, **kwargs):
        raise openai_fallback.OpenAIFallbackError("no response")

    monkeypatch.setattr(openai_fallback, "generate_structured", fake_failure)
    with pytest.raises(GeminiAPIError, match="Both Gemini and OpenAI backup") as error:
        client.generate_structured("evidence", CheckSchema)
    assert error.value.diagnostic.category == "QUOTA_EXCEEDED"


def test_openai_sdk_structured_schema_and_budget(monkeypatch):
    monkeypatch.setenv("OPENAI_FALLBACK_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    monkeypatch.setenv("OPENAI_FALLBACK_MODEL", "gpt-5-mini")
    captured = {}
    import openai

    def fake_parse(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(output_parsed=CheckSchema(value=42))

    monkeypatch.setattr(
        openai, "OpenAI", lambda **kwargs: SimpleNamespace(
            responses=SimpleNamespace(parse=fake_parse)
        ),
    )
    import app.beta_security as security

    budget_calls = []
    monkeypatch.setattr(
        security, "reserve_ai_attempt", lambda: budget_calls.append(1)
    )
    callback_calls = []
    actual = openai_fallback.generate_structured(
        "test", CheckSchema, before_attempt=lambda: callback_calls.append(1)
    )
    assert actual.value == 42
    assert captured["store"] is False
    assert captured["text_format"] is CheckSchema
    assert captured["model"] == "gpt-5-mini"
    assert len(budget_calls) == len(callback_calls) == 1


def test_gemini_503_uses_opted_in_openai_once_then_circuit_bypasses_sdk(monkeypatch):
    from unittest.mock import Mock
    from google.genai.errors import APIError
    from app.intelligence import gemini_client
    from app.intelligence.gemini_circuit import GeminiProviderCircuit

    monkeypatch.setenv("OPENAI_FALLBACK_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    clock = [100.0]
    circuit = GeminiProviderCircuit(clock=lambda: clock[0])
    monkeypatch.setattr(gemini_client, "PROVIDER_CIRCUIT", circuit)
    sdk = Mock()
    sdk.models.generate_content.side_effect = [
        APIError(503, {"message": "Temporary provider outage"}),
        SimpleNamespace(parsed={"value": 17}),
    ]
    client = GeminiClient(
        GeminiConfig(api_key="fake-key"), client_factory=lambda **_: sdk,
        max_attempts=3, sleep=lambda _: None,
    )
    called = []
    def fallback(prompt, schema, **kwargs):
        called.append(prompt)
        return CheckSchema(value=11)
    monkeypatch.setattr(openai_fallback, "generate_structured", fallback)
    assert client.generate_structured("first notice", CheckSchema).value == 11
    assert client.generate_structured("second notice", CheckSchema).value == 11
    assert sdk.models.generate_content.call_count == 1
    assert called == ["first notice", "second notice"]
    clock[0] += 301  # Five-minute 503 cooldown has elapsed.
    assert client.generate_structured("third notice", CheckSchema).value == 17
    assert sdk.models.generate_content.call_count == 2
    assert circuit.unavailable() is None


def test_quota_error_opens_long_cooldown_without_repeat_requests(monkeypatch):
    from unittest.mock import Mock
    from google.genai.errors import APIError
    from app.intelligence import gemini_client
    from app.intelligence.gemini_circuit import GeminiProviderCircuit

    monkeypatch.setenv("OPENAI_FALLBACK_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    circuit = GeminiProviderCircuit(clock=lambda: 500.0)
    monkeypatch.setattr(gemini_client, "PROVIDER_CIRCUIT", circuit)
    sdk = Mock()
    sdk.models.generate_content.side_effect = APIError(
        429, {"message": "Quota exceeded for this model"}
    )
    client = GeminiClient(
        GeminiConfig(api_key="fake-key"), client_factory=lambda **_: sdk
    )
    monkeypatch.setattr(
        openai_fallback, "generate_structured",
        lambda *_args, **_kwargs: CheckSchema(value=29),
    )
    assert client.generate_structured("one", CheckSchema).value == 29
    assert client.generate_structured("two", CheckSchema).value == 29
    assert sdk.models.generate_content.call_count == 1
    assert circuit.unavailable().category == "QUOTA_EXCEEDED"


def test_503_without_opt_in_keeps_existing_bounded_retries(monkeypatch):
    from unittest.mock import Mock
    from google.genai.errors import APIError
    monkeypatch.delenv("OPENAI_FALLBACK_ENABLED", raising=False)
    sdk = Mock()
    sdk.models.generate_content.side_effect = [
        APIError(503, {"message": "Temporary outage"}),
        SimpleNamespace(parsed={"value": 42}),
    ]
    client = GeminiClient(
        GeminiConfig(api_key="fake-key"),
        client_factory=lambda **_: sdk,
        sleep=lambda _: None,
    )
    assert client.generate_structured("notice", CheckSchema).value == 42
    assert sdk.models.generate_content.call_count == 2


def test_openai_rate_limited_backup_pauses_repeated_attempts(monkeypatch):
    import openai
    from app.intelligence import openai_fallback
    monkeypatch.setenv("OPENAI_FALLBACK_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    monkeypatch.setattr(openai_fallback, "_rate_limited_until", 0.0)
    attempts = []

    class SimulatedRateLimitError(Exception):
        status_code = 429

    def fail(**kwargs):
        attempts.append(1)
        raise SimulatedRateLimitError("mocked")

    monkeypatch.setattr(
        openai,
        "OpenAI",
        lambda **kwargs: SimpleNamespace(
            responses=SimpleNamespace(parse=fail),
        ),
    )
    with pytest.raises(openai_fallback.OpenAIFallbackError):
        openai_fallback.generate_structured("first", CheckSchema)
    with pytest.raises(openai_fallback.OpenAIFallbackError, match="temporarily rate limited"):
        openai_fallback.generate_structured("second", CheckSchema)
    assert attempts == [1]
    assert openai_fallback._rate_limited_until > openai_fallback.time.monotonic()
