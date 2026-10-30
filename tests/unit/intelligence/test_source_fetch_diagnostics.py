import socket
from unittest.mock import Mock

import pytest
import requests
from sqlalchemy import select

from app.controlled_network import BoundedWeb
from app.models import Source
from app.source_fetch_diagnostics import (
    SourceFetchError,
    classify_fetch_failure,
    report_fetch_failure,
)

from .test_source_discovery_integration import _analysis, _candidate, _discovery


@pytest.mark.parametrize(
    "error, category, status",
    [
        (requests.Timeout("SECRET URL"), "TIMEOUT", None),
        (socket.gaierror("SECRET HOST"), "DNS_NETWORK_ERROR", None),
        (requests.ConnectionError("SECRET CONTENT"), "DNS_NETWORK_ERROR", None),
        (SourceFetchError("HTTP_ERROR", 503), "HTTP_ERROR", 503),
        (SourceFetchError("BLOCKED", 403), "BLOCKED", 403),
        (SourceFetchError("EMPTY_CONTENT", 200), "EMPTY_CONTENT", 200),
        (SourceFetchError("UNSUPPORTED_CONTENT", 200), "UNSUPPORTED_CONTENT", 200),
        (ValueError("SECRET"), "FETCH_ERROR", None),
        (SourceFetchError("SECRET"), "FETCH_ERROR", None),
    ],
)
def test_fetch_diagnostics_never_serialize_sensitive_exceptions(
    error, category, status, caplog, monkeypatch
):
    from app import source_fetch_diagnostics

    monkeypatch.setattr(source_fetch_diagnostics.logger, "disabled", False)
    failure = classify_fetch_failure(error)
    assert (failure.category, failure.http_status) == (category, status)
    summary = report_fetch_failure(error)
    assert category in summary
    assert category in caplog.text
    assert "SECRET" not in summary + caplog.text


@pytest.mark.parametrize("status, category", [(403, "BLOCKED"), (503, "HTTP_ERROR")])
def test_http_exception_retains_status_without_body(status, category):
    response = requests.Response()
    response.status_code = status
    error = requests.HTTPError("SECRET BODY", response=response)
    assert classify_fetch_failure(error).category == category
    assert classify_fetch_failure(error).http_status == status


def test_source_failure_retains_safe_status_and_next_candidate_can_succeed(
    monkeypatch, isolated_session_factory
):
    analyzer = Mock(return_value=_analysis("ACCEPT"))
    discovery = _discovery(
        monkeypatch,
        isolated_session_factory,
        analyzer=analyzer,
        content_fetcher=Mock(
            side_effect=[SourceFetchError("BLOCKED", 403), "A procurement page"]
        ),
    )
    assert discovery._save_candidate(_candidate()).status == "ERROR"
    with isolated_session_factory() as db:
        source = db.scalar(select(Source))
        assert "category=BLOCKED http_status=403" in source.ai_reason
    outcome = discovery._save_candidate(
        _candidate(monitor_url="https://another.example.test/notices")
    )
    assert outcome.status == "ACCEPT"
    analyzer.assert_called_once()


@pytest.mark.parametrize(
    "status, content_type, text, category",
    [
        (503, "text/html", "SECRET", "HTTP_ERROR"),
        (403, "text/html", "SECRET", "BLOCKED"),
        (200, "application/pdf", "SECRET", "UNSUPPORTED_CONTENT"),
        (200, "text/html", "<script>SECRET</script>", "EMPTY_CONTENT"),
    ],
)
def test_default_source_fetch_classifies_status_and_content(
    monkeypatch, isolated_session_factory, status, content_type, text, category
):
    discovery = _discovery(
        monkeypatch, isolated_session_factory, analyzer=Mock(), content_fetcher=Mock()
    )
    discovery.content_session = Mock()
    discovery.content_session.get.return_value = Mock(
        ok=status == 200,
        status_code=status,
        headers={"Content-Type": content_type},
        text=text,
    )
    with pytest.raises(SourceFetchError) as captured:
        discovery._fetch_source_content("https://example.test/")
    assert classify_fetch_failure(captured.value).category == category
    assert classify_fetch_failure(captured.value).http_status == status


def test_bounded_fetch_preserves_http_status_without_retry(monkeypatch):
    web = BoundedWeb(3, 5)
    monkeypatch.setattr(web, "validate_url", lambda url: None)
    response = Mock(status_code=504)
    session = Mock()
    session.get.return_value.__enter__ = Mock(return_value=response)
    session.get.return_value.__exit__ = Mock(return_value=False)
    web.session = session
    with pytest.raises(SourceFetchError) as captured:
        web.fetch("https://example.test/", "SOURCE")
    assert classify_fetch_failure(captured.value).http_status == 504
    assert web.attempts["SOURCE"] == 1
    session.get.assert_called_once()
