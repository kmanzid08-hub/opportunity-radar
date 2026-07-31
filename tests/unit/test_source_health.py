from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.models import Source
from app.source_health import SourceHealthManager
from app.source_quality import SourceQualityEvaluator


def _source(**overrides: object) -> Source:
    values: dict[str, object] = {
        "organisation_name": "Example Buyer",
        "base_url": "https://example.test",
        "monitor_url": "https://example.test/opportunities",
        "domain": "example.test",
        "source_type": "Organisation",
        "confidence_score": 80.0,
        "url_relevance_score": 50.0,
        "total_opportunities_found": 4,
        "consecutive_failures": 2,
        "is_auto_disabled": False,
        "last_successful_scan_at": datetime.now(),
    }
    values.update(overrides)
    return Source(**values)


def test_source_health_priority_uses_current_weighting() -> None:
    priority = SourceHealthManager().calculate_priority(_source())

    assert priority == 57.0


def test_opportunity_yield_and_failure_adjustments_are_capped() -> None:
    manager = SourceHealthManager()

    assert manager.calculate_priority(
        _source(
            confidence_score=0,
            url_relevance_score=0,
            total_opportunities_found=100,
            consecutive_failures=0,
            last_successful_scan_at=None,
        )
    ) == 20.0
    assert manager.calculate_priority(
        _source(
            confidence_score=0,
            url_relevance_score=0,
            total_opportunities_found=0,
            consecutive_failures=100,
            last_successful_scan_at=None,
        )
    ) == 0.0


def test_auto_disabled_source_always_has_zero_priority() -> None:
    source = _source(
        confidence_score=100,
        url_relevance_score=100,
        total_opportunities_found=100,
        consecutive_failures=0,
        is_auto_disabled=True,
    )

    assert SourceHealthManager().calculate_priority(source) == 0.0


def test_success_recency_does_not_currently_affect_priority() -> None:
    recent = _source(last_successful_scan_at=datetime.now())
    stale = _source(
        last_successful_scan_at=datetime.now() - timedelta(days=365)
    )

    manager = SourceHealthManager()
    assert manager.calculate_priority(recent) == manager.calculate_priority(
        stale
    )


@pytest.mark.parametrize(
    ("source_type", "is_aggregator", "expected"),
    [
        ("Government Institution", False, 52.5),
        ("Government Institution", True, 42.5),
        ("Organisation", False, 42.5),
    ],
)
def test_source_quality_priority_characterization(
    source_type: str,
    is_aggregator: bool,
    expected: float,
) -> None:
    priority = SourceQualityEvaluator().calculate_priority(
        confidence_score=50,
        source_type=source_type,
        url_relevance_score=40,
        is_aggregator=is_aggregator,
    )

    assert priority == expected
