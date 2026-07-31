from __future__ import annotations

from datetime import date
from pathlib import Path

from bs4 import BeautifulSoup

from app.scanners.company_websites import CompanyWebsiteScanner


FIXTURE_DIR = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "company_websites"
)


def _soup(name: str) -> BeautifulSoup:
    return BeautifulSoup(
        (FIXTURE_DIR / name).read_text(encoding="utf-8"),
        "html.parser",
    )


def test_relevant_links_are_normalized_deduplicated_and_stable() -> None:
    scanner = CompanyWebsiteScanner()
    arguments = {
        "soup": _soup("links.html"),
        "page_url": "https://example.test/procurement/",
        "base_domain": "example.test",
    }

    first = scanner._extract_relevant_links(**arguments)
    second = scanner._extract_relevant_links(**arguments)

    assert first == second == [
        (
            "https://example.test/docs/"
            "consultancy-terms-of-reference.pdf?download=1"
        ),
        "https://example.test/tenders/audit-rfp",
        "https://example.test/procurement/grants?round=2",
        "https://external.example.test/tenders/foreign",
        "https://example.test/opportunities/supplier-registration?cycle=1",
    ]


def test_page_opportunity_preserves_source_and_cleans_html() -> None:
    scanner = CompanyWebsiteScanner()

    opportunity = scanner._build_page_opportunity(
        soup=_soup("opportunity_page.html"),
        page_url=(
            "https://example.test/procurement/audit-rfp?round=2"
        ),
        organisation_name="Example Advisory Cooperative",
        source_name="Example Advisory",
    )

    assert opportunity is not None
    assert opportunity.title == (
        "Audit & Risk Advisory Request for Proposals"
    )
    assert opportunity.organisation_name == "Example Advisory Cooperative"
    assert opportunity.source_name == "Example Advisory"
    assert opportunity.source_url == (
        "https://example.test/procurement/audit-rfp?round=2"
    )
    assert opportunity.deadline == date(2099, 1, 15)
    assert "  " not in (opportunity.description or "")
    assert "Navigation that" not in (opportunity.description or "")
    assert "Footer text" not in (opportunity.description or "")


def test_irrelevant_or_malformed_page_does_not_build_opportunity() -> None:
    scanner = CompanyWebsiteScanner()

    opportunity = scanner._build_page_opportunity(
        soup=_soup("irrelevant_malformed.html"),
        page_url="https://example.test/about",
        organisation_name=None,
        source_name="example.test",
    )

    assert opportunity is None


def test_empty_page_has_no_links_or_opportunity() -> None:
    scanner = CompanyWebsiteScanner()
    soup = BeautifulSoup("", "html.parser")

    assert scanner._extract_relevant_links(
        soup=soup,
        page_url="https://example.test/",
        base_domain="example.test",
    ) == []
    assert scanner._build_page_opportunity(
        soup=soup,
        page_url="https://example.test/",
        organisation_name=None,
        source_name="example.test",
    ) is None
