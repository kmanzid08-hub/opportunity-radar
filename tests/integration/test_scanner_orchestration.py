from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import func, select

from app import scanner
from app.models import Opportunity
from app.schemas import RawOpportunity


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
        "ensure_sqlite_schema",
        lambda: None,
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
    assert "[REJECTED] About our audit services" in output
    assert "Reason: no procurement evidence was detected" in output
    assert "Raw opportunities found: 3" in output
    assert "Accepted: 2" in output
    assert "Rejected: 1" in output
    assert "Duplicates: 1" in output
    assert "Saved: 1" in output
    assert "Errors: 0" in output


def test_classification_error_is_reported_and_propagated(
    isolated_session_factory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _configure_isolated_scan(
        monkeypatch,
        isolated_session_factory,
        [_qualifying_opportunity()],
    )

    def broken_classifier(
        _opportunity: RawOpportunity,
    ) -> None:
        raise RuntimeError("classifier contract failed")

    monkeypatch.setattr(
        scanner,
        "classify_opportunity_with_reason",
        broken_classifier,
    )

    with pytest.raises(
        RuntimeError,
        match="classifier contract failed",
    ):
        scanner.run_scanner()

    output = capsys.readouterr().out
    assert "Raw opportunities found: 1" in output
    assert "Accepted: 0" in output
    assert "Rejected: 0" in output
    assert "Duplicates: 0" in output
    assert "Saved: 0" in output
    assert "Errors: 1" in output
