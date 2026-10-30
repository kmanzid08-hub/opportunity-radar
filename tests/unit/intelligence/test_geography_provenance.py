import json
from unittest.mock import Mock

from bs4 import BeautifulSoup
import pytest
from pydantic import ValidationError

from app.intelligence.company_matcher import match_company_to_opportunity
from app.intelligence.matching_engine import build_match_fingerprint
from app.intelligence.provenance import observed_links, public_url, resolve_original
from app.intelligence.schemas import (
    GeographicScope,
    OpportunityAnalysis,
    SourceAnalysis,
    SourceRole,
)
from app.models import Source
from app.source_discovery import SourceDiscovery

from .test_matching_engine import _analysis, _match


@pytest.mark.parametrize(
    "buyer,delivery,scope,country",
    [
        ("Rwanda", ["Rwanda"], "SINGLE_COUNTRY", "Rwanda"),
        ("Rwanda", ["Ghana"], "SINGLE_COUNTRY", "Ghana"),
        (
            "Rwanda",
            ["Côte d’Ivoire", "Ghana", "Mozambique"],
            "MULTI_COUNTRY",
            "MULTI_COUNTRY",
        ),
        (None, [], "REGIONAL", None),
        (None, [], "GLOBAL", None),
        (None, [], "REMOTE", None),
        (None, [], "UNKNOWN", None),
        ("Rwanda", [], "UNKNOWN", None),
    ],
)
def test_delivery_geography_and_legacy_compatibility(buyer, delivery, scope, country):
    analysis = _analysis(
        country="Rwanda",
        buyer_country=buyer,
        delivery_countries=delivery,
        geographic_scope=scope,
    )
    assert analysis.country == country
    assert analysis.buyer_country == buyer
    assert analysis.delivery_countries == delivery
    assert analysis.geographic_scope.value == scope


def test_old_legacy_country_is_not_invented_buyer_or_delivery():
    old = _analysis().model_dump(mode="json")
    for field in (
        "buyer_country",
        "delivery_countries",
        "eligibility_countries",
        "geographic_scope",
    ):
        old.pop(field)
    old["country"] = "Rwanda"
    result = OpportunityAnalysis.model_validate(old)
    assert result.country is None
    assert result.buyer_country is None
    assert result.delivery_countries == result.eligibility_countries == []
    assert result.geographic_scope is GeographicScope.UNKNOWN


def test_eligibility_is_separate_and_strict_validation_remains():
    result = _analysis(
        buyer_country="Rwanda",
        delivery_countries=["Ghana"],
        eligibility_countries=["Kenya", "Uganda"],
    )
    assert result.country == "Ghana"
    assert result.eligibility_countries == ["Kenya", "Uganda"]
    with pytest.raises(ValidationError):
        _analysis(geographic_scope="ANYWHERE")
    with pytest.raises(ValidationError):
        _analysis(invented_field="no")
    with pytest.raises(ValidationError):
        _analysis(delivery_countries="Ghana")


@pytest.mark.parametrize("role", list(SourceRole))
def test_source_role_does_not_control_acceptance(role):
    source = Source(
        base_url="https://publisher.test/",
        monitor_url="https://publisher.test/notices",
        domain="publisher.test",
    )
    analysis = SourceAnalysis(
        decision="ACCEPT", confidence=90, reason="Useful notices", source_role=role
    )
    SourceDiscovery._apply_source_analysis(
        source=source, analysis=analysis, fingerprint="a" * 64
    )
    assert source.source_role == role.value
    assert source.is_approved and source.is_active


def resolve(
    analysis=None,
    *,
    url="https://portal.test/notices/123",
    links=None,
    role=SourceRole.REPOST,
    domains=None,
):
    return resolve_original(
        analysis=analysis or _analysis(),
        discovered_url=url,
        links=links or [],
        source_role=role,
        authoritative_domains=domains or [],
    )


def test_authoritative_notice_stays_authoritative():
    assert (
        resolve(url="https://issuer.test/notices/123", role=SourceRole.AUTHORITATIVE)
        == "https://issuer.test/notices/123"
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://issuer.test/",
        "https://issuer.test/tenders",
        "https://issuer.test/jobs/consultancy",
        "https://issuer.test/rfp",
    ],
)
def test_listing_or_homepage_is_not_original_notice(url):
    assert resolve(url=url, role=SourceRole.AUTHORITATIVE) is None


def test_repost_resolves_observed_notice_on_known_issuer_domain():
    notice = "https://procurement.issuer.test/notices/123"
    assert (
        resolve(
            links=["https://unrelated.test/notices/123", notice],
            domains=["https://issuer.test/"],
        )
        == notice
    )


def test_explicit_semantic_issuer_link_resolves_official_document():
    notice = "https://issuer.test/files/notice-123.pdf"
    analysis = _analysis(
        buyer_website="https://issuer.test/", original_source_url=notice
    )
    assert resolve(analysis, links=["https://issuer.test/", notice]) == notice


def test_unobserved_or_wrong_domain_original_is_not_fabricated():
    analysis = _analysis(
        buyer_website="https://issuer.test/",
        original_source_url="https://issuer.test/notices/invented",
    )
    assert (
        resolve(
            analysis, links=["https://issuer.test/", "https://other.test/notices/123"]
        )
        is None
    )
    assert (
        resolve(_analysis(original_source_url="https://issuer.test/notices/123"))
        is None
    )


def test_unknown_ambiguous_and_unrelated_links_stay_unresolved():
    assert resolve(role=SourceRole.UNKNOWN) is None
    assert (
        resolve(
            links=["https://issuer.test/notices/1", "https://issuer.test/notices/2"],
            domains=["https://issuer.test/"],
        )
        is None
    )
    assert (
        resolve(
            links=["https://issuer.test/privacy.pdf"], domains=["https://issuer.test/"]
        )
        is None
    )
    assert (
        resolve(
            links=["https://issuer.test.evil.test/notices/123"],
            domains=["https://issuer.test/"],
        )
        is None
    )


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "http://127.0.0.1/rfp/1",
        "http://localhost/rfp/1",
        "https://user:password@issuer.test/rfp/1",
        "https://issuer.test/rfp/1?token=secret",
        "file:///secret",
        "https://issuer.test:444/rfp/1",
    ],
)
def test_unsafe_links_are_never_retained(url):
    assert public_url(url) is None


def test_captured_links_are_bounded_unique_and_never_fetched():
    soup = BeautifulSoup(
        "<a href='/rfp/1'>RFP</a><a href='/rfp/1#details'>Repeat</a><a href='javascript:bad()'>Bad</a>"
        + "".join(f"<a href='/rfp/{i}'>Notice</a>" for i in range(2, 100)),
        "html.parser",
    )
    values = observed_links(soup, "https://issuer.test/")
    assert values[0] == "https://issuer.test/rfp/1"
    assert len(values) == len(set(values)) == 80


def test_geography_reaches_semantic_matcher_without_buyer_country_fallback():
    analysis = _analysis(
        buyer_country="Rwanda",
        delivery_countries=["Ghana"],
        eligibility_countries=["Ghana"],
    )
    client = Mock()
    client.generate_structured.return_value = _match(1, score=20, strength="WEAK")
    result = match_company_to_opportunity(
        company_id=1,
        company_profile={"countries": ["Rwanda"]},
        opportunity=analysis,
        client=client,
    )
    prompt = client.generate_structured.call_args.args[0]
    payload = json.loads(prompt.split("OPPORTUNITY ANALYSIS JSON:", 1)[1])
    assert payload["buyer_country"] == "Rwanda"
    assert payload["delivery_countries"] == ["Ghana"]
    assert payload["country"] == "Ghana"
    assert payload["eligibility_countries"] == ["Ghana"]
    instruction = client.generate_structured.call_args.kwargs["system_instruction"]
    assert "does not establish geographic fit" in instruction
    assert result.score == 20  # no deterministic score boost from buyer headquarters


def test_multicountry_reaches_matcher_and_geography_invalidates_cache():
    first = _analysis(buyer_country="Rwanda", delivery_countries=["Ghana"])
    second = _analysis(
        buyer_country="Rwanda", delivery_countries=["Ghana", "Mozambique"]
    )

    def fingerprint(item):
        return build_match_fingerprint(
            company_profile={"countries": ["Rwanda"]},
            opportunity=item,
            minimum_score=70,
        )

    assert fingerprint(first) != fingerprint(second)
    client = Mock()
    client.generate_structured.return_value = _match(1, score=20, strength="WEAK")
    match_company_to_opportunity(
        company_id=1,
        company_profile={"countries": ["Rwanda"]},
        opportunity=second,
        client=client,
    )
    assert (
        '"delivery_countries": ["Ghana", "Mozambique"]'
        in client.generate_structured.call_args.args[0]
    )
    provenance_only = second.model_copy(
        update={
            "original_source_url": "https://issuer.test/rfp/1",
            "discovered_from_url": "https://portal.test/rfp/1",
            "source_role": SourceRole.REPOST,
            "source_confidence": 99,
        }
    )
    assert fingerprint(second) == fingerprint(provenance_only)
    assert fingerprint(second) != fingerprint(
        second.model_copy(
            update={"scope": "Materially changed authoritative requirements"}
        )
    )
