from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import select

from app.beta_models import BetaUsage
from app.beta_scope import Principal, ai_attempts, principal
from app.beta_security import reserve_ai_attempt, take_limit, validate_production
from app.beta_submission import PinnedSession
from app.intelligence.gemini_client import (
    GeminiClient,
    GeminiConfig,
    GeminiRequestBudgetError,
)


def test_provider_attempt_budget_is_enforced_at_the_real_client_boundary(
    isolated_session_factory,
):
    from google.genai import types

    sdk = SimpleNamespace(
        models=SimpleNamespace(generate_content=Mock(return_value=object()))
    )
    client = GeminiClient(
        GeminiConfig(api_key="offline-test"), client_factory=lambda **_: sdk
    )
    user_token = principal.set(Principal(1, 7, "test@example.test", False))
    budget_token = ai_attempts.set(0)
    try:
        for _ in range(9):
            client._generate("Offline test", config=types.GenerateContentConfig())
        with pytest.raises(GeminiRequestBudgetError):
            client._generate("Offline test", config=types.GenerateContentConfig())
        assert sdk.models.generate_content.call_count == 9
        # A new request does not reset the workspace's durable daily quota.
        ai_attempts.set(0)
        with isolated_session_factory() as db:
            usage = db.scalar(select(BetaUsage))
            assert usage.count == 9
            usage.count = 90
            db.commit()
        with pytest.raises(GeminiRequestBudgetError):
            reserve_ai_attempt()
        assert sdk.models.generate_content.call_count == 9
    finally:
        principal.reset(user_token)
        ai_attempts.reset(budget_token)


def test_limits_are_persistent_and_expired_counters_are_removed(
    isolated_session_factory,
):
    from app.beta_security import utcnow

    with isolated_session_factory() as db:
        db.add(
            BetaUsage(key="expired", count=99, expires_at=utcnow() - timedelta(days=1))
        )
        db.commit()
    assert take_limit("test-limit", 1)
    assert not take_limit("test-limit", 1)
    with isolated_session_factory() as db:
        assert db.get(BetaUsage, "expired") is None
        assert len(list(db.scalars(select(BetaUsage)))) == 1


def test_pinned_fetch_never_connects_to_a_second_private_dns_resolution(monkeypatch):
    from app.controlled_network import BoundedWeb
    from app.source_fetch_diagnostics import SourceFetchError

    monkeypatch.setattr(BoundedWeb, "validate_url", staticmethod(lambda url: None))
    monkeypatch.setattr(
        "socket.getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", ("127.0.0.1", 443))]
    )
    send = Mock(side_effect=AssertionError("No private network request"))
    monkeypatch.setattr("requests.Session.send", send)
    with PinnedSession() as session:
        with pytest.raises(SourceFetchError):
            session.get("https://public.example.test/notice", allow_redirects=False)
    send.assert_not_called()


def test_pinned_https_preserves_tls_verification_and_disables_redirects(monkeypatch):
    from app.controlled_network import BoundedWeb

    monkeypatch.setattr(BoundedWeb, "validate_url", staticmethod(lambda url: None))
    monkeypatch.setattr(
        "socket.getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", ("8.8.8.8", 443))]
    )
    get = Mock(return_value=object())
    monkeypatch.setattr("requests.Session.get", get)
    with PinnedSession() as session:
        session.get(
            "https://public.example.test/notice", allow_redirects=False, stream=True
        )
        args, kwargs = get.call_args
        assert args[0] == "https://8.8.8.8/notice"
        assert kwargs["headers"]["Host"] == "public.example.test"
        assert kwargs["allow_redirects"] is False
        pool_options = session.get_adapter("https://").poolmanager.connection_pool_kw
        assert pool_options["assert_hostname"] == "public.example.test"
        assert pool_options["server_hostname"] == "public.example.test"
        assert "verify" not in kwargs  # requests' secure default remains enabled.


def test_production_fails_closed_for_missing_origin_or_unsafe_runtime(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("PUBLIC_ORIGIN", raising=False)
    with pytest.raises(RuntimeError, match="PUBLIC_ORIGIN"):
        validate_production()
    monkeypatch.setenv("PUBLIC_ORIGIN", "https://beta.example.test")
    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: SimpleNamespace(
            enable_internal_scheduler=True, database_url="postgresql+psycopg://unused"
        ),
    )
    with pytest.raises(RuntimeError, match="scheduler"):
        validate_production()
    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: SimpleNamespace(
            enable_internal_scheduler=False, database_url="sqlite:///unused.db"
        ),
    )
    with pytest.raises(RuntimeError, match="PostgreSQL"):
        validate_production()


def test_selected_source_fetch_uses_three_page_and_same_domain_crawl_limits(
    monkeypatch,
):
    from app.beta_submission import fetch_source_candidates
    from app.controlled_network import BoundedWeb
    from app.models import Source

    visited = []

    class OfflineResponse:
        status_code = 200
        headers = {"Content-Type": "text/html"}
        encoding = "utf-8"

        def __init__(self, url):
            self.url = url

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def iter_content(self, size):
            if self.url.endswith("robots.txt"):
                yield b"User-agent: *\nAllow: /"
            else:
                yield b'<html><title>Procurement notice</title><main>Qualified firms invited to submit proposals for controls reviews. Deadline December 2099.<a href="/tender-a">Tender controls review</a><a href="/tender-b">Request for proposals</a><a href="/tender-c">Tender services</a><a href="http://127.0.0.1/private">Tender private</a></main></html>'

    def get(self, url, **kwargs):
        visited.append(url)
        assert kwargs["allow_redirects"] is False
        return OfflineResponse(url)

    monkeypatch.setattr(BoundedWeb, "validate_url", staticmethod(lambda url: None))
    monkeypatch.setattr(PinnedSession, "get", get)
    source = Source(
        monitor_url="https://agency.example.test/notices",
        base_url="https://agency.example.test/",
        domain="agency.example.test",
    )
    candidates = fetch_source_candidates(source)
    assert len(candidates) == 3
    assert len(visited) == 4  # One robots request and three pages.
    assert all(url.startswith("https://agency.example.test/") for url in visited)
