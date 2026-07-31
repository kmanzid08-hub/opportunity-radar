from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import func, select

from app import scanner
from app.models import Opportunity
from app.schemas import FilteredOpportunity


def _filtered_opportunity(**overrides: object) -> FilteredOpportunity:
    values: dict[str, object] = {
        "organisation_name": " Example Buyer ",
        "title": "External audit services",
        "category": "Audit",
        "source_name": "Example Portal",
        "source_url": "https://example.test/notices/123",
        "match_score": 60,
        "match_reason": "external audit, request for proposals",
        "description": "Initial description",
        "deadline": date(2099, 12, 31),
    }
    values.update(overrides)
    return FilteredOpportunity(**values)


def test_duplicate_url_updates_one_record_and_preserves_workflow_status(
    isolated_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        scanner,
        "SessionLocal",
        isolated_session_factory,
    )
    original = _filtered_opportunity()

    assert scanner.save_opportunity(original) is True

    with isolated_session_factory() as session:
        stored = session.scalar(select(Opportunity))
        assert stored is not None
        stored.status = "Interested"
        session.commit()

    updated = _filtered_opportunity(
        organisation_name="Updated Buyer",
        title="Updated external audit services",
        description="Updated description",
        source_name="Updated Portal",
        match_score=75,
    )

    assert scanner.save_opportunity(updated) is False

    with isolated_session_factory() as session:
        assert session.scalar(
            select(func.count()).select_from(Opportunity)
        ) == 1
        stored = session.scalar(select(Opportunity))
        assert stored is not None
        assert stored.organisation_name == "Updated Buyer"
        assert stored.title == "Updated external audit services"
        assert stored.description == "Updated description"
        assert stored.source_name == "Updated Portal"
        assert stored.match_score == 75
        assert stored.status == "Interested"


def test_duplicate_with_empty_optional_values_preserves_stored_values(
    isolated_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        scanner,
        "SessionLocal",
        isolated_session_factory,
    )
    assert scanner.save_opportunity(_filtered_opportunity()) is True

    duplicate = _filtered_opportunity(
        organisation_name=None,
        description=None,
        deadline=None,
    )

    assert scanner.save_opportunity(duplicate) is False

    with isolated_session_factory() as session:
        stored = session.scalar(select(Opportunity))
        assert stored is not None
        assert stored.organisation_name == "Example Buyer"
        assert stored.description == "Initial description"
        assert stored.deadline == date(2099, 12, 31)


def test_same_title_at_different_urls_creates_distinct_records(
    isolated_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        scanner,
        "SessionLocal",
        isolated_session_factory,
    )

    assert scanner.save_opportunity(_filtered_opportunity()) is True
    assert scanner.save_opportunity(
        _filtered_opportunity(
            source_url="https://example.test/notices/456"
        )
    ) is True

    with isolated_session_factory() as session:
        assert session.scalar(
            select(func.count()).select_from(Opportunity)
        ) == 2
