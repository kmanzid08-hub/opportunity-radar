from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models import Opportunity, Source


def test_empty_dashboard_is_rendered(client) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "Opportunity Radar" in response.text
    assert "No opportunities found" in response.text


def _opportunity(
    *,
    title: str,
    now: datetime,
    deadline_offset_days: int | None,
    last_seen_age_days: int,
    is_expired: bool = False,
    is_lead: bool = False,
) -> Opportunity:
    deadline = (
        now.date() + timedelta(days=deadline_offset_days)
        if deadline_offset_days is not None
        else None
    )
    last_seen_at = now - timedelta(
        days=last_seen_age_days
    )

    return Opportunity(
        organisation_name="Example Buyer",
        title=title,
        category="Consulting",
        description="Inbox policy test record.",
        deadline=deadline,
        source_name="Example Source",
        source_url=(
            "https://example.test/"
            + title.lower().replace(" ", "-")
        ),
        match_reason="test",
        match_score=60,
        status="Expired" if is_expired else "New",
        is_expired=is_expired,
        is_lead=is_lead,
        first_discovered_at=last_seen_at,
        last_seen_at=last_seen_at,
        updated_at=last_seen_at,
        created_at=last_seen_at,
    )


def _source(
    *,
    name: str,
    first_discovered_at: datetime,
    last_scanned_at: datetime | None = None,
    last_successful_scan_at: datetime | None = None,
) -> Source:
    return Source(
        organisation_name=name,
        base_url=f"https://{name}.example.test",
        monitor_url=f"https://{name}.example.test/opportunities",
        domain=f"{name}.example.test",
        source_type="Organisation",
        confidence_score=80.0,
        is_active=True,
        is_approved=True,
        first_discovered_at=first_discovered_at,
        last_scanned_at=last_scanned_at,
        last_successful_scan_at=last_successful_scan_at,
    )


def test_dashboard_shows_operational_metrics(
    client,
    isolated_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import main

    now = datetime(
        2026,
        8,
        4,
        12,
        0,
        tzinfo=timezone.utc,
    )
    monkeypatch.setattr(
        main,
        "current_utc_time",
        lambda: now,
    )

    opportunities = (
        _opportunity(
            title="New today and expiring",
            now=now,
            deadline_offset_days=3,
            last_seen_age_days=0,
        ),
        _opportunity(
            title="New this week without deadline",
            now=now,
            deadline_offset_days=None,
            last_seen_age_days=1,
        ),
        _opportunity(
            title="Older active opportunity",
            now=now,
            deadline_offset_days=20,
            last_seen_age_days=10,
        ),
        _opportunity(
            title="Expired archived opportunity",
            now=now,
            deadline_offset_days=-1,
            last_seen_age_days=10,
            is_expired=True,
        ),
        _opportunity(
            title="Stale archived opportunity",
            now=now,
            deadline_offset_days=None,
            last_seen_age_days=46,
        ),
        _opportunity(
            title="Expired pipeline opportunity",
            now=now,
            deadline_offset_days=-2,
            last_seen_age_days=10,
            is_expired=True,
            is_lead=True,
        ),
    )

    successful_scan_at = now - timedelta(hours=1)
    failed_scan_at = now - timedelta(hours=2)
    yesterday = now - timedelta(days=1)
    sources = (
        _source(
            name="successful-today",
            first_discovered_at=now,
            last_scanned_at=successful_scan_at,
            last_successful_scan_at=successful_scan_at,
        ),
        _source(
            name="failed-today",
            first_discovered_at=now,
            last_scanned_at=failed_scan_at,
            last_successful_scan_at=yesterday,
        ),
        _source(
            name="new-unscanned",
            first_discovered_at=now,
        ),
        _source(
            name="old-success",
            first_discovered_at=yesterday,
            last_scanned_at=yesterday,
            last_successful_scan_at=yesterday,
        ),
    )

    with isolated_session_factory() as session:
        session.add_all((*opportunities, *sources))
        session.commit()

    response = client.get("/")

    assert response.status_code == 200
    assert response.context["dashboard_metrics"] == {
        "new_today": 1,
        "new_this_week": 2,
        "active": 3,
        "expiring_soon": 1,
        "expired": 2,
        "archived": 2,
        "pipeline": 1,
        "sources_scanned_today": 2,
        "sources_discovered_today": 3,
        "scan_success_rate": 50.0,
    }
    assert "New Today" in response.text
    assert "Sources Scanned Today" in response.text
    assert "Scan Success Rate" in response.text
    assert "50.0%" in response.text


def test_inbox_archives_stale_records_but_retains_pipeline(
    client,
    isolated_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import main

    now = datetime(
        2026,
        8,
        4,
        12,
        0,
        tzinfo=timezone.utc,
    )
    monkeypatch.setattr(
        main,
        "current_utc_time",
        lambda: now,
    )

    records = (
        _opportunity(
            title="Expired outside pipeline",
            now=now,
            deadline_offset_days=-1,
            last_seen_age_days=1,
            is_expired=True,
        ),
        _opportunity(
            title="Expired pipeline record",
            now=now,
            deadline_offset_days=-1,
            last_seen_age_days=100,
            is_expired=True,
            is_lead=True,
        ),
        _opportunity(
            title="Stale undated outside pipeline",
            now=now,
            deadline_offset_days=None,
            last_seen_age_days=46,
        ),
        _opportunity(
            title="Fresh undated opportunity",
            now=now,
            deadline_offset_days=None,
            last_seen_age_days=44,
        ),
        _opportunity(
            title="Stale undated pipeline record",
            now=now,
            deadline_offset_days=None,
            last_seen_age_days=100,
            is_lead=True,
        ),
    )

    with isolated_session_factory() as session:
        session.add_all(records)
        session.commit()

    inbox_response = client.get("/")

    assert inbox_response.status_code == 200
    assert "Expired outside pipeline" not in inbox_response.text
    assert "Stale undated outside pipeline" not in inbox_response.text
    assert "Expired pipeline record" in inbox_response.text
    assert "Fresh undated opportunity" in inbox_response.text
    assert "Stale undated pipeline record" in inbox_response.text
    assert "Active" in inbox_response.text

    archived_response = client.get(
        "/?deadline_filter=archived"
    )

    assert archived_response.status_code == 200
    assert "Expired outside pipeline" in archived_response.text
    assert "Stale undated outside pipeline" in archived_response.text
    assert "Expired pipeline record" not in archived_response.text
    assert "Stale undated pipeline record" not in archived_response.text


def test_refresh_restores_undated_opportunity_to_inbox(
    client,
    isolated_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import main

    now = datetime(
        2026,
        8,
        4,
        12,
        0,
        tzinfo=timezone.utc,
    )
    monkeypatch.setattr(
        main,
        "current_utc_time",
        lambda: now,
    )

    stale = _opportunity(
        title="Refreshable undated opportunity",
        now=now,
        deadline_offset_days=None,
        last_seen_age_days=46,
    )

    with isolated_session_factory() as session:
        session.add(stale)
        session.commit()
        opportunity_id = stale.id

    assert (
        "Refreshable undated opportunity"
        not in client.get("/").text
    )

    with isolated_session_factory() as session:
        stored = session.get(
            Opportunity,
            opportunity_id,
        )
        assert stored is not None
        stored.last_seen_at = now
        session.commit()

    assert (
        "Refreshable undated opportunity"
        in client.get("/").text
    )
