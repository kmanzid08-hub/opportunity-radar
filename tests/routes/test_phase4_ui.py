from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from conftest import stamp_current_fixture_matches
from sqlalchemy import event

from app.intelligence import matching_engine, opportunity_pipeline
from app.intelligence.schemas import OpportunityAnalysis
from app.models import (
    AIOpportunityAnalysis,
    CompanyOpportunityMatch,
    Opportunity,
    OpportunityPreference,
    Organization,
    Source,
)


def _analysis(source_url: str) -> OpportunityAnalysis:
    return OpportunityAnalysis.model_validate(
        {
            "decision": "ACCEPT",
            "confidence": 95,
            "reason": "Specific active procurement notice.",
            "title": "AI-clean controls review title",
            "buyer_name": "Development Programme Buyer",
            "issuing_organisation": "Development Programme Buyer",
            "country": "Rwanda",
            "location": "Kigali",
            "opportunity_type": "PROFESSIONAL_SERVICE",
            "status": "ACTIVE",
            "deadline": "2099-12-31",
            "estimated_value": "100,000",
            "currency": "USD",
            "summary": "Concise AI summary for a rapid bid decision.",
            "scope": "Review financial and operational controls.",
            "services_required": ["Controls review"],
            "eligibility_requirements": ["Registered firm"],
            "experience_requirements": ["Three similar assignments"],
            "qualification_requirements": ["Qualified team lead"],
            "required_documents": ["Technical proposal"],
            "deliverables": ["Final controls report"],
            "submission_method": "Submit through the procurement portal.",
            "original_source_url": source_url,
            "evidence": ["Qualified firms are invited to submit proposals."],
        }
    )


def _seed_ui_data(isolated_session_factory) -> tuple[int, int]:
    now = datetime.now(timezone.utc)
    source_url = "https://phase4.test/opportunity"
    analysis = _analysis(source_url)
    with isolated_session_factory() as db:
        company = Organization(
            name="Example Advisory Group",
            country="Rwanda",
            description="Assurance and advisory services.",
            is_active=True,
        )
        db.add(company)
        db.flush()
        db.add(
            OpportunityPreference(
                organization_id=company.id,
                countries=["Rwanda"],
                regions=["East Africa"],
                industries=["Professional services"],
                services=["Controls review"],
                opportunity_types=["Professional services"],
                keywords=["financial controls"],
                languages=["English"],
                minimum_match_score=70,
            )
        )
        opportunity = Opportunity(
            organisation_name="Raw buyer name",
            title="Raw crawler heading",
            category="PROFESSIONAL_SERVICE",
            description="RAW CRAWLER TEXT SHOULD NOT APPEAR IN DISCOVER",
            deadline=date(2099, 12, 31),
            source_name="Example Portal",
            source_url=source_url,
            status="New",
            is_expired=False,
            is_lead=True,
            lead_status="Under Review",
            next_action="Confirm eligibility",
            first_discovered_at=now,
            last_seen_at=now,
        )
        db.add(opportunity)
        db.flush()
        db.add(
            AIOpportunityAnalysis(
                opportunity_id=opportunity.id,
                source_url=source_url,
                raw_title=opportunity.title,
                source_name=opportunity.source_name,
                content_fingerprint="a" * 64,
                opportunity_fingerprint="b" * 64,
                decision="ACCEPT",
                confidence=95,
                opportunity_type="PROFESSIONAL_SERVICE",
                opportunity_status="ACTIVE",
                analysis_data=analysis.model_dump(mode="json"),
                analyzed_at=now,
            )
        )
        db.add(
            CompanyOpportunityMatch(
                organization_id=company.id,
                opportunity_id=opportunity.id,
                score=94,
                match_strength="VERY_STRONG",
                explanation="Strong fit with the company's controls practice.",
                strengths=["Direct service fit"],
                potential_gaps=["Confirm team availability"],
                eligibility_concerns=["Verify registration"],
                recommendation="Proceed to bid/no-bid review.",
                minimum_score=70,
                qualifies_for_feed=True,
                match_fingerprint="c" * 64,
                matched_at=now,
            )
        )
        db.add(
            Source(
                organisation_name="Example Procurement Portal",
                base_url="https://source.phase4.test",
                monitor_url="https://source.phase4.test/opportunities",
                domain="source.phase4.test",
                source_type="Procurement portal",
                confidence_score=91,
                is_active=True,
                is_approved=True,
                ai_status="ACCEPT",
                ai_confidence=96,
                ai_source_type="Official procurement portal",
                ai_reason="Publishes current professional-services notices.",
                total_opportunities_found=8,
            )
        )
        stamp_current_fixture_matches(db)
        db.commit()
        return company.id, opportunity.id


def test_primary_pages_render_stored_intelligence_without_side_effects(
    client,
    isolated_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import main
    from app.scanners.company_websites import CompanyWebsiteScanner

    company_id, opportunity_id = _seed_ui_data(isolated_session_factory)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("A GET page attempted an external intelligence action.")

    monkeypatch.setattr(main, "run_scanner", forbidden)
    monkeypatch.setattr(opportunity_pipeline, "analyze_opportunity", forbidden)
    monkeypatch.setattr(
        matching_engine,
        "match_company_to_opportunity",
        forbidden,
    )
    monkeypatch.setattr(CompanyWebsiteScanner, "scan", forbidden)

    discover = client.get(f"/?company_id={company_id}")
    detail = client.get(
        f"/opportunities/{opportunity_id}?company_id={company_id}"
    )
    pipeline = client.get("/pipeline")
    companies = client.get("/business-profile")
    sources = client.get("/sources")

    for response in (discover, detail, pipeline, companies, sources):
        assert response.status_code == 200

    assert "AI-clean controls review title" in discover.text
    assert "Development Programme Buyer" in discover.text
    assert "Concise AI summary" in discover.text
    assert "Example Advisory Group" in discover.text
    assert "RAW CRAWLER TEXT" not in discover.text
    assert "Controls review" in detail.text
    assert "Final controls report" in detail.text
    assert "Technical proposal" in detail.text
    assert "Confirm eligibility" in pipeline.text
    assert "Example Advisory Group" in companies.text
    assert "Publishes current professional-services notices" in sources.text


def test_discover_query_count_is_constant_per_page(
    client,
    isolated_session_factory,
) -> None:
    _seed_ui_data(isolated_session_factory)
    engine = isolated_session_factory.kw["bind"]
    select_count = 0

    def count_selects(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        nonlocal select_count
        if statement.lstrip().upper().startswith("SELECT"):
            select_count += 1

    event.listen(engine, "before_cursor_execute", count_selects)
    try:
        response = client.get("/")
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)

    assert response.status_code == 200
    assert select_count <= 10
