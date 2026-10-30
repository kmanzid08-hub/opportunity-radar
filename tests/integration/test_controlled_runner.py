from contextlib import AbstractContextManager
import json
import re
import sys
from types import SimpleNamespace
from unittest.mock import Mock

from google.genai.errors import APIError
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app import scanner, source_discovery
from app.controlled_network import BoundedWeb, BoundedCrawler, display_url
from app.controlled_runner import BudgetedGemini, RunLimits, run_controlled
from app.intelligence import matching_engine, opportunity_pipeline
from app.intelligence.gemini_client import GeminiAPIError, GeminiClient, GeminiConfig
from app.intelligence.schemas import SourceAnalysis
from app.models import (
    CompanyOpportunityMatch,
    CompanyOpportunityReadiness,
    CompanyOpportunityTriage,
    Opportunity,
    OpportunityPreference,
    Organization,
    Source,
)
from app.source_discovery import SearchResult, SourceDiscovery
from scripts.controlled_live_test import local_database_path


class HTMLResponse(AbstractContextManager):
    status_code = 200
    headers = {"Content-Type": "text/html"}
    encoding = "utf-8"

    def __init__(self, url):
        self.url = url

    def __exit__(self, *args):
        return False

    def iter_content(self, _size):
        if self.url.endswith("robots.txt"):
            yield b"User-agent: *\nAllow: /"
        elif self.url.endswith("/notice"):
            yield b"<html><title>Independent controls review</title><main><h1>Independent controls review</h1><p>Qualified firms are invited to submit proposals for a detailed review of financial controls. The proposal deadline is 31 December 2099. A tax clearance certificate is required.</p></main></html>"
        else:
            yield b"<html><title>Current procurement notices</title><main><h1>Public procurement listing</h1><p>LISTING_ONLY: this page lists current public invitations for consulting services, procurement notices and tender documents.</p><a href='/notice'>Request for proposals: Independent controls review</a></main></html>"


class FakeGemini:
    def __init__(
        self, failure=None, source_decision="ACCEPT", opportunity_decision="ACCEPT"
    ):
        self.failure = failure
        self.source_decision = source_decision
        self.opportunity_decision = opportunity_decision
        self.calls = []

    def generate_structured(self, prompt, response_schema, **kwargs):
        name = response_schema.__name__
        self.calls.append(name)
        if self.failure in {name, "ALL"}:
            raise GeminiAPIError("SECRET_PROVIDER_DETAIL")
        if name == "SourceAnalysis":
            values = dict(
                decision=self.source_decision,
                confidence=96,
                source_name="Public Authority",
                organisation="Public Authority",
                country="Kenya",
                is_official=True,
                publishes_opportunities=True,
                reason="A public opportunity source.",
            )
        elif name == "OpportunityAnalysis":
            listing = "LISTING_ONLY" in prompt
            values = dict(
                decision="REJECT" if listing else self.opportunity_decision,
                confidence=95,
                reason="Source listing" if listing else "A specific procurement notice",
                title="Public procurement listing"
                if listing
                else "Independent controls review",
                buyer_name="Public Buyer",
                issuing_organisation="Public Buyer",
                country="Kenya",
                opportunity_type="DIRECTORY" if listing else "CONSULTANCY",
                status="ACTIVE",
                deadline="2099-12-31",
                summary="Independent review of financial controls.",
                scope="Review internal financial control systems.",
                required_documents=["Tax clearance"],
            )
        elif name == "CompanyMatch":
            company_id = int(re.search(r"Company ID: (\d+)", prompt).group(1))
            values = dict(
                company_id=company_id,
                score=91 if company_id == 1 else 82,
                match_strength="STRONG",
                explanation="PRIVATE_MATCH_REASON",
                recommendation="PRIVATE_ACTION",
            )
        elif name == "TriageResult":
            values = dict(
                priority="HIGH",
                priority_score=85,
                bid_readiness="UNKNOWN",
                urgency="LOW",
                estimated_effort="UNKNOWN",
                key_reasons=["Private company reasoning"],
                critical_gaps=[],
                eligibility_risks=[],
                recommended_next_action="PRIVATE_ACTION",
                budget_evidence="UNKNOWN",
                deadline_evidence="CONFIRMED",
                eligibility_evidence="UNKNOWN",
                experience_evidence="UNKNOWN",
                exclusion_applies=False,
                required_documents=[
                    {"name": "Tax clearance", "category": "LEGAL_REGISTRATION"}
                ],
            )
        elif name == "RequirementExtraction":
            values = {
                "requirements": [
                    dict(
                        category="TAX",
                        requirement="Tax clearance",
                        source_field="required_documents",
                        evidence="Tax clearance",
                        required=True,
                        critical=False,
                        requires_document=True,
                        confidence=95,
                    )
                ]
            }
        elif name == "ReadinessAnalysis":
            data = json.loads(prompt)
            values = {
                "items": [
                    dict(
                        requirement_id=item["id"],
                        status="NEEDS_CONFIRMATION",
                        availability="UNKNOWN",
                        confidence=90,
                        notes="Document availability is not known.",
                        next_action="Confirm certificate availability.",
                        document_ids=[],
                    )
                    for item in data["requirements"]
                ]
            }
        else:
            raise AssertionError(name)
        return response_schema.model_validate(values)


@pytest.fixture
def runner_setup(monkeypatch, isolated_session_factory):
    for module in (source_discovery, scanner, matching_engine, opportunity_pipeline):
        monkeypatch.setattr(module, "SessionLocal", isolated_session_factory)
    monkeypatch.setenv("BRAVE_API_KEY", "SECRET_SEARCH_KEY")
    monkeypatch.setattr(
        SourceDiscovery,
        "_search",
        lambda self, query: [
            SearchResult(
                "Public procurement",
                "https://public.example.test/tenders",
                "Official public notices",
            )
        ],
    )
    monkeypatch.setattr(BoundedWeb, "validate_url", staticmethod(lambda url: None))
    fetches = []

    def get(_session, url, **kwargs):
        assert kwargs["allow_redirects"] is False
        fetches.append(url)
        return HTMLResponse(url)

    monkeypatch.setattr("requests.sessions.Session.get", get)
    monkeypatch.setattr(BoundedCrawler, "_wait_between_requests", lambda self: None)
    with isolated_session_factory() as db:
        for index in range(2):
            company = Organization(
                name=f"Company {index}",
                country="Kenya",
                description="CONFIDENTIAL_PROFILE",
                is_active=True,
            )
            db.add(company)
            db.flush()
            db.add(
                OpportunityPreference(
                    organization_id=company.id,
                    countries=["Kenya"],
                    services=["Assurance"],
                )
            )
        db.commit()
    return fetches


def execute(backend=None, **kwargs):
    return run_controlled(
        backend=backend or FakeGemini(),
        limits=kwargs.pop("limits", RunLimits(1, 3, 2, 20)),
        **kwargs,
    )


@pytest.fixture
def command_exit(monkeypatch, tmp_path):
    """Exercise the actual CLI exit decision without another run or real preflight."""
    from app import controlled_runner, schema_status
    from app.core.config import clear_settings_cache
    from scripts import controlled_live_test

    def invoke(report):
        with monkeypatch.context() as patcher:
            patcher.setattr(
                sys,
                "argv",
                ["controlled_live_test", "--database", "test.sqlite", "--execute"],
            )
            # The CLI requires a fresh process in production. This test already
            # imported the app; bypass only that guard, not global sys.modules.
            patcher.setattr(controlled_live_test, "sys", SimpleNamespace(modules={}))
            patcher.setattr(
                controlled_live_test,
                "local_database_path",
                lambda _: tmp_path / "test.sqlite",
            )
            patcher.setattr("dotenv.load_dotenv", lambda *args, **kwargs: None)
            patcher.delenv("RENDER", raising=False)
            # main() assigns these directly; register them with monkeypatch so
            # the original test environment is restored after the CLI returns.
            patcher.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
            patcher.setenv("ENABLE_INTERNAL_SCHEDULER", "false")
            patcher.setenv("APP_ENV", "test")
            patcher.setenv("GEMINI_API_KEY", "mock-key")
            patcher.setenv("BRAVE_API_KEY", "mock-search-key")
            patcher.setattr(
                schema_status,
                "inspect_schema",
                lambda _: SimpleNamespace(
                    state=schema_status.SchemaState.CURRENT,
                    expected_revision="test-head",
                ),
            )
            patcher.setattr(
                controlled_runner, "selected_profiles", lambda *args: [object()]
            )
            patcher.setattr(GeminiClient, "from_environment", Mock())
            run = Mock(return_value=report)
            patcher.setattr(controlled_runner, "run_controlled", run)
            try:
                code = controlled_live_test.main()
                run.assert_called_once()
                return code
            finally:
                clear_settings_cache()

    return invoke


def test_complete_flow_from_discovered_html_to_stored_feed(
    runner_setup, isolated_session_factory, capsys, command_exit
):
    backend = FakeGemini()
    report = execute(backend)
    assert report.failures == []
    assert report.complete_flow is True
    assert report.run_success is True
    assert report.qualifying_result_found is True
    assert (
        report.qualifying_opportunities == report.verified
    )  # count opportunities, not pairs
    assert command_exit(report) == 0
    assert report.sources == 1
    assert report.pages <= 3
    assert len(report.verified) == 1
    assert report.matches == report.triaged == 2
    assert report.readiness == 0
    assert set(report.ai_calls) == {
        "SOURCE",
        "OPPORTUNITY",
        "MATCH",
        "TRIAGE",
    }
    assert all(
        check["discover"]
        and check["stored_match"]
        and check["feed_triage"]
        and not check["readiness"]
        and not check["feed_readiness"]
        and check["match_consistent"]
        and check["readiness_consistent"]
        for check in report.render_checks
    )
    with isolated_session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Opportunity)) == 1
        assert db.scalar(select(func.count()).select_from(CompanyOpportunityMatch)) == 2
        assert (
            db.scalar(select(func.count()).select_from(CompanyOpportunityTriage)) == 2
        )
        assert (
            db.scalar(select(func.count()).select_from(CompanyOpportunityReadiness))
            == 0
        )
    output = capsys.readouterr().out
    for private in (
        "SECRET_SEARCH_KEY",
        "CONFIDENTIAL_PROFILE",
        "PRIVATE_MATCH_REASON",
        "PRIVATE_ACTION",
    ):
        assert private not in output
    assert "QUALITY" in output
    assert "Independent controls review" in output


@pytest.mark.parametrize("stage", ["source", "opportunity"])
def test_rejection_never_creates_verified_opportunity(
    runner_setup, isolated_session_factory, stage, command_exit, capsys
):
    report = execute(
        FakeGemini(
            source_decision="REJECT" if stage == "source" else "ACCEPT",
            opportunity_decision="REJECT",
        )
    )
    assert report.verified == []
    assert report.complete_flow is False
    assert report.run_success is True
    assert report.qualifying_result_found is False
    assert report.qualifying_opportunities == []
    assert command_exit(report) == 0
    assert "Run status: SUCCESS" in capsys.readouterr().out
    assert "MATCH" not in report.ai_calls
    with isolated_session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Opportunity)) == 0
        if stage == "source":
            assert db.scalar(select(Source)).is_approved is False


@pytest.mark.parametrize("failure", ["CompanyMatch", "TriageResult", "ALL"])
def test_stage_failure_is_recorded_without_destroying_safe_results(
    runner_setup, isolated_session_factory, failure, capsys, command_exit
):
    report = execute(FakeGemini(failure=failure))
    assert report.failures
    assert report.run_success is False
    assert command_exit(report) == 1
    with isolated_session_factory() as db:
        if failure == "ALL":
            assert report.verified == []
            assert db.scalar(select(Source)).ai_status == "ERROR"
        else:
            assert len(report.verified) == 1
            assert db.get(Opportunity, report.verified[0]) is not None
            if failure == "CompanyMatch":
                assert db.scalar(select(CompanyOpportunityMatch)).error_reason
            if failure == "TriageResult":
                assert db.scalar(select(CompanyOpportunityTriage)).error_reason
    assert "SECRET_PROVIDER_DETAIL" not in capsys.readouterr().out


def test_second_run_reuses_all_durable_ai_caches(runner_setup):
    first = execute()
    blocked = Mock(spec=GeminiClient)
    blocked.generate_structured.side_effect = AssertionError("Durable cache miss")
    second = execute(blocked)
    assert first.verified == second.verified
    assert second.failures == []
    assert second.ai_calls == {}
    assert second.cache_hits >= 7
    blocked.generate_structured.assert_not_called()


def test_ingestion_does_not_invoke_readiness_even_when_qualifying(
    runner_setup, monkeypatch
):
    from app.intelligence import readiness_engine

    forbidden = Mock(side_effect=AssertionError("Readiness is explicit only"))
    monkeypatch.setattr(readiness_engine, "analyze_opportunity_readiness", forbidden)
    monkeypatch.setattr(readiness_engine, "extract_requirements", forbidden)
    report = execute()
    assert report.complete_flow
    assert report.readiness == 0
    assert "READINESS" not in report.ai_calls
    assert "REQUIREMENTS" not in report.ai_calls
    forbidden.assert_not_called()


def test_five_nonqualifying_pages_fit_budget_and_other_sources_are_analyzed(
    runner_setup,
    monkeypatch,
    isolated_session_factory,
    command_exit,
    capsys,
):
    from app.schemas import RawOpportunity
    from app.intelligence import readiness_engine

    monkeypatch.setattr(
        SourceDiscovery,
        "_search",
        lambda self, query: [
            SearchResult(
                "Public procurement",
                f"https://source{i}.example.test/tenders",
                "Official notices",
            )
            for i in range(3)
        ],
    )

    def pages(crawler, source):
        raw = [
            RawOpportunity(
                organisation_name=f"Buyer {i}",
                title=f"Specific invitation {i}",
                source_name=source.organisation_name or "Public Authority",
                source_url=f"https://source0.example.test/notice/{i}",
                description=f"Unique professional service invitation {i}",
            )
            for i in range(5)
        ]
        crawler.fetched.update(item.source_url for item in raw)
        return raw

    monkeypatch.setattr(BoundedCrawler, "_scan_source", pages)
    forbidden = Mock(side_effect=AssertionError("No readiness during ingestion"))
    monkeypatch.setattr(readiness_engine, "analyze_opportunity_readiness", forbidden)

    class LowMatchGemini(FakeGemini):
        def generate_structured(self, prompt, response_schema, **kwargs):
            assert response_schema.__name__ not in {
                "TriageResult",
                "RequirementExtraction",
                "ReadinessAnalysis",
            }
            value = super().generate_structured(prompt, response_schema, **kwargs)
            if response_schema.__name__ == "OpportunityAnalysis":
                number = self.calls.count("OpportunityAnalysis")
                value = value.model_copy(
                    update={
                        "title": f"Specific invitation {number}",
                        "buyer_name": f"Buyer {number}",
                    }
                )
            elif response_schema.__name__ == "CompanyMatch":
                from app.intelligence.schemas import MatchStrength

                value = value.model_copy(
                    update={"score": 15, "match_strength": MatchStrength.NOT_A_MATCH}
                )
            return value

    report = execute(LowMatchGemini(), limits=RunLimits(3, 5, 2, 20))
    assert report.failures == []
    assert report.run_success is True
    assert report.qualifying_result_found is False
    assert report.qualifying_opportunities == []
    assert report.complete_flow is False
    assert command_exit(report) == 0
    output = capsys.readouterr().out
    assert "Run status: SUCCESS" in output
    assert "Qualifying opportunities: 0" in output
    assert report.sources == 3
    assert report.pages == len(report.verified) == 5
    assert report.matches == 10
    assert report.triaged == report.readiness == 0
    assert report.ai_calls == {"SOURCE": 3, "OPPORTUNITY": 5, "MATCH": 10}
    assert sum(report.ai_calls.values()) == 18
    assert all(
        not check["discover"] and check["stored_match"]
        for check in report.render_checks
    )
    with isolated_session_factory() as db:
        assert (
            db.scalar(select(func.count()).select_from(CompanyOpportunityMatch)) == 10
        )
        assert (
            db.scalar(select(func.count()).select_from(CompanyOpportunityTriage)) == 0
        )
        assert (
            db.scalar(select(func.count()).select_from(CompanyOpportunityReadiness))
            == 0
        )
    forbidden.assert_not_called()


@pytest.mark.parametrize("limits", [RunLimits(1, 1, 1, 1), RunLimits(3, 5, 2, 4)])
def test_limits_enforced_before_network_and_ai(
    runner_setup, monkeypatch, limits, command_exit
):
    monkeypatch.setattr(
        SourceDiscovery,
        "_search",
        lambda self, query: [
            SearchResult(
                "Public procurement",
                f"https://source{index}.example.test/tenders",
                "Official notices",
            )
            for index in range(30)
        ],
    )
    report = execute(limits=limits)
    assert report.sources <= limits.sources
    assert report.pages <= limits.pages
    assert report.companies <= limits.companies
    assert sum(report.ai_calls.values()) <= limits.ai_calls
    assert len(runner_setup) <= limits.sources * 2 + limits.pages
    assert report.run_success is False
    assert any("REQUEST_BUDGET_EXHAUSTED" in failure for failure in report.failures)
    assert command_exit(report) == 1


@pytest.mark.parametrize("recovers", [False, True])
def test_provider_retry_health_drives_exit_code(runner_setup, command_exit, recovers):
    sdk = Mock()
    error = APIError(504, {"message": "mock timeout"})
    sdk.models.generate_content.side_effect = (
        [
            error,
            SimpleNamespace(
                parsed={
                    "decision": "REJECT",
                    "confidence": 100,
                    "reason": "Not a source",
                }
            ),
        ]
        if recovers
        else [error, error, error]
    )
    sleep = Mock()
    backend = GeminiClient(
        GeminiConfig(api_key="mock-key"),
        client_factory=lambda **kwargs: sdk,
        sleep=sleep,
        jitter=lambda: 0,
    )
    report = execute(backend)
    assert sdk.models.generate_content.call_count == (2 if recovers else 3)
    assert sleep.call_count == (1 if recovers else 2)
    assert report.run_success is recovers
    assert report.qualifying_result_found is False
    assert command_exit(report) == (0 if recovers else 1)


def test_local_validation_failure_is_failure_even_with_qualifying_match(
    runner_setup, command_exit
):
    class InvalidTriageGemini(FakeGemini):
        def generate_structured(self, prompt, response_schema, **kwargs):
            value = super().generate_structured(prompt, response_schema, **kwargs)
            if response_schema.__name__ == "TriageResult":
                # Valid schema, but contradicts the absent budget in the analysis.
                value = response_schema.model_validate(
                    {**value.model_dump(), "budget_evidence": "CONFIRMED"}
                )
            return value

    report = execute(InvalidTriageGemini())
    assert report.qualifying_result_found is True
    assert len(report.qualifying_opportunities) == 1
    assert report.triaged == 0
    assert any("LOCAL_VALIDATION_ERROR" in failure for failure in report.failures)
    assert report.run_success is False
    assert command_exit(report) == 1


@pytest.mark.parametrize(
    "failure",
    [
        SQLAlchemyError("mock persistence failure"),
        RuntimeError("mock unexpected failure"),
    ],
)
def test_persistence_or_unexpected_failure_exits_nonzero(
    runner_setup, monkeypatch, command_exit, failure
):
    monkeypatch.setattr(scanner, "save_opportunity", Mock(side_effect=failure))
    report = execute()
    assert report.failures
    assert report.run_success is False
    assert command_exit(report) == 1


def test_render_failure_exits_nonzero_even_with_successful_ai(
    runner_setup, monkeypatch, command_exit
):
    monkeypatch.setattr(
        "app.controlled_runner.verify_rendering",
        Mock(side_effect=AssertionError("mock render failure")),
    )
    report = execute()
    assert report.qualifying_result_found is True
    assert report.triaged == 2
    assert report.run_success is False
    assert command_exit(report) == 1


def test_failed_identical_request_is_never_repeated_in_one_run():
    backend = FakeGemini(failure="ALL")
    budget = BudgetedGemini(backend, RunLimits())
    for _ in range(3):
        with pytest.raises(GeminiAPIError):
            budget.generate_structured("same content", SourceAnalysis)
    assert backend.calls == ["SourceAnalysis"]
    assert budget.calls["SOURCE"] == 1


def test_one_crawler_failure_continues_other_sources(runner_setup, monkeypatch):
    monkeypatch.setattr(
        SourceDiscovery,
        "_search",
        lambda self, query: [
            SearchResult(
                "Public procurement",
                "https://broken.example.test/tenders",
                "Official notices",
            ),
            SearchResult(
                "Public procurement",
                "https://working.example.test/tenders",
                "Official notices",
            ),
        ],
    )
    original = BoundedCrawler._scan_source

    def scan(self, source):
        if "broken" in source.monitor_url:
            raise RuntimeError("Crawler failed")
        return original(self, source)

    monkeypatch.setattr(BoundedCrawler, "_scan_source", scan)
    report = execute(limits=RunLimits(2, 3, 2, 20))
    assert len(report.verified) == 1
    assert "SOURCE/CRAWLER: source failed" in report.failures


def test_no_ai_or_crawler_calls_during_rendering(runner_setup, monkeypatch):
    from app.controlled_runner import verify_rendering, BudgetedGemini

    report = execute()
    backend = Mock(spec=GeminiClient)
    forbidden = Mock(side_effect=AssertionError("No crawler/discovery on page loads"))
    monkeypatch.setattr(SourceDiscovery, "_search", forbidden)
    monkeypatch.setattr(BoundedCrawler, "_scan_source", forbidden)
    checks = verify_rendering(
        report.verified, [1, 2], BudgetedGemini(backend, RunLimits())
    )
    assert all(check["readiness_consistent"] for check in checks)
    forbidden.assert_not_called()
    backend.generate_structured.assert_not_called()


@pytest.mark.parametrize(
    "value",
    [
        "postgresql://production/db",
        "//server/share/data.db",
        "\\\\server\\share\\data.db",
        "../elsewhere.db",
    ],
)
def test_runner_refuses_nonlocal_database_paths(value):
    with pytest.raises(ValueError):
        local_database_path(value)


@pytest.mark.parametrize(
    "kwargs",
    [{"sources": 4}, {"pages": 6}, {"companies": 3}, {"ai_calls": 44}, {"pages": 0}],
)
def test_runner_refuses_excessive_limits(kwargs):
    with pytest.raises(ValueError):
        RunLimits(**kwargs)


def test_public_http_guard_blocks_private_destinations(monkeypatch):
    monkeypatch.setattr(
        "socket.getaddrinfo", lambda *args: [(2, 1, 6, "", ("127.0.0.1", 443))]
    )
    import requests

    with pytest.raises(requests.RequestException) as captured:
        BoundedWeb.validate_url("https://example.test/")
    from app.source_fetch_diagnostics import classify_fetch_failure

    assert classify_fetch_failure(captured.value).category == "BLOCKED"
    assert (
        display_url("https://user:secret@example.test/path?token=secret#fragment")
        == "https://example.test/path"
    )
