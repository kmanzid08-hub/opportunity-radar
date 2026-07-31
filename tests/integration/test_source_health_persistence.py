from __future__ import annotations

import pytest

from app import source_health
from app.models import Source
from app.source_health import SourceHealthManager


def _persist_source(session_factory, **overrides: object) -> int:
    values: dict[str, object] = {
        "organisation_name": "Example Buyer",
        "base_url": "https://example.test",
        "monitor_url": "https://example.test/opportunities",
        "domain": "example.test",
        "source_type": "Organisation",
        "confidence_score": 80.0,
        "url_relevance_score": 50.0,
        "total_opportunities_found": 3,
        "consecutive_failures": 3,
        "is_active": True,
        "is_auto_disabled": True,
        "disabled_reason": "Earlier failure",
    }
    values.update(overrides)

    with session_factory() as session:
        source = Source(**values)
        session.add(source)
        session.commit()
        return source.id


def test_record_success_resets_health_and_updates_yield(
    isolated_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        source_health,
        "SessionLocal",
        isolated_session_factory,
    )
    source_id = _persist_source(isolated_session_factory)

    SourceHealthManager().record_success(
        source_id=source_id,
        duration_seconds=1.23456,
        http_status=200,
        opportunities_found=2,
    )

    with isolated_session_factory() as session:
        source = session.get(Source, source_id)
        assert source is not None
        assert source.consecutive_failures == 0
        assert source.is_auto_disabled is False
        assert source.disabled_reason is None
        assert source.total_opportunities_found == 5
        assert source.last_scan_duration_seconds == 1.235
        assert source.last_http_status == 200
        assert source.last_successful_scan_at is not None
        assert source.last_opportunity_found_at is not None
        assert source.priority_score == 69.0


def test_tenth_failure_disables_source(
    isolated_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        source_health,
        "SessionLocal",
        isolated_session_factory,
    )
    source_id = _persist_source(
        isolated_session_factory,
        consecutive_failures=9,
        is_auto_disabled=False,
        disabled_reason=None,
    )

    SourceHealthManager().record_failure(
        source_id=source_id,
        duration_seconds=2.34567,
        http_status=503,
        error_message="Service unavailable",
    )

    with isolated_session_factory() as session:
        source = session.get(Source, source_id)
        assert source is not None
        assert source.consecutive_failures == 10
        assert source.is_active is False
        assert source.is_auto_disabled is True
        assert source.last_scan_duration_seconds == 2.346
        assert source.last_http_status == 503
        assert "Service unavailable" in (source.disabled_reason or "")
        assert source.priority_score == 0.0
