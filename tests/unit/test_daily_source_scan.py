"""Scheduled discovery must crawl each healthy source once per Rwanda day."""
from datetime import datetime, timezone

from app.models import Source
from app.scanners.company_websites import CompanyWebsiteScanner


def source(*, last_scanned_at, failures=0, interval=48):
    return Source(
        organisation_name="Rwanda Procurement Organisation",
        base_url="https://agency.example.test",
        monitor_url="https://agency.example.test/tenders",
        domain="agency.example.test",
        is_active=True,
        is_approved=True,
        consecutive_failures=failures,
        scan_interval_hours=interval,
        last_scanned_at=last_scanned_at,
    )


def test_new_kigali_day_due_even_before_24_hours(monkeypatch):
    monkeypatch.setenv("DAILY_SOURCE_SCAN", "true")
    now = datetime(2026, 11, 14, 4, tzinfo=timezone.utc)
    yesterday = datetime(2026, 11, 13, 5, tzinfo=timezone.utc)
    assert CompanyWebsiteScanner()._source_is_due(
        source(last_scanned_at=yesterday), now
    )


def test_same_kigali_day_skips_repeat_crawl(monkeypatch):
    monkeypatch.setenv("DAILY_SOURCE_SCAN", "true")
    now = datetime(2026, 11, 14, 4, tzinfo=timezone.utc)
    today = datetime(2026, 11, 14, 1, tzinfo=timezone.utc)
    assert not CompanyWebsiteScanner()._source_is_due(
        source(last_scanned_at=today), now
    )


def test_repeat_failed_sources_keep_backoff(monkeypatch):
    monkeypatch.setenv("DAILY_SOURCE_SCAN", "true")
    now = datetime(2026, 11, 14, 4, tzinfo=timezone.utc)
    yesterday = datetime(2026, 11, 13, 5, tzinfo=timezone.utc)
    assert not CompanyWebsiteScanner()._source_is_due(
        source(last_scanned_at=yesterday, failures=5, interval=48), now
    )


def test_manual_adaptive_mode_keeps_interval_semantics(monkeypatch):
    monkeypatch.delenv("DAILY_SOURCE_SCAN", raising=False)
    now = datetime(2026, 11, 14, 4, tzinfo=timezone.utc)
    yesterday = datetime(2026, 11, 13, 5, tzinfo=timezone.utc)
    assert not CompanyWebsiteScanner()._source_is_due(
        source(last_scanned_at=yesterday), now
    )


def test_never_scanned_sources_are_immediately_eligible(monkeypatch):
    monkeypatch.setenv("DAILY_SOURCE_SCAN", "true")
    assert CompanyWebsiteScanner()._source_is_due(
        source(last_scanned_at=None),
        datetime(2026, 11, 14, 4, tzinfo=timezone.utc),
    )
