import json
from types import SimpleNamespace
from unittest.mock import Mock

from google.genai import _transformers, errors
import pytest

from app.intelligence.gemini_client import GeminiConfig
from app.intelligence.schemas import AnalysisDecision, SourceAnalysis
from app.intelligence.gemini_schema import sanitize_gemini_schema
from app.intelligence.source_analyzer import SOURCE_SYSTEM_INSTRUCTION
from app.intelligence.source_request_diagnostics import source_failure_details
from scripts.gemini_source_diagnostic import SYNTHETIC_CONTENT, run_source_diagnostic


SECRET = "source-test-secret"
PRIVATE = "Private company, full page and Authorization: Bearer " + SECRET


def sdk_factory(response=None, error=None):
    sdk = Mock()
    sdk.models.generate_content.return_value = response or SimpleNamespace(
        parsed={"decision": "REVIEW", "confidence": 65, "reason": "Limited evidence"}
    )
    sdk.models.generate_content.side_effect = error
    return Mock(return_value=sdk), sdk


def test_source_request_construction_and_safe_metadata(capsys):
    factory, sdk = sdk_factory()
    assert (
        run_source_diagnostic(GeminiConfig(api_key=SECRET), client_factory=factory) == 0
    )
    request = sdk.models.generate_content.call_args.kwargs
    assert request["model"] == GeminiConfig().model
    assert SYNTHETIC_CONTENT in request["contents"]
    config = request["config"]
    assert config.response_schema == sanitize_gemini_schema(
        SourceAnalysis.model_json_schema()
    )
    assert config.response_mime_type == "application/json"
    assert config.system_instruction == SOURCE_SYSTEM_INSTRUCTION
    assert config.temperature == 0
    assert config.max_output_tokens is None
    sdk.models.generate_content.assert_called_once()
    assert factory.call_args.kwargs["http_options"].retry_options.attempts == 1
    output = capsys.readouterr().out
    assert "result: success" in output
    assert SECRET not in output
    assert SYNTHETIC_CONTENT not in output
    assert request["contents"] not in output
    assert SOURCE_SYSTEM_INSTRUCTION not in output
    metadata = json.loads(
        next(
            line.split(": ", 1)[1]
            for line in output.splitlines()
            if line.startswith("SOURCE request metadata:")
        )
    )
    assert metadata["prompt_chars"] == len(request["contents"])
    assert metadata["content_chars"] == len(SYNTHETIC_CONTENT)
    assert metadata["schema_bytes"] > 0
    assert metadata["fields"]["decision"]["enum_count"] == 3
    assert metadata["fields"]["organisation"]["nullable"]
    sdk.close.assert_called_once()


def test_schema_preserves_enum_nulls_and_confidence_constraints():
    schema = _transformers.t_schema(SimpleNamespace(vertexai=False), SourceAnalysis)
    assert schema.properties["decision"].enum == [
        item.value for item in AnalysisDecision
    ]
    assert schema.properties["confidence"].minimum == 0
    assert schema.properties["confidence"].maximum == 100
    assert set(schema.required) == {"decision", "confidence", "reason"}
    assert all(value.type.value != "OBJECT" for value in schema.properties.values())
    assert schema.properties["source_role"].enum == ["AUTHORITATIVE", "DISCOVERY_ONLY", "REPOST", "UNKNOWN"]
    for name in set(SourceAnalysis.model_fields) - set(schema.required) - {"source_role"}:
        assert schema.properties[name].nullable
    analysis = SourceAnalysis(
        decision="REVIEW",
        confidence=50,
        reason="Unknown",
        organisation=None,
        is_official=None,
    )
    assert analysis.organisation is None
    assert analysis.is_official is None


def test_source_uses_central_sanitizer_without_weakening_local_schema():
    from app.intelligence.schemas import CompanyMatch, OpportunityAnalysis

    assert SourceAnalysis.model_json_schema()["additionalProperties"] is False
    schema = _transformers.t_schema(
        SimpleNamespace(vertexai=False),
        sanitize_gemini_schema(SourceAnalysis.model_json_schema()),
    )
    assert "additional_properties" not in schema.model_dump(exclude_none=True)
    assert SourceAnalysis.model_config["extra"] == "forbid"
    assert OpportunityAnalysis.model_json_schema()["additionalProperties"] is False
    assert CompanyMatch.model_json_schema()["additionalProperties"] is False


def test_actual_sdk_wire_request_omits_unsupported_source_keyword(capsys):
    from google import genai
    import httpx

    bodies = []

    def handle(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [
                                {
                                    "text": json.dumps(
                                        {
                                            "decision": "REVIEW",
                                            "confidence": 70,
                                            "reason": "Limited evidence",
                                            "organisation": None,
                                            "is_official": None,
                                        }
                                    )
                                }
                            ],
                        },
                        "finishReason": "STOP",
                    }
                ]
            },
        )

    def factory(**kwargs):
        kwargs["vertexai"] = False
        kwargs["http_options"].client_args = {"transport": httpx.MockTransport(handle)}
        return genai.Client(**kwargs)

    assert (
        run_source_diagnostic(GeminiConfig(api_key=SECRET), client_factory=factory) == 0
    )
    assert len(bodies) == 1
    generation = bodies[0]["generationConfig"]
    schema = generation["responseSchema"]
    assert "additionalProperties" not in schema
    assert "additional_properties" not in schema
    assert schema["properties"]["decision"]["enum"] == ["ACCEPT", "REJECT", "REVIEW"]
    assert schema["properties"]["organisation"]["nullable"] is True
    assert generation["responseMimeType"] == "application/json"
    assert generation["temperature"] == 0
    assert "maxOutputTokens" not in generation
    assert SECRET not in capsys.readouterr().out


@pytest.mark.parametrize(
    "payload",
    [
        {"decision": "ACCEPT", "confidence": 90, "reason": "Official source"},
        {
            "decision": "REJECT",
            "confidence": 20,
            "reason": "Directory",
            "is_directory": True,
        },
        {
            "decision": "REVIEW",
            "confidence": 50,
            "reason": "Unknown",
            "organisation": None,
            "is_official": None,
        },
    ],
)
def test_real_schema_response_parsing(payload):
    factory, sdk = sdk_factory(
        response=SimpleNamespace(parsed=None, text=json.dumps(payload))
    )
    assert (
        run_source_diagnostic(
            GeminiConfig(api_key=SECRET), client_factory=factory, emit=Mock()
        )
        == 0
    )
    sdk.models.generate_content.assert_called_once()


@pytest.mark.parametrize(
    "payload",
    [
        {"decision": "UNKNOWN", "confidence": 10, "reason": "Unknown"},
        {"decision": "ACCEPT", "confidence": 101, "reason": "Unknown"},
        {
            "decision": "ACCEPT",
            "confidence": 90,
            "reason": "Unknown",
            "unexpected": PRIVATE,
        },
    ],
)
def test_invalid_responses_still_fail_validation(payload, capsys):
    factory, sdk = sdk_factory(response=SimpleNamespace(parsed=payload))
    assert (
        run_source_diagnostic(GeminiConfig(api_key=SECRET), client_factory=factory) == 1
    )
    output = capsys.readouterr().out
    assert "STRUCTURED_OUTPUT_ERROR" in output
    assert SECRET not in output
    assert PRIVATE not in output
    sdk.models.generate_content.assert_called_once()


@pytest.mark.parametrize(
    "message,category",
    [
        (
            'Invalid JSON payload received. Unknown name "additionalProperties" at "generation_config.response_schema": Cannot find field.',
            "INVALID_SCHEMA",
        ),
        ("Invalid temperature in generation_config", "INVALID_GENERATION_CONFIG"),
        ("contents must be specified", "INVALID_CONTENT"),
        ("Unsupported parameter: mystery", "UNSUPPORTED_PARAMETER"),
        ("Input exceeds the token limit", "CONTEXT_TOO_LARGE"),
        ("Parameter not supported by this model", "MODEL_PARAMETER_ERROR"),
        ("Bad request", "OTHER_INVALID_REQUEST"),
    ],
)
def test_invalid_request_classification_is_safe(message, category):
    error = errors.APIError(400, {"message": message + " " + PRIVATE})
    details = source_failure_details(error)
    assert details["category"] == category
    assert details["http_status"] == 400
    assert SECRET not in json.dumps(details)
    assert PRIVATE not in json.dumps(details)


def test_underlying_provider_error_captured_before_client_wrapping(capsys):
    error = errors.APIError(
        400,
        {
            "message": 'Unknown name "additionalProperties" at "generation_config.response_schema": Cannot find field. '
            + PRIVATE
        },
    )
    factory, sdk = sdk_factory(error=error)
    assert (
        run_source_diagnostic(
            GeminiConfig(api_key=SECRET), client_factory=factory, page_content=PRIVATE
        )
        == 1
    )
    output = capsys.readouterr().out
    assert "INVALID_SCHEMA" in output
    assert "additionalProperties" in output
    assert "Cannot find field" in output
    assert PRIVATE not in output
    assert SECRET not in output
    sdk.models.generate_content.assert_called_once()


def test_missing_key_never_attempts_request(capsys):
    factory = Mock()
    assert run_source_diagnostic(GeminiConfig(), client_factory=factory) == 1
    factory.assert_not_called()
    assert "request attempted: no" in capsys.readouterr().out
