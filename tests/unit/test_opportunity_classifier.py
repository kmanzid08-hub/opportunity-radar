from __future__ import annotations

from datetime import date

from app.filters import classify_opportunity
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
