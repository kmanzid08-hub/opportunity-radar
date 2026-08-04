from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models import Opportunity


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
    assert "Inbox Opportunities" in inbox_response.text

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
