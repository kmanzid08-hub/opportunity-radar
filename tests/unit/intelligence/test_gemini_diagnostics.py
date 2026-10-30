from types import SimpleNamespace
import logging
import traceback
from unittest.mock import Mock

from google.genai.errors import APIError
import httpx
import pytest
import requests

from app.intelligence.gemini_client import GeminiAPIError, GeminiClient, GeminiConfig
from app.intelligence.gemini_diagnostics import classify_gemini_error, safe_model_name
from app.intelligence.schemas import SourceAnalysis
from app.intelligence.gemini_schema import sanitize_gemini_schema
from scripts.gemini_diagnostic import ConnectionCheck, main, run_diagnostic


SECRET = "secret-diagnostic-api-key"
PRIVATE = "private prompt, page, company and Authorization: Bearer " + SECRET


@pytest.fixture(autouse=True)
def capture_diagnostic_logger(monkeypatch, caplog):
    # Earlier Alembic tests use fileConfig, which disables existing loggers.
    logger = logging.getLogger("app.intelligence.gemini_client")
    monkeypatch.setattr(logger, "disabled", False)
    caplog.set_level(logging.WARNING, logger=logger.name)


def test_diagnostic_schema_is_accepted_by_installed_sdk():
    from google.genai import _transformers

    schema = _transformers.t_schema(SimpleNamespace(vertexai=False), ConnectionCheck)
    assert schema.properties["ok"].type.value == "BOOLEAN"


def provider_error(code, status=None, *, reason=None, message=PRIVATE, details=None):
    return APIError(
        code,
        {
            "error": {
                "status": status,
                "message": message,
                "details": details or ([{"reason": reason}] if reason else []),
            }
        },
    )


@pytest.mark.parametrize(
    "error,category,status,retryable",
    [
        (provider_error(401), "AUTHENTICATION_ERROR", 401, False),
        (
            provider_error(400, reason="API_KEY_INVALID"),
            "AUTHENTICATION_ERROR",
            400,
            False,
        ),
        (
            provider_error(400, message="API key not valid. " + PRIVATE),
            "AUTHENTICATION_ERROR",
            400,
            False,
        ),
        (provider_error(403), "PERMISSION_ERROR", 403, False),
        (provider_error(429), "RATE_LIMIT", 429, True),
        (provider_error(429, reason="QUOTA_EXCEEDED"), "QUOTA_EXCEEDED", 429, True),
        (
            provider_error(429, message="You exceeded your current quota. " + PRIVATE),
            "QUOTA_EXCEEDED",
            429,
            True,
        ),
        (
            provider_error(
                429, details=[{"@type": "type.googleapis.com/google.rpc.QuotaFailure"}]
            ),
            "QUOTA_EXCEEDED",
            429,
            True,
        ),
        (provider_error(503), "MODEL_UNAVAILABLE", 503, True),
        (provider_error(404), "MODEL_NOT_FOUND", 404, False),
        (provider_error(400), "INVALID_REQUEST", 400, False),
        (provider_error(500), "UNKNOWN_PROVIDER_ERROR", 500, True),
        (provider_error(504), "TIMEOUT", 504, True),
        (httpx.ConnectError(PRIVATE), "NETWORK_ERROR", None, True),
        (requests.ConnectionError(PRIVATE), "NETWORK_ERROR", None, True),
        (httpx.ReadTimeout(PRIVATE), "TIMEOUT", None, True),
        (requests.Timeout(PRIVATE), "TIMEOUT", None, True),
        (TimeoutError(PRIVATE), "TIMEOUT", None, True),
        (RuntimeError(PRIVATE), "UNKNOWN_PROVIDER_ERROR", None, False),
    ],
)
def test_classification_and_safe_source_logs(
    error, category, status, retryable, caplog
):
    diagnostic = classify_gemini_error(error)
    assert (diagnostic.category, diagnostic.http_status, diagnostic.retryable) == (
        category,
        status,
        retryable,
    )
    models = Mock()
    models.generate_content.side_effect = error
    client = GeminiClient(
        GeminiConfig(api_key=SECRET),
        client_factory=Mock(return_value=SimpleNamespace(models=models)),
        max_attempts=1,
    )
    with pytest.raises(GeminiAPIError) as caught:
        client.generate_structured(PRIVATE, SourceAnalysis)
    assert caught.value.diagnostic == diagnostic
    assert "Gemini SOURCE attempt 1/1 failed" in caplog.text
    assert f"category={category}" in caplog.text
    assert f"retryable={str(retryable).lower()}" in caplog.text
    formatted = "".join(traceback.format_exception(caught.value))
    for sensitive in (SECRET, PRIVATE, "Authorization", "Bearer"):
        assert sensitive not in caplog.text
        assert sensitive not in formatted
    assert len(caplog.records) == 1
    assert caplog.records[0].exc_info is None
    assert models.generate_content.call_count == 1


@pytest.mark.parametrize(
    "model", [SECRET, "models/" + SECRET, "bad\nmodel", "?key=x", "AIzaAnything"]
)
def test_model_log_field_cannot_echo_key_or_inject_lines(model):
    assert safe_model_name(model, SECRET) == "[redacted]"


def test_valid_model_identifier_is_preserved():
    assert safe_model_name("gemini-2.5-flash-lite", SECRET) == "gemini-2.5-flash-lite"


def diagnostic_factory(*, error=None, response=None):
    sdk = Mock()
    sdk.models.generate_content.side_effect = error
    sdk.models.generate_content.return_value = response or SimpleNamespace(
        parsed={"ok": True, "message": "Gemini connection successful"}
    )
    return Mock(return_value=sdk), sdk


def test_command_success_exactly_one_trivial_request_and_no_retry(capsys):
    factory, sdk = diagnostic_factory()
    assert (
        run_diagnostic(
            GeminiConfig(api_key=SECRET, model="custom-model"), client_factory=factory
        )
        == 0
    )
    assert sdk.models.generate_content.call_count == 1
    request = sdk.models.generate_content.call_args.kwargs
    assert request["model"] == "custom-model"
    assert request["config"].response_schema == sanitize_gemini_schema(
        ConnectionCheck.model_json_schema()
    )
    assert (
        request["contents"]
        == 'Return only {"ok": true, "message": "Gemini connection successful"}.'
    )
    options = factory.call_args.kwargs["http_options"]
    assert options.retry_options.attempts == 1
    assert options.timeout == 30_000
    sdk.close.assert_called_once()
    output = capsys.readouterr().out
    assert "key configured: yes" in output
    assert "model configured: custom-model" in output
    assert "request attempted: yes" in output
    assert "result: success" in output
    assert "category: NONE" in output
    assert "http_status: 200" in output
    assert SECRET not in output
    assert request["contents"] not in output


@pytest.mark.parametrize(
    "error,category",
    [
        (provider_error(503), "MODEL_UNAVAILABLE"),
        (provider_error(429, reason="QUOTA_EXCEEDED"), "QUOTA_EXCEEDED"),
        (httpx.ConnectError(PRIVATE), "NETWORK_ERROR"),
    ],
)
def test_command_failure_never_retries_or_prints_provider_body(
    error, category, capsys, caplog
):
    factory, sdk = diagnostic_factory(error=error)
    assert run_diagnostic(GeminiConfig(api_key=SECRET), client_factory=factory) == 1
    assert sdk.models.generate_content.call_count == 1
    sdk.close.assert_called_once()
    output = capsys.readouterr().out + caplog.text
    assert "request attempted: yes" in output
    assert "result: failure" in output
    assert f"category: {category}" in output
    assert SECRET not in output
    assert PRIVATE not in output


@pytest.mark.parametrize(
    "response",
    [
        SimpleNamespace(parsed=None, text=PRIVATE),
        SimpleNamespace(parsed={"ok": False, "message": PRIVATE}),
        SimpleNamespace(parsed=None, text=None),
    ],
)
def test_structured_output_failure_is_classified_without_echoing_content(
    response, capsys, caplog
):
    factory, sdk = diagnostic_factory(response=response)
    assert run_diagnostic(GeminiConfig(api_key=SECRET), client_factory=factory) == 1
    assert sdk.models.generate_content.call_count == 1
    output = capsys.readouterr().out + caplog.text
    assert "STRUCTURED_OUTPUT_ERROR" in output
    assert "request attempted: yes" in output
    assert SECRET not in output
    assert PRIVATE not in output


def test_missing_key_does_not_construct_client_or_attempt_request(capsys, caplog):
    factory = Mock()
    assert run_diagnostic(GeminiConfig(), client_factory=factory) == 1
    factory.assert_not_called()
    output = capsys.readouterr().out
    assert "key configured: no" in output
    assert "request attempted: no" in output
    assert "category: AUTHENTICATION_ERROR" in output


def test_initialization_failure_does_not_claim_request_attempted(capsys, caplog):
    factory = Mock(side_effect=RuntimeError(PRIVATE))
    assert run_diagnostic(GeminiConfig(api_key=SECRET), client_factory=factory) == 1
    factory.assert_called_once()
    output = capsys.readouterr().out + caplog.text
    assert "request attempted: no" in output
    assert "UNKNOWN_PROVIDER_ERROR" in output
    assert SECRET not in output
    assert PRIVATE not in output


def test_cli_reads_configured_environment_without_database_or_webpage(monkeypatch):
    from scripts import gemini_diagnostic

    load = Mock()
    run = Mock(return_value=0)
    monkeypatch.setattr(gemini_diagnostic, "load_dotenv", load)
    monkeypatch.setattr(gemini_diagnostic, "run_diagnostic", run)
    monkeypatch.setenv("GEMINI_API_KEY", SECRET)
    monkeypatch.setenv("GEMINI_MODEL", "configured-model")
    assert main() == 0
    config = run.call_args.args[0]
    assert config.api_key == SECRET
    assert config.model == "configured-model"
    assert load.call_args.kwargs["override"] is False
