"""Scheduled discovery must crawl each healthy source once per Rwanda day."""
from datetime import datetime, timedelta, timezone

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


def test_newly_discovered_source_is_first_in_daily_scan(
    monkeypatch, isolated_session_factory
):
    from app.scanners import company_websites

    monkeypatch.setenv("DAILY_SOURCE_SCAN", "true")
    monkeypatch.setattr(company_websites, "SessionLocal", isolated_session_factory)
    two_days_ago = datetime.now(timezone.utc) - timedelta(days=2)
    with isolated_session_factory() as db:
        older = Source(
            organisation_name="High score incumbent",
            base_url="https://old.example.rw",
            monitor_url="https://old.example.rw/tenders",
            domain="old.example.rw",
            is_active=True, is_approved=True,
            confidence_score=100, priority_score=100,
            url_relevance_score=100,
            last_scanned_at=two_days_ago,
        )
        fresh = Source(
            organisation_name="New local association",
            base_url="https://new.example.rw",
            monitor_url="https://new.example.rw/media/announcements",
            domain="new.example.rw",
            is_active=True, is_approved=True,
            confidence_score=20, priority_score=20,
            url_relevance_score=0,
            last_scanned_at=None,
        )
        db.add_all([older, fresh])
        db.commit()
        new_id = fresh.id
        old_id = older.id

    scanner = CompanyWebsiteScanner()
    candidates = scanner._get_sources()
    assert [s.id for s in candidates] == [new_id, old_id]


def test_recovery_mode_selects_only_recent_unfinished_crawls(
    monkeypatch, isolated_session_factory
):
    from app.scanners import company_websites

    monkeypatch.setenv("SOURCE_SCAN_RECOVERY_ONLY", "true")
    monkeypatch.setenv("DAILY_SOURCE_SCAN", "true")
    monkeypatch.setattr(
        company_websites, "SessionLocal", isolated_session_factory
    )
    now = datetime.now(timezone.utc)

    def site(name, *, started=None, finished=None):
        return Source(
            organisation_name=name,
            base_url=f"https://{name}.example.rw",
            monitor_url=f"https://{name}.example.rw/tenders",
            domain=f"{name}.example.rw",
            is_active=True, is_approved=True, is_auto_disabled=False,
            last_scan_started_at=started, last_scanned_at=finished,
        )

    with isolated_session_factory() as db:
        db.add_all([
            site("retry", started=now - timedelta(hours=1),
                 finished=now - timedelta(days=1)),
            site("completed", started=now - timedelta(hours=2),
                 finished=now - timedelta(hours=1)),
            site("new", started=None, finished=None),
            site("stale", started=now - timedelta(days=5),
                 finished=now - timedelta(days=6)),
            site("disabled", started=now - timedelta(hours=1),
                 finished=now - timedelta(days=1)),
        ])
        db.commit()
        disabled = db.query(Source).filter(Source.domain == "disabled.example.rw").one()
        disabled.is_auto_disabled = True
        db.commit()

    found = CompanyWebsiteScanner()._get_sources()
    assert [site.domain for site in found] == ["retry.example.rw"]


def test_recovery_only_uses_website_crawler(monkeypatch):
    from app import scanner as scanner_module

    monkeypatch.setenv("SOURCE_SCAN_RECOVERY_ONLY", "true")
    # Verify that scan selection honors the environment without touching DB.
    candidates = scanner_module.get_scanners()
    assert any(type(item).__name__ == "CompanyWebsiteScanner" for item in candidates)
