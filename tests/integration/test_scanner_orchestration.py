from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import func, select

from app import scanner
from app.intelligence.opportunity_pipeline import (
    OpportunityProcessingOutcome,
    OpportunityProcessingStatus,
)
from app.models import Opportunity
from app.schemas import FilteredOpportunity, RawOpportunity


class StubScanner:
    source_name = "Stub Scanner"

    def __init__(
        self,
        opportunities: list[RawOpportunity],
    ) -> None:
        self.opportunities = opportunities

    def scan(self) -> list[RawOpportunity]:
        return self.opportunities


def _qualifying_opportunity() -> RawOpportunity:
    return RawOpportunity(
        organisation_name="Example Public Buyer",
        title=(
            "Request for proposals for external audit services"
        ),
        source_name="Example Procurement Portal",
        source_url=(
            "https://example.test/notices/external-audit"
        ),
        description=(
            "Qualified audit firms are invited to submit a proposal. "
            "The submission deadline is 31 December 2099."
        ),
        deadline=date(2099, 12, 31),
    )


def _rejected_opportunity() -> RawOpportunity:
    return RawOpportunity(
        organisation_name="Example Public Buyer",
        title="About our audit services",
        source_name="Example Website",
        source_url="https://example.test/about",
        description="An overview of the services we provide.",
    )


def _configure_isolated_scan(
    monkeypatch: pytest.MonkeyPatch,
    isolated_session_factory,
    opportunities: list[RawOpportunity],
) -> None:
    monkeypatch.setattr(
        scanner,
        "SessionLocal",
        isolated_session_factory,
    )
    monkeypatch.setattr(
        scanner,
        "approve_existing_sources",
        lambda: 0,
    )
    monkeypatch.setattr(
        scanner,
        "mark_expired_opportunities",
        lambda: 0,
    )
    monkeypatch.setattr(
        scanner,
        "get_scanners",
        lambda: [StubScanner(opportunities)],
    )

    def process_candidate(
        raw: RawOpportunity,
        *,
        country_context: str | None = None,
    ) -> OpportunityProcessingOutcome:
        del country_context
        if raw.title == "About our audit services":
            return OpportunityProcessingOutcome(
                status=OpportunityProcessingStatus.REJECTED,
                analysis=None,
                analyzed=True,
            )
        return OpportunityProcessingOutcome(
            status=OpportunityProcessingStatus.VERIFIED,
            analysis=None,
            analyzed=True,
            filtered_opportunity=FilteredOpportunity(
                organisation_name=raw.organisation_name,
                title=raw.title,
                category="Audit",
                source_name=raw.source_name,
                source_url=raw.source_url,
                match_score=95,
                match_reason="Verified professional-services notice.",
                description=raw.description,
                deadline=raw.deadline,
            ),
        )

    monkeypatch.setattr(
        scanner,
        "process_opportunity_candidate",
        process_candidate,
    )
    monkeypatch.setattr(
        scanner,
        "link_analysis_to_saved_opportunity",
        lambda _source_url: None,
    )


def test_scan_saves_qualifying_item_and_reports_funnel(
    isolated_session_factory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    qualifying = _qualifying_opportunity()
    _configure_isolated_scan(
        monkeypatch,
        isolated_session_factory,
        [
            qualifying,
            qualifying,
            _rejected_opportunity(),
        ],
    )

    scanner.run_scanner()

    with isolated_session_factory() as session:
        assert session.scalar(
            select(func.count()).select_from(Opportunity)
        ) == 1
        stored = session.scalar(select(Opportunity))

    assert stored is not None
    assert stored.title == qualifying.title
    assert stored.category == "Audit"

    output = capsys.readouterr().out
    assert "About our audit services" not in output
    assert "Possible listings found:       3" in output
    assert "Relevant opportunities:        2" in output
    assert "New opportunities saved:       1" in output
    assert "Existing or updated records:   1" in output
    assert "Scanner failures:              0" in output
    assert "Classification failures:       0" in output
    assert "Database save failures:        0" in output


def test_classification_error_is_reported_and_scan_continues(
    isolated_session_factory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _configure_isolated_scan(
        monkeypatch,
        isolated_session_factory,
        [_qualifying_opportunity()],
    )

    def broken_analyzer(
        _opportunity: RawOpportunity,
        *,
        country_context: str | None = None,
    ) -> None:
        del country_context
        raise RuntimeError("analyzer contract failed")

    monkeypatch.setattr(
        scanner,
        "process_opportunity_candidate",
        broken_analyzer,
    )

    scanner.run_scanner()

    output = capsys.readouterr().out
    assert "Classification failed for:" in output
    assert "RuntimeError: analyzer contract failed" in output
    assert "Possible listings found:       1" in output
    assert "Relevant opportunities:        0" in output
    assert "New opportunities saved:       0" in output
    assert "Classification failures:       1" in output
