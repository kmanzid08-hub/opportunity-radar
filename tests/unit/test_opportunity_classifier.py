from __future__ import annotations

from datetime import date

import pytest

from app.filters import classify_opportunity
from app.schemas import RawOpportunity


@pytest.mark.xfail(
    strict=True,
    raises=TypeError,
    reason=(
        "Known contract mismatch: classify_opportunity passes raw= to "
        "FilteredOpportunity, whose dataclass defines flattened fields."
    ),
)
def test_qualifying_opportunity_exposes_filtered_contract_mismatch() -> None:
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
    assert result.title == raw_opportunity.title
    assert result.source_url == raw_opportunity.source_url
