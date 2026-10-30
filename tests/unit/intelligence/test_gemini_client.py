from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.intelligence.gemini_client import (
    DEFAULT_GEMINI_MODEL,
    GeminiAPIError,
    GeminiClient,
    GeminiConfig,
    GeminiConfigurationError,
    GeminiResponseError,
    load_gemini_config,
)
from app.intelligence.schemas import SourceAnalysis
from app.intelligence.gemini_schema import sanitize_gemini_schema


class FakeModels:
    def __init__(self, *, response=None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls: list[dict[str, object]] = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class RecordingFactory:
    def __init__(self, models: FakeModels):
        self.models = models
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(models=self.models)


def test_configuration_uses_default_and_environment_model() -> None:
    default = load_gemini_config({})
    configured = load_gemini_config(
        {
            "GEMINI_API_KEY": "  secret-key  ",
            "GEMINI_MODEL": "  custom-model  ",
        }
    )

    assert default.api_key is None
    assert DEFAULT_GEMINI_MODEL == "gemini-3.5-flash-lite"
    assert default.model == DEFAULT_GEMINI_MODEL
    assert GeminiConfig().model == DEFAULT_GEMINI_MODEL
    assert load_gemini_config({"GEMINI_MODEL": "  "}).model == DEFAULT_GEMINI_MODEL
    assert configured.api_key == "secret-key"
    assert configured.model == "custom-model"
    assert "secret-key" not in repr(configured)


def test_missing_api_key_is_lazy_and_fails_only_on_generation() -> None:
    factory = RecordingFactory(FakeModels(response=None))
    client = GeminiClient.from_environment({}, client_factory=factory)

    assert factory.calls == []
    with pytest.raises(GeminiConfigurationError, match="GEMINI_API_KEY"):
        client.generate_text("Analyze this source")
    assert factory.calls == []


def test_text_generation_initializes_once_with_timeout() -> None:
    models = FakeModels(response=SimpleNamespace(text="  useful result  "))
    factory = RecordingFactory(models)
    client = GeminiClient(
        GeminiConfig(
            api_key="secret-key",
            model="test-model",
            timeout_seconds=12.5,
        ),
        client_factory=factory,
    )

    assert client.generate_text("First prompt") == "useful result"
    assert client.generate_text("Second prompt") == "useful result"

    assert len(factory.calls) == 1
    assert factory.calls[0]["api_key"] == "secret-key"
    assert factory.calls[0]["http_options"].timeout == 12_500
    assert models.calls[0]["model"] == "test-model"


def test_structured_generation_accepts_sdk_parsed_output() -> None:
    parsed = {
        "decision": "REJECT",
        "confidence": 97,
        "reason": "This is a paid tender aggregator.",
        "is_paid_aggregator": True,
    }
    models = FakeModels(response=SimpleNamespace(parsed=parsed, text=None))
    client = GeminiClient(
        GeminiConfig(api_key="secret-key"),
        client_factory=RecordingFactory(models),
    )

    result = client.generate_structured(
        "Analyze this source",
        SourceAnalysis,
    )

    assert result.confidence == 97
    assert result.is_paid_aggregator is True
    config = models.calls[0]["config"]
    assert config.response_mime_type == "application/json"
    assert config.response_schema == sanitize_gemini_schema(
        SourceAnalysis.model_json_schema()
    )


def test_structured_generation_falls_back_to_response_text() -> None:
    response = SimpleNamespace(
        parsed=None,
        text=(
            '{"decision":"REVIEW","confidence":45,"reason":"Insufficient evidence."}'
        ),
    )
    client = GeminiClient(
        GeminiConfig(api_key="secret-key"),
        client_factory=RecordingFactory(FakeModels(response=response)),
    )

    result = client.generate_structured("Analyze", SourceAnalysis)

    assert result.confidence == 45


@pytest.mark.parametrize(
    "response",
    [
        SimpleNamespace(parsed=None, text="not-json"),
        SimpleNamespace(
            parsed={
                "decision": "ACCEPT",
                "confidence": 500,
                "reason": "Invalid bound.",
            },
            text=None,
        ),
        SimpleNamespace(parsed=None, text=None),
    ],
)
def test_malformed_structured_response_is_sanitized(response) -> None:
    client = GeminiClient(
        GeminiConfig(api_key="secret-key"),
        client_factory=RecordingFactory(FakeModels(response=response)),
    )

    with pytest.raises(GeminiResponseError) as exc_info:
        client.generate_structured("Analyze", SourceAnalysis)

    assert "secret-key" not in str(exc_info.value)
    assert "not-json" not in str(exc_info.value)


def test_api_failure_is_wrapped_without_prompt_or_key() -> None:
    client = GeminiClient(
        GeminiConfig(api_key="secret-key"),
        client_factory=RecordingFactory(
            FakeModels(error=RuntimeError("provider details"))
        ),
    )

    with pytest.raises(GeminiAPIError) as exc_info:
        client.generate_text("prompt-containing-private-data")

    message = str(exc_info.value)
    assert "secret-key" not in message
    assert "prompt-containing-private-data" not in message
    assert "provider details" not in message
