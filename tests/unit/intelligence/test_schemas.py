from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from app.intelligence.schemas import (
    AnalysisDecision,
    CompanyMatch,
    MatchStrength,
    OpportunityAnalysis,
    SourceAnalysis,
)


def test_source_analysis_parses_bounded_structured_data() -> None:
    result = SourceAnalysis.model_validate(
        {
            "decision": "ACCEPT",
            "confidence": 93,
            "source_name": "Procurement Notices",
            "organisation": "Example University",
            "country": "Kenya",
            "source_type": "University procurement page",
            "is_official": True,
            "publishes_opportunities": True,
            "reason": "Official procurement notices are published here.",
        }
    )

    assert result.decision is AnalysisDecision.ACCEPT
    assert result.confidence == 93
    assert result.is_paid_aggregator is None


def test_opportunity_analysis_preserves_unknowns_as_null_or_empty() -> None:
    result = OpportunityAnalysis.model_validate(
        {
            "decision": "REVIEW",
            "confidence": 58,
            "reason": "The page is incomplete.",
            "title": "Independent project controls assessment",
            "opportunity_type": "PROFESSIONAL_SERVICE",
            "status": "UNKNOWN",
            "publication_date": "2026-09-10",
        }
    )

    assert result.publication_date == date(2026, 9, 10)
    assert result.deadline is None
    assert result.estimated_value is None
    assert result.services_required == []
    assert result.evidence == []


@pytest.mark.parametrize(
    ("schema", "values", "field_name"),
    [
        (
            SourceAnalysis,
            {
                "decision": "ACCEPT",
                "confidence": 101,
                "reason": "Invalid confidence.",
            },
            "confidence",
        ),
        (
            OpportunityAnalysis,
            {
                "decision": "REVIEW",
                "confidence": 50,
                "reason": "Insufficient evidence.",
                "opportunity_type": "UNKNOWN",
                "status": "UNKNOWN",
                "source_confidence": -1,
            },
            "source_confidence",
        ),
        (
            CompanyMatch,
            {
                "company_id": 7,
                "score": -1,
                "match_strength": "WEAK",
                "explanation": "No fit.",
                "recommendation": "Do not pursue.",
            },
            "score",
        ),
    ],
)
def test_score_and_confidence_bounds_are_enforced(
    schema,
    values: dict[str, object],
    field_name: str,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        schema.model_validate(values)

    assert field_name in str(exc_info.value)


def test_company_match_parses_lists_and_enums() -> None:
    result = CompanyMatch.model_validate(
        {
            "company_id": 12,
            "score": 88,
            "match_strength": "STRONG",
            "explanation": "The delivery capability is well evidenced.",
            "strengths": ["Relevant delivery history"],
            "potential_gaps": [],
            "eligibility_concerns": ["Local registration is unclear"],
            "recommendation": "Pursue after confirming registration.",
        }
    )

    assert result.match_strength is MatchStrength.STRONG
    assert result.score == 88
