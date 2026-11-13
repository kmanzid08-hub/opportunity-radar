"""Match existing verified shared notices after a company profile is created.

A new business can otherwise wait for every source to become scan-due again.
This bounded background worker is part of the existing scheduled scan; it
never changes source dates or the private pursuit state of any workspace.
"""
from __future__ import annotations

from datetime import datetime, timezone
import logging
import os

from sqlalchemy import or_, select

from app.database import SessionLocal
from app.intelligence.match_store import match_is_current
from app.intelligence.matching_engine import (
    CompanyMatchProcessingStatus,
    load_active_company_profiles,
    match_verified_opportunity,
)
from app.intelligence.opportunity_pipeline import (
    OpportunityProcessingStatus,
    load_canonical_analyses,
    opportunity_quality_status,
)
from app.intelligence.triage_engine import triage_verified_opportunity
from app.models import CompanyOpportunityMatch, Opportunity

log = logging.getLogger("opportunity_radar.backfill")


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    profiles = load_active_company_profiles(None)
    if not profiles:
        log.warning(
            "No active business profiles exist. Notices are saved, but "
            "qualified recommendations require a company profile."
        )
        return

    try:
        limit = max(1, min(25, int(os.getenv("MATCH_BACKFILL_LIMIT", "10"))))
    except ValueError:
        limit = 10

    today = datetime.now(timezone.utc).date()
    with SessionLocal() as db:
        opportunities = list(
            db.scalars(
                select(Opportunity)
                .where(
                    Opportunity.workspace_id.is_(None),
                    Opportunity.is_expired.is_(False),
                    or_(Opportunity.deadline.is_(None), Opportunity.deadline >= today),
                )
                .order_by(
                    Opportunity.deadline.asc().nulls_last(),
                    Opportunity.last_seen_at.desc(),
                )
                .limit(500)
            )
        )
        ids = {opportunity.id for opportunity in opportunities}
        analyses = load_canonical_analyses(db, ids)
        existing_matches = {
            (match.organization_id, match.opportunity_id): match
            for match in db.scalars(
                select(CompanyOpportunityMatch).where(
                    CompanyOpportunityMatch.opportunity_id.in_(ids)
                )
            )
        }

        todo = []
        for opportunity in opportunities:
            analysis = analyses.get(opportunity.id)
            if (
                analysis is None
                or opportunity_quality_status(analysis)
                is not OpportunityProcessingStatus.VERIFIED
            ):
                continue
            if all(
                match_is_current(
                    existing_matches[(profile.company_id, opportunity.id)],
                    profile=profile,
                    analysis=analysis,
                )
                for profile in profiles
                if (profile.company_id, opportunity.id) in existing_matches
            ) and all(
                (profile.company_id, opportunity.id) in existing_matches
                for profile in profiles
            ):
                continue
            todo.append(opportunity.id)

    log.info(
        "Backfill: %s active companies; %s current shared notices; "
        "%s verified notices needing matching; processing up to %s",
        len(profiles), len(opportunities), len(todo), limit,
    )
    completed = 0
    matched = 0
    errors = 0
    for opportunity_id in todo[:limit]:
        try:
            outcomes = match_verified_opportunity(
                opportunity_id, profiles=profiles
            )
            matched += sum(item.qualifies_for_feed for item in outcomes)
            errors += sum(
                item.status is CompanyMatchProcessingStatus.ERROR
                for item in outcomes
            )
            triage_verified_opportunity(
                opportunity_id, profiles=profiles, matches=outcomes
            )
            completed += 1
        except Exception:
            errors += 1
            log.exception("Backfill failed for opportunity_id=%s", opportunity_id)
    log.info(
        "Backfill complete: processed=%s qualifying_matches=%s errors=%s remaining=%s",
        completed, matched, errors, max(0, len(todo) - limit),
    )


if __name__ == "__main__":
    main()
