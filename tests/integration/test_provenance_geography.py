from unittest.mock import Mock

from bs4 import BeautifulSoup
import pytest
from sqlalchemy import func, select

from app import scanner
from app.intelligence import matching_engine, opportunity_pipeline
from app.intelligence.schemas import OpportunityAnalysis, SourceRole
from app.models import AIOpportunityAnalysis, Opportunity, Source
from app.scanners.company_websites import CompanyWebsiteScanner

from test_phase3_ai_pipeline import _analysis, _raw, _seed_companies, _company_match


OFFICIAL = "https://issuer.test/notices/123"
REPOST = "https://portal.test/notices/123"


@pytest.fixture
def provenance_setup(monkeypatch, isolated_session_factory):
    for module in (scanner, matching_engine, opportunity_pipeline):
        monkeypatch.setattr(module, "SessionLocal", isolated_session_factory)
    with isolated_session_factory() as db:
        for base, role in [
            ("https://issuer.test/", "AUTHORITATIVE"),
            ("https://portal.test/", "REPOST"),
        ]:
            db.add(
                Source(
                    organisation_name="Example Development Programme",
                    base_url=base,
                    monitor_url=base + "notices",
                    domain=base.split("/")[2],
                    source_role=role,
                    is_approved=True,
                    is_active=True,
                )
            )
        db.commit()

    def persist(raw, analysis=None):
        result = opportunity_pipeline.process_opportunity_candidate(
            raw, analyzer=Mock(return_value=analysis or _analysis())
        )
        assert result.status.value == "VERIFIED"
        identifier = result.duplicate_opportunity_id
        if identifier is None:
            scanner.save_opportunity(result.filtered_opportunity)
            identifier = opportunity_pipeline.link_analysis_to_saved_opportunity(
                raw.source_url
            )
        return identifier, result

    return persist


@pytest.mark.parametrize("official_first", [False, True])
def test_original_and_repost_converge_preserving_both_paths(
    provenance_setup, isolated_session_factory, official_first
):
    original = _raw(OFFICIAL)
    repost = _raw(REPOST)
    repost.outgoing_links = [OFFICIAL]
    first, second = (original, repost) if official_first else (repost, original)
    first_id, _ = provenance_setup(first)
    second_id, _ = provenance_setup(second)
    assert first_id == second_id
    with isolated_session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Opportunity)) == 1
        opportunity = db.get(Opportunity, first_id)
        assert opportunity.discovered_from_url == first.source_url
        assert opportunity.original_source_url == opportunity.canonical_url == OFFICIAL
        assert set(db.scalars(select(AIOpportunityAnalysis.source_url))) == {
            OFFICIAL,
            REPOST,
        }
    canonical = matching_engine._load_verified_analysis(first_id)
    assert canonical.source_role is SourceRole.AUTHORITATIVE


def test_unresolved_repost_remains_valid_then_original_enriches(
    provenance_setup, isolated_session_factory
):
    identifier, first = provenance_setup(_raw(REPOST))
    assert first.analysis.original_source_url is None
    with isolated_session_factory() as db:
        record = db.get(Opportunity, identifier)
        assert record.original_source_url is None
        assert record.canonical_url == REPOST
    resolved_id, _ = provenance_setup(_raw(OFFICIAL))
    assert resolved_id == identifier
    with isolated_session_factory() as db:
        record = db.get(Opportunity, identifier)
        assert record.source_url == REPOST
        assert record.original_source_url == OFFICIAL


def test_later_link_evidence_resolves_without_repeating_opportunity_ai(
    provenance_setup, isolated_session_factory
):
    raw = _raw(REPOST)
    identifier, _ = provenance_setup(raw)
    raw.outgoing_links = [OFFICIAL]
    forbidden = Mock(
        side_effect=AssertionError("Do not rerun AI for link-only enrichment")
    )
    outcome = opportunity_pipeline.process_opportunity_candidate(
        raw, analyzer=forbidden
    )
    assert outcome.analyzed is False
    assert outcome.duplicate_opportunity_id == identifier
    assert outcome.analysis.original_source_url == OFFICIAL
    forbidden.assert_not_called()
    with isolated_session_factory() as db:
        assert db.get(Opportunity, identifier).original_source_url == OFFICIAL


def test_authoritative_url_deduplicates_updated_content_and_refreshes_geo(
    provenance_setup, isolated_session_factory
):
    raw = _raw(REPOST)
    raw.outgoing_links = [OFFICIAL]
    identifier, _ = provenance_setup(raw)
    official = _raw(OFFICIAL)
    official.description = "Updated authoritative requirements and delivery geography"
    updated = OpportunityAnalysis.model_validate(
        {
            **_analysis().model_dump(),
            "scope": "Updated assignment scope",
            "buyer_country": "Rwanda",
            "delivery_countries": ["Ghana", "Mozambique"],
            "geographic_scope": "MULTI_COUNTRY",
        }
    )
    same_id, outcome = provenance_setup(official, updated)
    assert identifier == same_id
    assert outcome.analysis.country == "MULTI_COUNTRY"
    assert matching_engine._load_verified_analysis(identifier).delivery_countries == [
        "Ghana",
        "Mozambique",
    ]
    with isolated_session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Opportunity)) == 1


def test_search_country_does_not_overwrite_extracted_geography(provenance_setup):
    raw = _raw(OFFICIAL)
    analysis = OpportunityAnalysis.model_validate(
        {
            **_analysis().model_dump(),
            "buyer_country": "Rwanda",
            "delivery_countries": ["Ghana", "Mozambique"],
            "eligibility_countries": ["Kenya"],
            "geographic_scope": "MULTI_COUNTRY",
        }
    )
    identifier, result = provenance_setup(raw, analysis)
    assert raw.country_context == "Rwanda"
    assert result.analysis.country == "MULTI_COUNTRY"
    stored = matching_engine._load_verified_analysis(identifier)
    assert stored.delivery_countries == ["Ghana", "Mozambique"]
    assert stored.eligibility_countries == ["Kenya"]


def test_new_delivery_geo_rematches_but_provenance_only_enrichment_keeps_cache(
    provenance_setup, isolated_session_factory
):
    company_id = _seed_companies(isolated_session_factory)[0]
    identifier, _ = provenance_setup(_raw(REPOST))
    matcher = Mock(return_value=_company_match(company_id, 80, "STRONG"))
    matching_engine.match_verified_opportunity(
        identifier, organization_ids=[company_id], matcher=matcher
    )
    raw = _raw(REPOST)
    raw.outgoing_links = [OFFICIAL]
    provenance_setup(raw)
    assert (
        matching_engine.match_verified_opportunity(
            identifier, organization_ids=[company_id], matcher=matcher
        )[0].status.value
        == "CACHED"
    )
    with isolated_session_factory() as db:
        row = db.scalar(select(AIOpportunityAnalysis))
        row.analysis_data = {**row.analysis_data, "delivery_countries": ["Ghana"]}
        db.commit()
    assert (
        matching_engine.match_verified_opportunity(
            identifier, organization_ids=[company_id], matcher=matcher
        )[0].status.value
        == "MATCHED"
    )
    assert matcher.call_count == 2
    assert matcher.call_args.kwargs["opportunity"].country == "Ghana"


def test_crawler_captures_links_without_expanding_fetches(monkeypatch):
    crawler = CompanyWebsiteScanner()
    monkeypatch.setattr(crawler, "_looks_like_opportunity", lambda _: True)
    soup = BeautifulSoup(
        '<h1>RFP for consultancy</h1><main>Specific notice <a href="https://issuer.test/notices/123">Original notice</a></main>',
        "html.parser",
    )
    try:
        raw = crawler._build_page_opportunity(soup, REPOST, "Buyer", "Portal")
        assert raw.outgoing_links == [OFFICIAL]
    finally:
        crawler.session.close()
