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
