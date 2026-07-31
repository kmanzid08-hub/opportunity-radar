from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from app import filters
from app.schemas import RawOpportunity


def _raw_opportunity(
    *,
    title: str = "Request for proposals for external audit services",
    description: str | None = (
        "Qualified firms are invited to submit a proposal. "
        "The submission deadline is 31 December 2026."
    ),
    deadline: date | None = date(2099, 12, 31),
    source_url: str = "https://example.test/opportunities/external-audit",
) -> RawOpportunity:
    return RawOpportunity(
        organisation_name="Example Buyer",
        title=title,
        source_name="Example Portal",
        source_url=source_url,
        description=description,
        deadline=deadline,
    )


@pytest.fixture
def classify_without_contract_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    """Expose scoring while the separately tested output mismatch remains."""
    monkeypatch.setattr(
        filters,
        "FilteredOpportunity",
        lambda **values: SimpleNamespace(**values),
    )
    return filters.classify_opportunity


@pytest.mark.parametrize(
    "opportunity",
    [
        _raw_opportunity(
            title="External audit services",
            description="An overview of the organisation's audit services.",
            deadline=None,
        ),
        _raw_opportunity(
            title="Careers",
            description="Apply for our external audit manager job vacancy.",
            deadline=None,
        ),
        _raw_opportunity(
            title="",
            description=None,
            deadline=None,
        ),
    ],
)
def test_irrelevant_or_incomplete_opportunities_are_filtered_out(
    opportunity: RawOpportunity,
) -> None:
    assert filters.classify_opportunity(opportunity) is None


def test_single_service_and_procurement_match_remains_below_threshold(
    classify_without_contract_failure,
) -> None:
    opportunity = _raw_opportunity(
        title="External audit request for proposal",
        description=None,
        deadline=None,
    )

    assert classify_without_contract_failure(opportunity) is None


def test_current_scoring_and_reason_are_characterized(
    classify_without_contract_failure,
) -> None:
    result = classify_without_contract_failure(_raw_opportunity())

    assert result is not None
    assert result.category == "Audit"
    assert result.match_score == 60
    assert result.match_reason == (
        "deadline detected, external audit, external audit services, "
        "qualified firms, request for proposals, submission deadline, "
        "submit a proposal"
    )


def test_repeated_terms_do_not_change_score_or_duplicate_reasons(
    classify_without_contract_failure,
) -> None:
    baseline = classify_without_contract_failure(_raw_opportunity())
    repeated = classify_without_contract_failure(
        _raw_opportunity(
            description=(
                "Qualified firms qualified firms are invited to submit a "
                "proposal and submit a proposal. Submission deadline. "
                "Submission deadline."
            )
        )
    )

    assert baseline is not None
    assert repeated is not None
    assert repeated.match_score == baseline.match_score == 60
    assert repeated.match_reason == baseline.match_reason


def test_deadline_presence_scores_past_and_future_dates_equally(
    classify_without_contract_failure,
) -> None:
    past = classify_without_contract_failure(
        _raw_opportunity(deadline=date(2000, 1, 1))
    )
    future = classify_without_contract_failure(
        _raw_opportunity(deadline=date(2099, 12, 31))
    )

    assert past is not None
    assert future is not None
    assert past.match_score == future.match_score == 60


def test_relevance_score_is_capped_at_one_hundred(
    classify_without_contract_failure,
) -> None:
    opportunity = _raw_opportunity(
        title=(
            "Request for proposals and invitation to bid for external "
            "audit services and statutory audit services"
        ),
        description=(
            "Qualified firms and eligible firms are invited to submit a "
            "proposal and submit a bid. Submission deadline, proposal "
            "deadline, evaluation criteria and technical proposal."
        ),
        source_url="https://example.test/request-for-proposal-audit.pdf",
    )

    result = classify_without_contract_failure(opportunity)

    assert result is not None
    assert result.category == "Audit"
    assert result.match_score == 100
