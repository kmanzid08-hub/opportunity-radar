from unittest.mock import Mock

from sqlalchemy import select

from app.intelligence.gemini_client import GeminiClient
from app.models import AIOpportunityAnalysis, Opportunity, Source
from app.scanners.company_websites import CompanyWebsiteScanner

from test_phase4_ui import _seed_ui_data


def test_stored_geography_and_provenance_render_without_external_calls(
    client, isolated_session_factory, monkeypatch
):
    company_id, opportunity_id = _seed_ui_data(isolated_session_factory)
    forbidden = Mock(side_effect=AssertionError("Rendering must be stored-only"))
    monkeypatch.setattr(GeminiClient, "generate_structured", forbidden)
    monkeypatch.setattr(CompanyWebsiteScanner, "scan", forbidden)
    with isolated_session_factory() as db:
        opportunity = db.get(Opportunity, opportunity_id)
        opportunity.original_source_url = "https://issuer.test/notices/123"
        analysis = db.scalar(select(AIOpportunityAnalysis))
        analysis.analysis_data = {
            **analysis.analysis_data,
            "buyer_country": "Rwanda",
            "delivery_countries": ["Ghana", "Mozambique"],
            "eligibility_countries": ["Kenya"],
            "geographic_scope": "MULTI_COUNTRY",
        }
        db.scalar(select(Source)).source_role = "DISCOVERY_ONLY"
        from conftest import stamp_current_fixture_matches

        stamp_current_fixture_matches(db)
        db.commit()

    discover = client.get(f"/?company_id={company_id}&country=Ghana")
    assert discover.status_code == 200
    assert "AI-clean controls review title" in discover.text
    assert "Ghana" in discover.text and "Mozambique" in discover.text
    buyer_filter = client.get(f"/?company_id={company_id}&country=Rwanda")
    assert "AI-clean controls review title" not in buyer_filter.text
    detail = client.get(f"/opportunities/{opportunity_id}?company_id={company_id}")
    assert detail.status_code == 200
    for value in (
        "Buyer country",
        "Rwanda",
        "Delivery",
        "Ghana",
        "Mozambique",
        "Eligibility",
        "Kenya",
        "https://issuer.test/notices/123",
        "https://phase4.test/opportunity",
    ):
        assert value in detail.text
    assert "Discovery Only" in client.get("/sources").text
    forbidden.assert_not_called()


def test_legacy_stored_record_has_unknown_geography_and_original(
    client, isolated_session_factory
):
    company_id, opportunity_id = _seed_ui_data(isolated_session_factory)
    response = client.get(f"/opportunities/{opportunity_id}?company_id={company_id}")
    assert response.status_code == 200
    assert "Original source not resolved" in response.text
    assert "Discovered via" in response.text


def test_same_original_and_discovery_link_is_shown_only_once(
    client, isolated_session_factory
):
    company_id, opportunity_id = _seed_ui_data(isolated_session_factory)
    with isolated_session_factory() as db:
        opportunity = db.get(Opportunity, opportunity_id)
        opportunity.original_source_url = opportunity.source_url
        db.commit()
    response = client.get(f"/opportunities/{opportunity_id}?company_id={company_id}")
    assert response.status_code == 200
    assert "Original source not resolved" not in response.text
    assert "Discovered via" not in response.text
