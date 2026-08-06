from __future__ import annotations

from datetime import date

from app.filters import (
    classify_opportunity,
    classify_opportunity_with_reason,
)
from app.schemas import RawOpportunity


def test_qualifying_opportunity_maps_to_filtered_contract() -> None:
    raw_opportunity = RawOpportunity(
        organisation_name="Example Public Buyer",
        title="Request for proposals for external audit services",
        source_name="Example Procurement Portal",
        source_url=(
            "https://procurement.example.test/notices/external-audit"
        ),
        description=(
            "Qualified audit firms are invited to submit a proposal. "
            "The submission deadline is 31 December 2026."
        ),
        deadline=date(2026, 12, 31),
    )

    result = classify_opportunity(raw_opportunity)

    assert result is not None
    assert result.organisation_name == raw_opportunity.organisation_name
    assert result.title == raw_opportunity.title
    assert result.category == "Audit"
    assert result.source_name == raw_opportunity.source_name
    assert result.source_url == raw_opportunity.source_url
    assert result.description == raw_opportunity.description
    assert result.deadline == raw_opportunity.deadline
    assert result.match_score == 73
    assert result.match_reason


def test_aggregate_tender_index_is_rejected_with_clear_reason() -> None:
    repeated_notices = " ".join(
        (
            f"Tender {number}. Publish date: 5 August 2026. "
            f"Closing date: {20 + number} August 2026. "
            "Country: Example. Tender type: Invitation to Bid."
        )
        for number in range(1, 4)
    )
    raw_opportunity = RawOpportunity(
        organisation_name="Example Tender Aggregator",
        title=(
            "World Tenders and Procurement Opportunities Search "
            "Advanced Search Business Services Tender Listings"
        ),
        source_name="Example Tender Aggregator",
        source_url="https://aggregator.example.test/tenders/cpv-79000000",
        description=(
            "List of tenders. Filter country. No. of entries. "
            + repeated_notices
        ),
        deadline=date(2026, 8, 21),
    )

    decision = classify_opportunity_with_reason(raw_opportunity)

    assert decision.opportunity is None
    assert decision.rejection_reason is not None
    assert "aggregate listing page" in decision.rejection_reason


def test_single_tender_notice_is_not_treated_as_aggregate() -> None:
    raw_opportunity = RawOpportunity(
        organisation_name="Example Public Buyer",
        title="Request for proposals for external audit services",
        source_name="Example Procurement Portal",
        source_url="https://procurement.example.test/notices/audit-2026",
        description=(
            "Publish date: 5 August 2026. Closing date: 31 August 2026. "
            "Qualified audit firms are invited to submit a proposal."
        ),
        deadline=date(2026, 8, 31),
    )

    decision = classify_opportunity_with_reason(raw_opportunity)

    assert decision.opportunity is not None
    assert decision.rejection_reason is None
