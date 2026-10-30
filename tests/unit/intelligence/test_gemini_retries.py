import logging
import socket
import ssl
from types import SimpleNamespace
from unittest.mock import Mock, call

from google import genai
from google.genai.errors import APIError
import httpx
import pytest
import requests

from app.controlled_runner import BudgetedGemini, RunLimits
from app.intelligence import gemini_client
from app.intelligence.gemini_client import (
    GeminiAPIError,
    GeminiClient,
    GeminiConfig,
    GeminiConfigurationError,
    GeminiResponseError,
)
from app.intelligence.schemas import SourceAnalysis


PRIVATE = "private prompt/page/company; Authorization: Bearer SECRET_KEY"
SUCCESS = SimpleNamespace(
    parsed={"decision": "REVIEW", "confidence": 70, "reason": "Insufficient evidence"},
    text="ok",
)


def failure(status=503):
    return APIError(status, {"message": PRIVATE})


def make_client(outcomes, *, max_attempts=3, sleep=None, jitter=None):
    sdk = Mock()
    sdk.models.generate_content.side_effect = outcomes
    factory = Mock(return_value=sdk)
    sleep = sleep if sleep is not None else Mock()
    client = GeminiClient(
        GeminiConfig(api_key="SECRET_KEY"),
        client_factory=factory,
        max_attempts=max_attempts,
        sleep=sleep,
        jitter=jitter or (lambda: 0.125),
    )
    return client, sdk, sleep, factory


@pytest.fixture(autouse=True)
def retry_logs(monkeypatch, caplog):
    logger = logging.getLogger("app.intelligence.gemini_client")
    monkeypatch.setattr(logger, "disabled", False)
    caplog.set_level(logging.INFO, logger=logger.name)


@pytest.mark.parametrize("failures", [0, 1, 2])
def test_success_with_at_most_two_transient_failures(failures, caplog):
    client, sdk, sleep, factory = make_client(
        [failure() for _ in range(failures)] + [SUCCESS]
    )
    result = client.generate_structured(PRIVATE, SourceAnalysis)
    assert result.decision.value == "REVIEW"
    assert sdk.models.generate_content.call_count == failures + 1
    assert sleep.call_args_list == [call(delay) for delay in [1.125, 2.125][:failures]]
    assert factory.call_args.kwargs["http_options"].retry_options.attempts == 1
    assert f"attempt {failures + 1}/3 succeeded" in caplog.text
    if failures:
        assert "retrying=true" in caplog.text
    assert PRIVATE not in caplog.text
    assert "SECRET_KEY" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_three_failures_stop_without_fourth_attempt(caplog):
    client, sdk, sleep, _ = make_client([failure(), failure(), failure(), SUCCESS])
    with pytest.raises(GeminiAPIError) as caught:
        client.generate_structured(PRIVATE, SourceAnalysis)
    assert caught.value.diagnostic.http_status == 503
    assert caught.value.diagnostic.category == "MODEL_UNAVAILABLE"
    assert sdk.models.generate_content.call_count == 3
    assert sleep.call_count == 2
    assert "attempt 3/3 failed" in caplog.text
    assert "retrying=false" in caplog.text
    assert PRIVATE not in str(caught.value)


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_transient_http_statuses_retry(status):
    client, sdk, sleep, _ = make_client([failure(status), SUCCESS])
    assert client.generate_text(PRIVATE) == "ok"
    assert sdk.models.generate_content.call_count == 2
    sleep.assert_called_once()


@pytest.mark.parametrize(
    "error",
    [
        httpx.ReadTimeout(PRIVATE),
        requests.Timeout(PRIVATE),
        TimeoutError(PRIVATE),
        httpx.ConnectError(PRIVATE),
        httpx.ReadError(PRIVATE),
        httpx.RemoteProtocolError(PRIVATE),
        ConnectionResetError(PRIVATE),
        requests.ConnectionError(PRIVATE),
        socket.gaierror(socket.EAI_AGAIN, PRIVATE),
    ],
)
def test_temporary_network_interruptions_retry(error):
    client, sdk, sleep, _ = make_client([error, SUCCESS])
    client.generate_structured(PRIVATE, SourceAnalysis)
    assert sdk.models.generate_content.call_count == 2
    sleep.assert_called_once()


@pytest.mark.parametrize(
    "error",
    [
        failure(400),
        failure(401),
        failure(403),
        failure(404),
        failure(422),
        failure(501),
        APIError(400, {"message": "Invalid response_schema " + PRIVATE}),
        httpx.UnsupportedProtocol(PRIVATE),
        httpx.LocalProtocolError(PRIVATE),
        requests.exceptions.SSLError(PRIVATE),
        socket.gaierror(socket.EAI_NONAME, PRIVATE),
        ValueError(PRIVATE),
    ],
)
def test_deterministic_failures_never_retry(error):
    client, sdk, sleep, _ = make_client([error, SUCCESS])
    with pytest.raises(GeminiAPIError):
        client.generate_structured(PRIVATE, SourceAnalysis)
    assert sdk.models.generate_content.call_count == 1
    sleep.assert_not_called()


def test_certificate_failure_wrapped_as_connect_error_is_not_transient():
    error = httpx.ConnectError(PRIVATE)
    error.__cause__ = ssl.SSLCertVerificationError(PRIVATE)
    client, sdk, sleep, _ = make_client([error, SUCCESS])
    with pytest.raises(GeminiAPIError):
        client.generate_text(PRIVATE)
    assert sdk.models.generate_content.call_count == 1
    sleep.assert_not_called()


def test_invalid_structured_response_is_not_retried():
    client, sdk, sleep, _ = make_client(
        [SimpleNamespace(parsed=None, text=PRIVATE), SUCCESS]
    )
    with pytest.raises(GeminiResponseError):
        client.generate_structured(PRIVATE, SourceAnalysis)
    assert sdk.models.generate_content.call_count == 1
    sleep.assert_not_called()


def test_permanent_initialization_failure_not_retried():
    factory = Mock(side_effect=ValueError(PRIVATE))
    sleep = Mock()
    client = GeminiClient(
        GeminiConfig(api_key="SECRET_KEY"), client_factory=factory, sleep=sleep
    )
    with pytest.raises(GeminiConfigurationError):
        client.generate_text(PRIVATE)
    factory.assert_called_once()
    sleep.assert_not_called()


@pytest.mark.parametrize("limit", [1, 2, 3])
def test_configured_attempt_limit_is_honored(limit):
    client, sdk, sleep, _ = make_client([failure()] * 4, max_attempts=limit)
    with pytest.raises(GeminiAPIError):
        client.generate_structured(PRIVATE, SourceAnalysis)
    assert sdk.models.generate_content.call_count == limit
    assert sleep.call_count == limit - 1


@pytest.mark.parametrize("limit", [0, 4, -1, 100, True, 1.5])
def test_attempt_limit_cannot_exceed_hard_bounds(limit):
    with pytest.raises(ValueError):
        GeminiClient(GeminiConfig(), max_attempts=limit)


def test_default_sleep_and_jitter_can_be_patched(monkeypatch):
    sleeper = Mock()
    monkeypatch.setattr(gemini_client.time, "sleep", sleeper)
    monkeypatch.setattr(gemini_client.random, "uniform", lambda *_: 0.2)
    sdk = Mock()
    sdk.models.generate_content.side_effect = [failure(), failure(), SUCCESS]
    client = GeminiClient(
        GeminiConfig(api_key="SECRET_KEY"), client_factory=lambda **_: sdk
    )
    client.generate_structured(PRIVATE, SourceAnalysis)
    assert sleeper.call_args_list == [call(1.2), call(2.2)]


def test_jitter_is_bounded_even_when_injected():
    client, _, sleep, _ = make_client(
        [failure(), failure(), SUCCESS], jitter=lambda: 500
    )
    client.generate_structured(PRIVATE, SourceAnalysis)
    assert sleep.call_args_list == [call(1.25), call(2.25)]


def test_sdk_does_not_multiply_client_retries():
    requests_seen = []
    sleep = Mock()

    def handle(request):
        requests_seen.append(request)
        return httpx.Response(
            503,
            json={"error": {"code": 503, "status": "UNAVAILABLE", "message": PRIVATE}},
        )

    def factory(**kwargs):
        assert kwargs["http_options"].retry_options.attempts == 1
        kwargs["http_options"].client_args = {"transport": httpx.MockTransport(handle)}
        kwargs["vertexai"] = False
        return genai.Client(**kwargs)

    client = GeminiClient(
        GeminiConfig(api_key="SECRET_KEY"),
        client_factory=factory,
        sleep=sleep,
        jitter=lambda: 0,
    )
    try:
        with pytest.raises(GeminiAPIError):
            client.generate_structured(PRIVATE, SourceAnalysis)
    finally:
        client._get_client().close()
    assert len(requests_seen) == 3
    assert sleep.call_count == 2


def test_controlled_budget_counts_retry_attempts_and_reuses_only_success():
    client, sdk, _, _ = make_client([failure(), SUCCESS])
    budget = BudgetedGemini(client, RunLimits(ai_calls=2), emit=Mock())
    first = budget.generate_structured(PRIVATE, SourceAnalysis)
    assert budget.calls["SOURCE"] == 2
    assert budget.operations["SOURCE"] == 1
    assert budget.generate_structured(PRIVATE, SourceAnalysis) == first
    assert budget.cache_hits == 1
    assert sdk.models.generate_content.call_count == 2
    with pytest.raises(GeminiAPIError):
        budget.generate_structured("different input", SourceAnalysis)
    assert sdk.models.generate_content.call_count == 2


def test_controlled_budget_stops_retries_before_next_sdk_request():
    client, sdk, _, _ = make_client([failure(), failure(), SUCCESS])
    budget = BudgetedGemini(client, RunLimits(ai_calls=2), emit=Mock())
    with pytest.raises(GeminiAPIError):
        budget.generate_structured(PRIVATE, SourceAnalysis)
    assert sdk.models.generate_content.call_count == 2
    assert sum(budget.calls.values()) == 2
    assert all(result is None for result in budget.seen.values())


def test_failed_attempts_never_create_success_cache_and_future_run_can_retry():
    client, sdk, _, _ = make_client([failure(), failure(), failure(), SUCCESS])
    budget = BudgetedGemini(client, RunLimits(), emit=Mock())
    with pytest.raises(GeminiAPIError):
        budget.generate_structured(PRIVATE, SourceAnalysis)
    with pytest.raises(GeminiAPIError):
        budget.generate_structured(PRIVATE, SourceAnalysis)
    assert sdk.models.generate_content.call_count == 3
    assert all(result is None for result in budget.seen.values())
    later_run = BudgetedGemini(client, RunLimits(), emit=Mock())
    later_run.generate_structured(PRIVATE, SourceAnalysis)
    assert sdk.models.generate_content.call_count == 4
    assert later_run.calls["SOURCE"] == 1


def test_unique_source_limit_is_separate_from_retry_attempt_limit():
    client, sdk, _, _ = make_client([failure(), failure(), SUCCESS, SUCCESS])
    budget = BudgetedGemini(client, RunLimits(sources=1), emit=Mock())
    budget.generate_structured(PRIVATE, SourceAnalysis)
    assert budget.calls["SOURCE"] == 3
    with pytest.raises(GeminiAPIError):
        budget.generate_structured("another source", SourceAnalysis)
    assert sdk.models.generate_content.call_count == 3
