"""Interrupted publisher processing must be recoverable on the next scan."""
from __future__ import annotations

from datetime import date

import pytest

from sqlalchemy import select

from app import scanner
from app.models import Opportunity, Source
from app.schemas import FilteredOpportunity, RawOpportunity
from app.intelligence.opportunity_pipeline import (
    OpportunityProcessingOutcome,
    OpportunityProcessingStatus,
)
from app.scanners import company_websites
from app.scanners.company_websites import CompanyWebsiteScanner


def _source(index: int) -> Source:
    return Source(
        organisation_name=f"Buyer {index}",
        base_url=f"https://buyer{index}.example.rw",
        monitor_url=f"https://buyer{index}.example.rw/tenders",
        domain=f"buyer{index}.example.rw",
        source_type="Public buyer",
        is_active=True,
        is_approved=True,
        confidence_score=80,
    )


def _raw(index: int) -> RawOpportunity:
    return RawOpportunity(
        organisation_name=f"Buyer {index}",
        title=f"Audit services tender {index}",
        source_name=f"Buyer {index}",
        source_url=f"https://buyer{index}.example.rw/tenders/audit",
        description="Qualified audit firms are invited to tender.",
        deadline=date(2099, 12, 31),
    )


def test_site_acknowledged_only_after_all_ai_processing(
    monkeypatch, isolated_session_factory
):
    monkeypatch.setenv("WEBSITE_SCAN_WORKERS", "1")
    monkeypatch.setattr(company_websites, "SessionLocal", isolated_session_factory)
    monkeypatch.setattr(scanner, "SessionLocal", isolated_session_factory)
    monkeypatch.setattr(scanner, "approve_existing_sources", lambda: 0)
    monkeypatch.setattr(scanner, "mark_expired_opportunities", lambda: 0)
    monkeypatch.setattr(scanner, "get_scanners", lambda: [CompanyWebsiteScanner()])
    monkeypatch.setattr(
        scanner, "link_analysis_to_saved_opportunity", lambda _url: None
    )
    with isolated_session_factory() as db:
        db.add_all([_source(1), _source(2)])
        db.commit()

    def fake_crawl(_self, site):
        return [_raw(1 if "buyer1." in site.monitor_url else 2)]

    monkeypatch.setattr(CompanyWebsiteScanner, "_scan_source", fake_crawl)
    monkeypatch.setattr(CompanyWebsiteScanner, "_wait_between_requests", lambda _: None)

    def fake_ai(raw, *, country_context=None):
        del country_context
        with isolated_session_factory() as db:
            site = db.scalar(
                select(Source).where(Source.domain == "buyer1.example.rw")
            )
            assert site is not None
            if "buyer2." in raw.source_url:
                # First site's checkpoint is durable before processing site 2.
                assert site.last_scanned_at is None
            else:
                # Even when crawling finishes, no checkpoint before AI completes.
                assert site.last_scanned_at is None
        if "buyer1." in raw.source_url:
            raise RuntimeError("temporary AI outage")
        return OpportunityProcessingOutcome(
            status=OpportunityProcessingStatus.VERIFIED,
            analysis=None,
            analyzed=True,
            filtered_opportunity=FilteredOpportunity(
                organisation_name=raw.organisation_name,
                title=raw.title,
                category="Audit",
                source_name=raw.source_name,
                source_url=raw.source_url,
                match_score=95,
                match_reason="Professional audit notice",
                description=raw.description,
                deadline=raw.deadline,
            ),
        )

    monkeypatch.setattr(scanner, "process_opportunity_candidate", fake_ai)
    scanner.run_scanner()

    with isolated_session_factory() as db:
        one = db.scalar(select(Source).where(Source.domain == "buyer1.example.rw"))
        two = db.scalar(select(Source).where(Source.domain == "buyer2.example.rw"))
        stored = list(db.scalars(select(Opportunity)).all())
        assert one.last_scanned_at is None
        assert two.last_scanned_at is not None
        assert len(stored) == 1
        assert "buyer2." in stored[0].source_url


def test_acknowledgement_is_idempotent_and_deferred(
    monkeypatch, isolated_session_factory
):
    monkeypatch.setattr(company_websites, "SessionLocal", isolated_session_factory)
    with isolated_session_factory() as db:
        db.add(_source(1))
        db.commit()
        site = db.scalar(select(Source))
        site_id = site.id
    crawler = CompanyWebsiteScanner()
    monkeypatch.setattr(crawler, "_scan_source", lambda site: [_raw(1)])
    with isolated_session_factory() as db:
        site = db.get(Source, site_id)
    batch = crawler._crawl_source(site, 1, 1)
    assert batch.crawl_succeeded

    with isolated_session_factory() as db:
        assert db.get(Source, site_id).last_scanned_at is None

    crawler.acknowledge_source(batch)
    crawler.acknowledge_source(batch)
    with isolated_session_factory() as db:
        saved = db.get(Source, site_id)
        assert saved.last_scanned_at is not None
        assert saved.total_opportunities_found == 1



def test_unverified_duplicate_does_not_hold_entire_publisher_for_retry(monkeypatch):
    """A canonical REVIEW/REJECT result is not a transient matching outage."""
    from app.intelligence import matching_engine

    def unverified(_opportunity_id):
        raise ValueError("Company matching accepts VERIFIED opportunities only.")

    monkeypatch.setattr(matching_engine, "_load_verified_analysis", unverified)
    monkeypatch.setattr(
        scanner,
        "match_verified_opportunity",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("should not match unverified notice")
        ),
    )
    assert scanner.match_saved_opportunity(42, []) == (0, 0)



def test_strict_scan_reports_partial_ai_failure_after_summary(monkeypatch):
    """Scheduled jobs must fail visibly while preserving successful writes."""
    monkeypatch.setenv("SCAN_FAIL_ON_ERRORS", "true")
    monkeypatch.setattr(scanner, "approve_existing_sources", lambda: 0)
    monkeypatch.setattr(scanner, "mark_expired_opportunities", lambda: 0)
    monkeypatch.setattr(scanner, "load_active_company_profiles", lambda _: [])

    class TestSource:
        source_name = "Test publisher"

        def scan(self):
            return [_raw(1)]

    monkeypatch.setattr(scanner, "get_scanners", lambda: [TestSource()])

    def broken_analysis(_raw, *, country_context=None):
        raise RuntimeError("temporary provider outage")

    monkeypatch.setattr(scanner, "process_opportunity_candidate", broken_analysis)
    with pytest.raises(RuntimeError, match="classification=1"):
        scanner.run_scanner()
