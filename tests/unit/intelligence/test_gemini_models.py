from types import SimpleNamespace
from unittest.mock import Mock

from google.genai import errors, types
from google.genai.pagers import Pager
import httpx
import pytest

from app.intelligence.gemini_client import GeminiConfig
from scripts.gemini_models import discover_models, generation_candidate


SECRET = "secret-test-api-key"
PRIVATE = "Authorization: Bearer " + SECRET + " confidential document/prompt"


def model(name="models/gemini-test-flash-lite", actions=None):
    return types.Model(
        name=name,
        supported_actions=actions if actions is not None else ["generateContent"],
        description=PRIVATE,
        display_name=PRIVATE,
    )


def test_lists_all_pages_without_generation_or_sensitive_metadata(capsys):
    next_page = Mock(
        return_value=types.ListModelsResponse(
            models=[model("models/gemini-test-flash"), model()], next_page_token=None
        )
    )
    pager = Pager(
        "models",
        next_page,
        types.ListModelsResponse(
            models=[model()], next_page_token="PRIVATE_PAGE_TOKEN"
        ),
        types.ListModelsConfig(page_size=1000, query_base=True),
    )
    sdk = Mock()
    sdk.models.list.return_value = pager
    factory = Mock(return_value=sdk)
    assert discover_models(GeminiConfig(api_key=SECRET), client_factory=factory) == 0
    output = capsys.readouterr().out
    assert output.count("capability=generateContent") == 2
    assert "model=models/gemini-test-flash-lite capability=generateContent" in output
    assert "Structured-output support is not exposed" in output
    for secret in (SECRET, PRIVATE, "PRIVATE_PAGE_TOKEN"):
        assert secret not in output
    sdk.models.generate_content.assert_not_called()
    sdk.models.list.assert_called_once()
    next_page.assert_called_once()
    sdk.close.assert_called_once()
    config = sdk.models.list.call_args.kwargs["config"]
    assert config.query_base is True
    options = factory.call_args.kwargs
    assert options["vertexai"] is False
    assert options["http_options"].retry_options.attempts == 1
    assert options["http_options"].timeout == 30_000


@pytest.mark.parametrize(
    "candidate",
    [
        model("models/gemini-test-flash-image"),
        model("models/gemini-test-flash-preview-tts"),
        model("models/gemini-test-native-audio"),
        model("models/gemini-embedding", ["embedContent"]),
        model("models/gemma-test"),
        model("tunedModels/private-company"),
        model("models/gemini-" + SECRET),
        model("models/gemini-bad\n" + PRIVATE),
        model(actions=[]),
        types.Model(name="models/gemini-unknown-capability"),
        types.Model(),
    ],
)
def test_filters_non_analysis_models_and_unverified_capabilities(candidate):
    assert generation_candidate(candidate, SECRET) is None


@pytest.mark.parametrize(
    "error,category,status",
    [
        (errors.APIError(403, {"message": PRIVATE}), "PERMISSION_ERROR", 403),
        (errors.APIError(429, {"message": "quota " + PRIVATE}), "QUOTA_EXCEEDED", 429),
        (errors.APIError(404, {"message": PRIVATE}), "UNKNOWN_PROVIDER_ERROR", 404),
        (httpx.ConnectError(PRIVATE), "NETWORK_ERROR", "unknown"),
    ],
)
def test_errors_are_sanitized_without_retry(error, category, status, capsys):
    sdk = Mock()
    sdk.models.list.side_effect = error
    assert (
        discover_models(
            GeminiConfig(api_key=SECRET), client_factory=Mock(return_value=sdk)
        )
        == 1
    )
    output = capsys.readouterr().out
    assert f"category={category}" in output
    assert f"http_status={status}" in output
    assert "listing incomplete" in output
    assert SECRET not in output
    assert PRIVATE not in output
    sdk.models.list.assert_called_once()
    sdk.models.generate_content.assert_not_called()
    sdk.close.assert_called_once()


def test_missing_key_never_constructs_sdk(capsys):
    factory = Mock()
    assert discover_models(GeminiConfig(), client_factory=factory) == 1
    factory.assert_not_called()
    assert "AUTHENTICATION_ERROR" in capsys.readouterr().out


def test_pagination_is_bounded_and_reports_incomplete(capsys):
    pager = SimpleNamespace(
        page=[model()], config={"page_token": PRIVATE}, next_page=Mock()
    )
    sdk = Mock()
    sdk.models.list.return_value = pager
    assert (
        discover_models(
            GeminiConfig(api_key=SECRET), client_factory=Mock(return_value=sdk)
        )
        == 1
    )
    assert pager.next_page.call_count == 9  # Initial page plus at most nine more.
    output = capsys.readouterr().out
    assert "Listing incomplete" in output
    assert PRIVATE not in output
    sdk.models.generate_content.assert_not_called()


def test_later_page_failure_does_not_claim_complete_listing(capsys):
    pager = SimpleNamespace(
        page=[model()],
        config={"page_token": PRIVATE},
        next_page=Mock(side_effect=httpx.ReadTimeout(PRIVATE)),
    )
    sdk = Mock()
    sdk.models.list.return_value = pager
    assert (
        discover_models(
            GeminiConfig(api_key=SECRET), client_factory=Mock(return_value=sdk)
        )
        == 1
    )
    output = capsys.readouterr().out
    assert "TIMEOUT" in output
    assert "listing incomplete" in output
    assert "model=models/" not in output
    assert PRIVATE not in output
    assert pager.next_page.call_count == 1


def test_cli_loads_environment_without_changing_model(monkeypatch):
    from scripts import gemini_models

    monkeypatch.setenv("GEMINI_API_KEY", SECRET)
    monkeypatch.setenv("GEMINI_MODEL", "keep-configured-model")
    loader = Mock()
    discover = Mock(return_value=0)
    monkeypatch.setattr(gemini_models, "load_dotenv", loader)
    monkeypatch.setattr(gemini_models, "discover_models", discover)
    assert gemini_models.main() == 0
    assert discover.call_args.args[0].model == "keep-configured-model"
    assert loader.call_args.kwargs["override"] is False
