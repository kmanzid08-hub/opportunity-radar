from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, text

from app.ai_verifier import (
    AIVerificationResult,
    get_model,
    verify_opportunity,
)
from app.database import SessionLocal, engine
from app.models import Opportunity


STATE_TABLE = "ai_opportunity_verification"
DEFAULT_BATCH_SIZE = 5


@dataclass(frozen=True)
class AIReviewSummary:
    requested: int
    reviewed: int
    accepted: int
    rejected: int
    needs_review: int
    unavailable: int
    remaining_unreviewed: int


def configured_batch_size() -> int:
    raw = os.getenv(
        "AI_REVIEW_BATCH_SIZE",
        str(DEFAULT_BATCH_SIZE),
    ).strip()

    try:
        value = int(raw)
    except ValueError:
        value = DEFAULT_BATCH_SIZE

    # Hard cap protects against an accidental expensive click.
    return max(1, min(20, value))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_ai_review_table() -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS {STATE_TABLE} (
                    opportunity_id INTEGER PRIMARY KEY,
                    decision VARCHAR(20) NOT NULL,
                    confidence INTEGER NOT NULL,
                    reason TEXT,
                    opportunity_type VARCHAR(200),
                    verifier_model VARCHAR(200),
                    verified_at TIMESTAMP WITH TIME ZONE NOT NULL
                )
                """
            )
        )


def reviewed_ids() -> set[int]:
    ensure_ai_review_table()

    with engine.begin() as connection:
        rows = connection.execute(
            text(
                f"""
                SELECT opportunity_id
                FROM {STATE_TABLE}
                """
            )
        ).scalars().all()

    return {int(value) for value in rows}


def count_remaining_unreviewed() -> int:
    done = reviewed_ids()

    with SessionLocal() as db:
        opportunities = db.scalars(
            select(Opportunity).where(
                Opportunity.status.notin_(
                    ("Rejected", "Expired")
                ),
                Opportunity.is_lead.is_(False),
            )
        ).all()

    return sum(
        1
        for opportunity in opportunities
        if opportunity.id not in done
    )


def _candidate_batch(
    limit: int,
) -> list[Opportunity]:
    done = reviewed_ids()

    with SessionLocal() as db:
        opportunities = list(
            db.scalars(
                select(Opportunity)
                .where(
                    Opportunity.status.notin_(
                        ("Rejected", "Expired")
                    ),
                    Opportunity.is_lead.is_(False),
                )
                .order_by(
                    Opportunity.first_discovered_at.desc(),
                    Opportunity.id.desc(),
                )
            ).all()
        )

        selected = [
            opportunity
            for opportunity in opportunities
            if opportunity.id not in done
        ][:limit]

        # Detach data before session closes.
        for opportunity in selected:
            db.expunge(opportunity)

    return selected


def _record_result(
    opportunity_id: int,
    result: AIVerificationResult,
) -> None:
    ensure_ai_review_table()

    with engine.begin() as connection:
        connection.execute(
            text(
                f"""
                INSERT INTO {STATE_TABLE} (
                    opportunity_id,
                    decision,
                    confidence,
                    reason,
                    opportunity_type,
                    verifier_model,
                    verified_at
                )
                VALUES (
                    :opportunity_id,
                    :decision,
                    :confidence,
                    :reason,
                    :opportunity_type,
                    :verifier_model,
                    :verified_at
                )
                ON CONFLICT (opportunity_id)
                DO UPDATE SET
                    decision = EXCLUDED.decision,
                    confidence = EXCLUDED.confidence,
                    reason = EXCLUDED.reason,
                    opportunity_type = EXCLUDED.opportunity_type,
                    verifier_model = EXCLUDED.verifier_model,
                    verified_at = EXCLUDED.verified_at
                """
            ),
            {
                "opportunity_id": opportunity_id,
                "decision": result.decision,
                "confidence": result.confidence,
                "reason": result.reason,
                "opportunity_type": result.opportunity_type,
                "verifier_model": get_model(),
                "verified_at": _utc_now(),
            },
        )


def _archive_false_positive(
    opportunity_id: int,
) -> bool:
    with SessionLocal() as db:
        opportunity = db.get(
            Opportunity,
            opportunity_id,
        )

        if opportunity is None:
            return False

        # Pipeline items are protected even if the database changed after
        # the candidate batch was selected.
        if opportunity.is_lead:
            return False

        opportunity.status = "Rejected"
        opportunity.updated_at = _utc_now()
        db.commit()

    return True


def run_ai_review(
    limit: int | None = None,
) -> AIReviewSummary:
    """
    Manually verify a small batch of already-saved opportunities.

    Claude is called ONLY when this function is explicitly invoked,
    normally from the dashboard button. Ordinary scanner runs never call it.
    """
    safe_limit = (
        configured_batch_size()
        if limit is None
        else max(1, min(20, int(limit)))
    )

    candidates = _candidate_batch(
        safe_limit
    )

    accepted = 0
    rejected = 0
    needs_review = 0
    unavailable = 0
    reviewed = 0

    print("=" * 70)
    print("Opportunity Radar - Manual AI verification")
    print("=" * 70)
    print(
        f"Batch limit: {safe_limit}"
    )
    print(
        f"Candidates selected: {len(candidates)}"
    )
    print(
        "Source scanning: OFF "
        "(reviewing saved database opportunities only)"
    )

    for opportunity in candidates:
        print("-" * 70)
        print(
            f"Checking ID {opportunity.id}: "
            f"{opportunity.title[:100]}"
        )

        result = verify_opportunity(
            opportunity,
            category=opportunity.category,
            rule_score=opportunity.match_score,
        )

        if not result.verifier_used:
            unavailable += 1
            print(
                "[AI VERIFIER UNAVAILABLE]"
            )
            print(
                f"Reason: {result.reason}"
            )
            # Do not record failures; this item can be retried next click.
            continue

        reviewed += 1

        print(
            f"Decision: {result.decision} "
            f"({result.confidence}%)"
        )
        print(
            f"Reason: {result.reason}"
        )
        print(
            "Auto-reject threshold: "
            f"{result.rejection_threshold}%"
        )

        if result.should_reject:
            if _archive_false_positive(
                opportunity.id
            ):
                rejected += 1
                print(
                    "[ARCHIVED AS FALSE POSITIVE]"
                )
            else:
                needs_review += 1
                print(
                    "[PROTECTED / NOT ARCHIVED]"
                )
        elif result.decision == "ACCEPT":
            accepted += 1
        else:
            needs_review += 1

        _record_result(
            opportunity.id,
            result,
        )

    remaining = count_remaining_unreviewed()

    print("=" * 70)
    print(
        "Manual AI verification complete"
    )
    print(
        f"Reviewed successfully: {reviewed}"
    )
    print(
        f"Accepted: {accepted}"
    )
    print(
        f"Rejected/archived: {rejected}"
    )
    print(
        f"Needs review: {needs_review}"
    )
    print(
        f"Verifier unavailable: {unavailable}"
    )
    print(
        f"Remaining unreviewed: {remaining}"
    )
    print("=" * 70)

    return AIReviewSummary(
        requested=len(candidates),
        reviewed=reviewed,
        accepted=accepted,
        rejected=rejected,
        needs_review=needs_review,
        unavailable=unavailable,
        remaining_unreviewed=remaining,
    )