from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from app.scanners.job_in_rwanda import JobInRwandaScanner


FIXTURE_DIR = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "job_in_rwanda"
)


def _soup(name: str) -> BeautifulSoup:
    return BeautifulSoup(
        (FIXTURE_DIR / name).read_text(encoding="utf-8"),
        "html.parser",
    )


def test_listing_links_are_normalized_deduplicated_and_stable() -> None:
    scanner = JobInRwandaScanner()

    first = scanner._extract_listing_links(_soup("listings.html"))
    second = scanner._extract_listing_links(_soup("listings.html"))

    assert list(first) == list(second) == [
        "https://www.jobinrwanda.com/job/external-audit",
        "https://www.jobinrwanda.com/job/grant-advisory-consultancy",
    ]
    assert first[
        "https://www.jobinrwanda.com/job/external-audit"
    ] == {
        "title": "Request for External Audit Services",
        "card_text": (
            "Request for External Audit Services "
            "Example Public Foundation Qualified firms are invited to "
            "submit proposals. Deadline: 31 December 2099."
        ),
        "organisation_name": "Example Public Foundation",
    }


def test_scan_builds_multiple_opportunities_from_local_html(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scanner = JobInRwandaScanner()
    monkeypatch.setattr(
        scanner,
        "SECTION_URLS",
        ("https://www.jobinrwanda.com/jobs/tender",),
    )
    monkeypatch.setattr(scanner, "MAX_PAGES_PER_SECTION", 1)
    monkeypatch.setattr(
        scanner,
        "_wait_between_requests",
        lambda: None,
    )

    def local_soup(url: str) -> BeautifulSoup:
        if "/jobs/tender" in url:
            return _soup("listings.html")
        if url.endswith("/job/external-audit"):
            return _soup("detail_valid.html")
        return _soup("detail_minimal.html")

    monkeypatch.setattr(scanner, "_get_soup", local_soup)

    opportunities = scanner.scan()

    assert [item.source_url for item in opportunities] == [
        "https://www.jobinrwanda.com/job/external-audit",
        "https://www.jobinrwanda.com/job/grant-advisory-consultancy",
    ]

    audit, consultancy = opportunities
    assert audit.title == (
        "Request for External Audit & Assurance Services"
    )
    assert audit.organisation_name == "Example Public Foundation"
    assert audit.deadline == date(2099, 12, 31)
    assert audit.source_name == "Job in Rwanda"
    assert "navigation text" not in (audit.description or "").lower()

    assert consultancy.title == "Grant Advisory Consultancy"
    assert consultancy.organisation_name == "Sample Research Institute"
    assert consultancy.deadline is None
    assert consultancy.description


def test_empty_or_malformed_listing_page_has_no_results() -> None:
    scanner = JobInRwandaScanner()

    assert scanner._extract_listing_links(
        _soup("empty_malformed.html")
    ) == {}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("31/12/2099", date(2099, 12, 31)),
        ("31st December, 2099", date(2099, 12, 31)),
        ("2099-12-31", date(2099, 12, 31)),
        ("32/13/2099", None),
        ("not a date", None),
        ("", None),
    ],
)
def test_supported_and_invalid_deadlines_are_characterized(
    value: str,
    expected: date | None,
) -> None:
    assert JobInRwandaScanner()._parse_date(value) == expected


def test_preview_without_title_is_rejected() -> None:
    scanner = JobInRwandaScanner()

    assert scanner._build_from_preview(
        listing_url="https://www.jobinrwanda.com/job/incomplete",
        preview={
            "title": "",
            "card_text": "Deadline: invalid",
            "organisation_name": "",
        },
    ) is None
