from unittest.mock import Mock
from types import SimpleNamespace

import pytest
from google.genai.errors import APIError
from sqlalchemy import func, select

from app import targeted_runner
from app.controlled_network import BoundedWeb
from app.controlled_runner import RunLimits
from app.intelligence import matching_engine, opportunity_pipeline
from app.intelligence.gemini_client import GeminiClient, GeminiConfig
from app.intelligence.schemas import OpportunityAnalysis
from app.models import Opportunity, CompanyOpportunityReadiness
from app.targeted_runner import run_targeted, validate_targets
from app.source_fetch_diagnostics import SourceFetchError

from test_controlled_runner import FakeGemini, runner_setup as _runner_setup


runner_setup = _runner_setup


URL = "https://public.example.test/notice"


def test_targeted_replay_real_components_cache_dedup_and_no_readiness(
    runner_setup, isolated_session_factory, monkeypatch
):
    discovery = Mock(side_effect=AssertionError("Discovery forbidden"))
    monkeypatch.setattr("app.source_discovery.SourceDiscovery._search", discovery)
    backend = FakeGemini()
    lines = []
    first = run_targeted(backend=backend, urls=[URL], emit=lines.append)
    second = run_targeted(backend=backend, urls=[URL], emit=lines.append)
    assert first["run_success"] and second["run_success"]
    item = first["targets"][0]
    assert item["classification"] == "VERIFIED"
    assert (
        item["opportunity_version"] == opportunity_pipeline.OPPORTUNITY_ANALYSIS_VERSION
    )
    assert len(item["opportunity_fingerprint"]) == 64
    assert len(item["matches"]) == 2
    assert len(item["triage"]) == 2
    assert item["opportunity_id"] == second["targets"][0]["opportunity_id"]
    assert second["budget_used"] == 0 and second["cache_hits"] == 5
    assert set(backend.calls) == {"OpportunityAnalysis", "CompanyMatch", "TriageResult"}
    assert all(url == URL or url.endswith("robots.txt") for url in runner_setup)
    assert "CONFIDENTIAL_PROFILE" not in "\n".join(lines)
    assert "PRIVATE_MATCH_REASON" not in "\n".join(lines)
    discovery.assert_not_called()
    with isolated_session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Opportunity)) == 1
        assert (
            db.scalar(select(func.count()).select_from(CompanyOpportunityReadiness))
            == 0
        )
    monkeypatch.setattr(matching_engine, "MATCH_ANALYSIS_VERSION", "test-next-version")
    third = run_targeted(backend=backend, urls=[URL], emit=lambda _: None)
    assert third["attempts"] == {"MATCH": 2, "TRIAGE": 2}
    assert third["targets"][0]["opportunity_id"] == item["opportunity_id"]


def test_targeted_budget_and_company_limits(runner_setup):
    backend = FakeGemini()
    report = run_targeted(
        backend=backend, urls=[URL], limits=RunLimits(ai_calls=1), emit=lambda _: None
    )
    assert report["budget_used"] == 1 and not report["run_success"]
    assert backend.calls == ["OpportunityAnalysis"]
    with pytest.raises(ValueError):
        run_targeted(backend=backend, urls=[URL], company_ids=[1, 2, 3])
    with pytest.raises(ValueError):
        run_targeted(backend=backend, urls=[URL], limits=RunLimits(ai_calls=21))


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.test/file",
        "https://user:secret@example.test/",
        "http://example.test:8080/",
        "https://example.test/#fragment",
    ],
)
def test_targeted_syntax_rejects_unsafe_urls(url):
    with pytest.raises(ValueError):
        validate_targets([url])


def test_targeted_public_network_guard_before_any_fetch(runner_setup, monkeypatch):
    def blocked(url):
        raise SourceFetchError("BLOCKED")

    monkeypatch.setattr(BoundedWeb, "validate_url", staticmethod(blocked))
    backend = FakeGemini()
    report = run_targeted(
        backend=backend, urls=["http://127.0.0.1/private"], emit=lambda _: None
    )
    assert not report["run_success"]
    assert report["targets"][0]["error"] == "BLOCKED"
    assert not runner_setup and not backend.calls


def test_targeted_legitimate_rejection_no_downstream(runner_setup):
    backend = FakeGemini(opportunity_decision="REJECT")
    report = run_targeted(backend=backend, urls=[URL], emit=lambda _: None)
    assert report["run_success"]
    assert backend.calls == ["OpportunityAnalysis"]
    assert report["targets"][0]["matches"] == []


def test_targeted_max_urls_and_duplicates():
    with pytest.raises(ValueError):
        validate_targets([URL] * 6)
    with pytest.raises(ValueError):
        validate_targets([URL, URL])


def test_targeted_no_unbounded_scan_entrypoints(runner_setup, monkeypatch):
    blocked = Mock(side_effect=AssertionError("Scan forbidden"))
    monkeypatch.setattr(targeted_runner.BoundedCrawler, "_scan_source", blocked)
    assert run_targeted(
        backend=FakeGemini(), urls=[URL], company_ids=[1], emit=lambda _: None
    )["run_success"]
    blocked.assert_not_called()


@pytest.mark.parametrize("budget,expected_attempts,success", [(1, 1, False), (2, 2, True)])
def test_targeted_central_retries_count_against_budget(runner_setup, budget, expected_attempts, success):
    sdk = Mock()
    rejected = FakeGemini(opportunity_decision="REJECT").generate_structured("", OpportunityAnalysis)
    sdk.models.generate_content.side_effect = [APIError(503, {"message": "private provider detail"}), SimpleNamespace(parsed=rejected.model_dump(mode="json"))]
    sleep = Mock()
    backend = GeminiClient(GeminiConfig(api_key="SECRET_TEST_KEY"), client_factory=lambda **kwargs: sdk, sleep=sleep, jitter=lambda: 0)
    report = run_targeted(backend=backend, urls=[URL], limits=RunLimits(ai_calls=budget), emit=lambda _: None)
    assert sdk.models.generate_content.call_count == expected_attempts
    assert report["run_success"] is success
    assert report["budget_used"] == expected_attempts
    assert report["targets"][0]["retries"]["OPPORTUNITY"] == expected_attempts - 1


@pytest.mark.parametrize("status,content_type,oversized", [(302, "text/html", False), (200, "application/pdf", False), (200, "text/html", True)])
def test_targeted_reuses_redirect_content_type_and_size_guards(runner_setup, monkeypatch, status, content_type, oversized):
    from test_controlled_runner import HTMLResponse

    def get(_session, url, **kwargs):
        assert kwargs["allow_redirects"] is False
        response = HTMLResponse(url)
        if not url.endswith("robots.txt"):
            response.status_code = status
            response.headers = {"Content-Type": content_type}
            if oversized:
                response.iter_content = lambda _: iter([b"x" * 2_000_001])
        return response

    monkeypatch.setattr("requests.sessions.Session.get", get)
    backend = FakeGemini()
    report = run_targeted(backend=backend, urls=[URL], emit=lambda _: None)
    assert not report["run_success"]
    assert not backend.calls


def test_targeted_preflight_does_not_construct_ai_or_fetch(monkeypatch, tmp_path):
    import sys
    from scripts import controlled_live_test
    from app import schema_status
    from app.core.config import clear_settings_cache

    monkeypatch.setattr(sys, "argv", ["targeted_live_test", "--database", "test.sqlite", "--url", URL])
    monkeypatch.setattr(controlled_live_test, "sys", SimpleNamespace(modules={}))
    monkeypatch.setattr(controlled_live_test, "local_database_path", lambda _: tmp_path / "test.sqlite")
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("ENABLE_INTERNAL_SCHEDULER", "false")
    monkeypatch.setattr(schema_status, "inspect_schema", lambda _: SimpleNamespace(state=schema_status.SchemaState.CURRENT, expected_revision="test-head"))
    monkeypatch.setattr("app.controlled_runner.selected_profiles", lambda *args: [object()])
    forbidden = Mock(side_effect=AssertionError("No execution during preflight"))
    monkeypatch.setattr(GeminiClient, "from_environment", forbidden)
    monkeypatch.setattr(BoundedWeb, "fetch", forbidden)
    try:
        assert controlled_live_test.main(targeted=True) == 0
        forbidden.assert_not_called()
    finally:
        clear_settings_cache()
