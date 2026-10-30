from __future__ import annotations

from datetime import date

import pytest

from app.intelligence.company_matcher import match_company_to_opportunity
from app.intelligence.gemini_client import GeminiResponseError
from app.intelligence.opportunity_analyzer import analyze_opportunity
from app.intelligence.schemas import (
    CompanyMatch,
    OpportunityAnalysis,
    SourceAnalysis,
)
from app.intelligence.source_analyzer import analyze_source


class StubGeminiClient:
    def __init__(self, responses: dict[type, object]):
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    def generate_structured(
        self,
        prompt: str,
        response_schema: type,
        *,
        system_instruction: str | None = None,
    ):
        self.calls.append(
            {
                "prompt": prompt,
                "response_schema": response_schema,
                "system_instruction": system_instruction,
            }
        )
        return self.responses[response_schema]


def _opportunity_analysis() -> OpportunityAnalysis:
    return OpportunityAnalysis(
        decision="ACCEPT",
        confidence=92,
        reason="A specific professional-services request.",
        title="Independent financial controls assessment",
        buyer_name="Example Foundation",
        country="Uganda",
        opportunity_type="RFP",
        status="ACTIVE",
        summary="The buyer requires an independent controls assessment.",
        services_required=["Financial controls assessment"],
        evidence=["Proposals are invited from qualified firms."],
    )


def test_source_analyzer_uses_semantic_structured_contract() -> None:
    expected = SourceAnalysis(
        decision="REJECT",
        confidence=96,
        source_name="Tender Index",
        is_paid_aggregator=True,
        reason="Subscription aggregator rather than an original source.",
    )
    client = StubGeminiClient({SourceAnalysis: expected})

    result = analyze_source(
        url="https://example.test/tenders",
        page_content="Subscribe to unlock buyer contact details.",
        client=client,
    )

    assert result is expected
    assert client.calls[0]["response_schema"] is SourceAnalysis
    instruction = str(client.calls[0]["system_instruction"])
    assert "Keyword occurrence is not evidence" in instruction
    assert "paid/subscription tender aggregators" in instruction


def test_opportunity_analyzer_distinguishes_jobs_from_services() -> None:
    expected = OpportunityAnalysis(
        decision="REJECT",
        confidence=99,
        reason="This page advertises individual employment.",
        title="Audit Manager Vacancy",
        opportunity_type="JOB_VACANCY",
        status="UNKNOWN",
        summary="An individual employment vacancy.",
        evidence=["Applicants must submit a CV."],
    )
    client = StubGeminiClient({OpportunityAnalysis: expected})

    result = analyze_opportunity(
        source_url="https://example.test/jobs/audit-manager",
        page_content="Apply for our full-time Audit Manager role.",
        as_of_date=date(2026, 9, 15),
        client=client,
    )

    assert result is expected
    instruction = str(client.calls[0]["system_instruction"])
    assert "keyword presence or frequency" in instruction
    assert '"Audit Manager Vacancy" is a job' in instruction


def test_company_matcher_uses_full_structured_inputs() -> None:
    expected = CompanyMatch(
        company_id=9,
        score=87,
        match_strength="STRONG",
        explanation="Strong capability and geographic fit.",
        strengths=["Relevant controls-assessment experience"],
        potential_gaps=[],
        eligibility_concerns=[],
        recommendation="Pursue.",
    )
    client = StubGeminiClient({CompanyMatch: expected})
    opportunity = _opportunity_analysis()

    result = match_company_to_opportunity(
        company_id=9,
        company_profile={
            "name": "Example Advisory",
            "services": ["Financial controls assessment"],
            "countries": ["Uganda"],
            "experience": ["Donor-funded project reviews"],
        },
        opportunity=opportunity,
        client=client,
    )

    assert result.model_dump(exclude={"eligibility_concerns"}) == expected.model_dump(exclude={"eligibility_concerns"})
    assert result.applicant_eligibility.value == "UNKNOWN"
    assert "not specified" in result.eligibility_concerns[0]
    prompt = str(client.calls[0]["prompt"])
    instruction = str(client.calls[0]["system_instruction"])
    assert "Donor-funded project reviews" in prompt
    assert opportunity.title in prompt
    assert "Never count keywords" in instruction


def test_company_matcher_rejects_wrong_company_identifier() -> None:
    client = StubGeminiClient(
        {
            CompanyMatch: CompanyMatch(
                company_id=10,
                score=50,
                match_strength="MODERATE",
                explanation="Ambiguous fit.",
                recommendation="Review manually.",
            )
        }
    )

    with pytest.raises(GeminiResponseError, match="wrong company"):
        match_company_to_opportunity(
            company_id=9,
            company_profile={"name": "Example Advisory"},
            opportunity=_opportunity_analysis(),
            client=client,
        )


@pytest.mark.parametrize(
    ("analyzer", "kwargs", "message"),
    [
        (
            analyze_source,
            {"url": "", "page_content": "content"},
            "Source URL",
        ),
        (
            analyze_opportunity,
            {"source_url": "https://example.test", "page_content": ""},
            "page content",
        ),
    ],
)
def test_analyzers_reject_blank_required_input(
    analyzer,
    kwargs: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        analyzer(**kwargs)


@pytest.mark.parametrize(("field", "value", "score", "explanation", "gap"), [
    ("capabilities", ["Independent controls assessment"], 91,
     "CONFIRMED: stated controls capability matches the required review.", ""),
    ("experience", {"relevant_experience": []}, 65,
     "POSSIBLE: service fit, with experience UNKNOWN.",
     "UNKNOWN: similar assignments are not documented, not proven absent."),
    ("countries", ["Canada"], 28,
     "CONFLICT: Uganda delivery is outside the stated markets.",
     "Confirm capacity for delivery outside the stated markets."),
    ("preferred_opportunity_types", ["RFP"], 84,
     "CONFIRMED: the RFP aligns with the stated opportunity preference.", ""),
    ("preferred_sectors", ["International development"], 80,
     "POSSIBLE: buyer context aligns with the preferred sector.",
     "UNKNOWN: sector experience is not documented by a preference."),
    ("exclusions", {"services": ["Controls assessment"]}, 10,
     "CONFLICT: the required service is explicitly excluded.", ""),
    ("exclusions", {"sectors": ["International development"]}, 20,
     "CONFLICT: buyer sector falls within an explicit exclusion.", ""),
    ("exclusions", {"countries": ["Uganda"]}, 12,
     "CONFLICT: delivery country is explicitly excluded.", ""),
    ("exclusions", {"opportunity_types": ["RFP"]}, 15,
     "CONFLICT: RFP assignments are explicitly excluded.", ""),
])
def test_rich_evidence_reaches_gemini_and_assessment_is_retained(
    field, value, score, explanation, gap,
):
    import json

    expected = CompanyMatch(
        company_id=9, score=score,
        match_strength="STRONG" if score >= 70 else "WEAK",
        explanation=explanation, potential_gaps=[gap] if gap else [],
        recommendation="Review the stated evidence before deciding.",
    )
    client = StubGeminiClient({CompanyMatch: expected})
    result = match_company_to_opportunity(
        company_id=9, company_profile={"name": "Example Advisory", field: value},
        opportunity=_opportunity_analysis(), client=client,
    )
    assert result.model_dump(exclude={"eligibility_concerns"}) == expected.model_dump(exclude={"eligibility_concerns"})
    assert "not specified" in result.eligibility_concerns[0]
    call = client.calls[0]
    assert json.dumps(value) in str(call["prompt"])
    instruction = str(call["system_instruction"])
    for label in ("CONFIRMED", "POSSIBLE", "UNKNOWN", "CONFLICT"):
        assert label in instruction
    assert "Never silently ignore an exclusion" in instruction
    assert "not individual words" in instruction
