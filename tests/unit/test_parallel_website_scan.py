"""Daily website scans must finish within a bounded time on many sources."""
from __future__ import annotations

from io import BytesIO
import threading

from bs4 import BeautifulSoup
import requests

from app.models import Source
from app.schemas import RawOpportunity
from app.scanners.company_websites import CompanyWebsiteScanner


def source(i: int) -> Source:
    return Source(
        id=i,
        organisation_name=f"Publisher {i}",
        domain=f"publisher{i}.example.rw",
        base_url=f"https://publisher{i}.example.rw",
        monitor_url=f"https://publisher{i}.example.rw",
        source_type="NGO",
        is_active=True,
        is_approved=True,
        priority_score=85,
    )


def test_daily_scan_uses_independent_http_sessions_for_parallel_sources(monkeypatch):
    monkeypatch.setenv("WEBSITE_SCAN_WORKERS", "4")
    items=[source(i) for i in range(1, 6)]
    monkeypatch.setattr(CompanyWebsiteScanner, "_get_sources", lambda self: items)
    monkeypatch.setattr(CompanyWebsiteScanner, "_record_scan_started", lambda self, _id: None)
    monkeypatch.setattr(CompanyWebsiteScanner, "_record_scan_success", lambda self, **_: None)
    monkeypatch.setattr(CompanyWebsiteScanner, "_record_scan_failure", lambda self, **_: None)

    seen=set()
    lock=threading.Lock()
    def fake_crawl(self, item):
        with lock:
            seen.add(id(self))
        return [RawOpportunity(
            organisation_name=item.organisation_name,
            title=f"Audit consultancy {item.id}",
            source_name=item.organisation_name,
            source_url=f"{item.monitor_url}/tenders/2026",
            description="Notice for qualified consulting companies.",
        )]
    monkeypatch.setattr(CompanyWebsiteScanner, "_scan_source", fake_crawl)
    results=CompanyWebsiteScanner().scan()
    assert len(results)==5
    assert len({x.source_url for x in results})==5
    assert len(seen)==5  # Every task gets its own crawler, not a shared Session.


def test_robots_rules_use_bounded_http_timeout_and_are_cached(monkeypatch):
    monkeypatch.delenv("WEBSITE_SCAN_WORKERS", raising=False)
    scanner=CompanyWebsiteScanner()
    calls=[]
    class FakeRaw(BytesIO):
        def read(self, n=-1, decode_content=False):
            return super().read(n)
    class FakeResponse:
        status_code=200
        def __init__(self):
            self.raw=FakeRaw(b"User-agent: *\nDisallow: /private\n")
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
    def get(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse()
    monkeypatch.setattr(scanner.session, "get", get)
    assert scanner._can_fetch("https://example.org/private/bids") is False
    assert scanner._can_fetch("https://example.org/tenders") is True
    assert len(calls)==1
    assert calls[0][1]["timeout"]==(3, 5)


def test_robots_network_failure_does_not_hang_or_crawl_without_permission(monkeypatch):
    scanner=CompanyWebsiteScanner()
    def timeout(*_args, **_kwargs):
        raise requests.Timeout("network stalled")
    monkeypatch.setattr(scanner.session, "get", timeout)
    assert scanner._can_fetch("https://unreachable.example.org/tenders") is False


def test_per_site_page_limit_respects_configuration(monkeypatch):
    monkeypatch.setenv("WEBSITE_MAX_PAGES_PER_SOURCE", "2")
    monkeypatch.setenv("WEBSITE_CRAWL_BUDGET_SECONDS", "40")
    scanner=CompanyWebsiteScanner()
    visited=[]
    monkeypatch.setattr(scanner, "_can_fetch", lambda url: True)
    def get_page(url):
        visited.append(url)
        return BeautifulSoup("<html><body><h1>Publisher</h1></body></html>", "html.parser")
    monkeypatch.setattr(scanner, "_get_soup", get_page)
    monkeypatch.setattr(scanner, "_build_page_opportunity", lambda **kwargs: None)
    monkeypatch.setattr(scanner, "_wait_between_requests", lambda: None)
    monkeypatch.setattr(
        scanner, "_extract_relevant_links",
        lambda **kwargs: [
            "https://publisher1.example.rw/tenders",
            "https://publisher1.example.rw/announcements",
            "https://publisher1.example.rw/consultancies",
        ],
    )
    assert scanner._scan_source(source(1))==[]
    assert len(visited)==2
