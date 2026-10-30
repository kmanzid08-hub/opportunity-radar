from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from sqlalchemy import event, func, select

from app import ai_review, scheduled_discovery
from app.ai_verifier import AIVerificationResult
from app.models import (
    AIOpportunityVerification,
    AutomationJobState,
    Opportunity,
)


def test_scheduler_state_uses_migrated_orm_table(
    isolated_session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        scheduled_discovery,
        "SessionLocal",
        isolated_session_factory,
    )
    engine = isolated_session_factory.kw["bind"]
    statements: list[str] = []

    def record_statement(
        _connection,
        _cursor,
        statement: str,
        _parameters,
        _context,
        _executemany: bool,
    ) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record_statement)
    try:
        completed_at = datetime(2026, 9, 13, 12, tzinfo=timezone.utc)
        scheduled_discovery._record_success(completed_at)
        assert scheduled_discovery._last_success() == completed_at

        later = completed_at + timedelta(hours=1)
        scheduled_discovery._record_success(later)
        assert scheduled_discovery._last_success() == later
    finally:
        event.remove(engine, "before_cursor_execute", record_statement)

    with isolated_session_factory() as db:
        assert db.scalar(
            select(func.count()).select_from(AutomationJobState)
        ) == 1

    assert not any(
        statement.lstrip().upper().startswith(("CREATE", "ALTER", "DROP"))
        for statement in statements
    )


def test_ai_review_state_uses_migrated_orm_table(
    isolated_session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        ai_review,
        "SessionLocal",
        isolated_session_factory,
    )
    monkeypatch.setattr(ai_review, "get_model", lambda: "test-model")

    with isolated_session_factory() as db:
        opportunity = Opportunity(
            organisation_name="Example Buyer",
            title="Example opportunity",
            category="Consulting",
            source_name="Example Source",
            source_url="https://example.test/opportunity/1",
        )
        db.add(opportunity)
        db.commit()
        opportunity_id = opportunity.id

    result = AIVerificationResult(
        decision="ACCEPT",
        confidence=91,
        reason="A genuine individual notice.",
        opportunity_type="RFP",
        is_individual_notice=True,
        is_recruitment_or_job_portal=False,
        is_paid_or_subscription_aggregator=False,
        is_service_marketing_page=False,
        is_directory_or_listing_page=False,
        original_buyer_source_likely=True,
    )

    engine = isolated_session_factory.kw["bind"]
    statements: list[str] = []

    def record_statement(
        _connection,
        _cursor,
        statement: str,
        _parameters,
        _context,
        _executemany: bool,
    ) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record_statement)
    try:
        ai_review._record_result(opportunity_id, result)
        assert ai_review.reviewed_ids() == {opportunity_id}

        ai_review._record_result(
            opportunity_id,
            replace(result, decision="REVIEW", confidence=65),
        )
    finally:
        event.remove(engine, "before_cursor_execute", record_statement)

    with isolated_session_factory() as db:
        record = db.get(AIOpportunityVerification, opportunity_id)
        assert record is not None
        assert record.decision == "REVIEW"
        assert record.confidence == 65
        assert record.verifier_model == "test-model"
        assert db.scalar(
            select(func.count()).select_from(AIOpportunityVerification)
        ) == 1

    assert not any(
        statement.lstrip().upper().startswith(("CREATE", "ALTER", "DROP"))
        for statement in statements
    )
