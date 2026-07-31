from __future__ import annotations

from app.discovery.source_repository import (
    get_base_url,
    normalise_domain,
    normalise_url,
)
from app.scanners.company_websites import CompanyWebsiteScanner
from app.source_quality import SourceQualityEvaluator


def test_repository_url_normalization_removes_tracking_and_sorts_query() -> None:
    url = (
        "HTTPS://WWW.Example.Test/tenders/?utm_source=newsletter"
        "&b=2&a=1#notice"
    )

    assert normalise_url(url) == (
        "https://example.test/tenders?a=1&b=2"
    )


def test_repository_normalization_produces_stable_duplicate_keys() -> None:
    first = "https://www.example.test/tenders/?utm_campaign=spring"
    second = "https://example.test/tenders"

    assert normalise_url(first) == normalise_url(second)
    assert normalise_domain(first) == "example.test"
    assert get_base_url(first) == "https://example.test"


def test_company_scanner_normalizes_scheme_less_and_protocol_relative_urls() -> None:
    scanner = CompanyWebsiteScanner()

    assert scanner._normalise_url("Example.Test/tenders/") == (
        "https://example.test/tenders"
    )
    assert scanner._normalise_url("//Example.Test/tenders/#details") == (
        "https://example.test/tenders"
    )


def test_source_quality_normalization_requires_absolute_http_url() -> None:
    evaluator = SourceQualityEvaluator()

    assert evaluator.normalise_url("example.test/tenders") == ""
    assert evaluator.normalise_url(
        "HTTPS://Example.Test/tenders/?a=1#details"
    ) == ""
    assert evaluator.normalise_url(
        "https://Example.Test/tenders/?a=1#details"
    ) == "https://example.test/tenders"
