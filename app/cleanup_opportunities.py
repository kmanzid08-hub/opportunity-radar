from __future__ import annotations

import argparse
from dataclasses import dataclass
from enum import Enum

from sqlalchemy import select

from app.database import SessionLocal
from app.filters import (
    STRONG_PROCUREMENT_TERMS,
    classify_opportunity_with_reason,
    contains_term,
    normalise_text,
)
from app.models import Opportunity
from app.schemas import RawOpportunity


class CleanupDisposition(str, Enum):
    SAFE_TO_ARCHIVE = "safe_to_archive"
    REVIEW_MANUALLY = "review_manually"
    PROTECTED = "protected"
    KEEP = "keep"


@dataclass(frozen=True)
class CleanupCandidate:
    opportunity_id: int
    title: str
    organisation_name: str | None
    source_url: str
    rejection_reason: str
    disposition: CleanupDisposition


AUTO_ARCHIVE_REASON_PREFIXES: tuple[str, ...] = (
    "commercial service page detected",
    "commercial/SEO service page detected",
    "promotional service page detected",
    "aggregate listing page contains multiple notices",
    "aggregate listing/search page was detected",
    "generic page title without enough opportunity evidence",
    "ordinary employment listing without procurement evidence",
    "no credible procurement or opportunity evidence was detected",
)

MANUAL_REVIEW_REASON_PREFIXES: tuple[str, ...] = (
    "opportunity confidence score",
)

STRONG_TITLE_OPPORTUNITY_TERMS: tuple[str, ...] = (
    "tender",
    "request for proposal",
    "request for proposals",
    "request for quotation",
    "request for quotations",
    "expression of interest",
    "expressions of interest",
    "invitation to bid",
    "invitation for bids",
    "invitation to tender",
    "call for proposals",
    "terms of reference",
    "procurement notice",
    "supplier registration",
    "prequalification",
    "framework agreement",
    "framework contract",
    "rfp",
    "rfq",
    "eoi",
    "itb",
    "itt",
)


def build_raw_opportunity(
    opportunity: Opportunity,
) -> RawOpportunity:
    return RawOpportunity(
        organisation_name=opportunity.organisation_name,
        title=opportunity.title,
        source_name=opportunity.source_name,
        source_url=opportunity.source_url,
        description=opportunity.description,
        deadline=opportunity.deadline,
    )


def has_strong_title_evidence(
    opportunity: Opportunity,
) -> bool:
    title = normalise_text(opportunity.title)

    return any(
        contains_term(title, term)
        for term in STRONG_TITLE_OPPORTUNITY_TERMS
    )


def has_strong_notice_evidence(
    opportunity: Opportunity,
) -> bool:
    text = normalise_text(
        " ".join(
            [
                opportunity.title or "",
                opportunity.description or "",
                opportunity.source_name or "",
                opportunity.source_url or "",
            ]
        )
    )

    return any(
        contains_term(text, term)
        for term in STRONG_PROCUREMENT_TERMS
    )


def classify_cleanup_disposition(
    opportunity: Opportunity,
    rejection_reason: str,
) -> CleanupDisposition:
    """
    Decide how aggressively an existing rejected record may be cleaned.

    Existing data is handled more conservatively than newly discovered data.
    """

    reason = rejection_reason.strip()

    # Pipeline items are never modified automatically.
    if opportunity.is_lead:
        return CleanupDisposition.PROTECTED

    # Strong opportunity language in the title always forces manual review,
    # even if the current classifier rejects the record.
    if has_strong_title_evidence(opportunity):
        return CleanupDisposition.REVIEW_MANUALLY

    # Low-confidence rejection alone is not enough to auto-archive.
    if reason.startswith(MANUAL_REVIEW_REASON_PREFIXES):
        return CleanupDisposition.REVIEW_MANUALLY

    # If the body contains explicit procurement language, err on the side
    # of manual review unless the rejection reason is an unmistakable
    # commercial/SEO page classification.
    if has_strong_notice_evidence(opportunity):
        if reason.startswith(
            (
                "commercial service page detected",
                "commercial/SEO service page detected",
                "promotional service page detected",
            )
        ):
            return CleanupDisposition.SAFE_TO_ARCHIVE

        return CleanupDisposition.REVIEW_MANUALLY

    if reason.startswith(AUTO_ARCHIVE_REASON_PREFIXES):
        return CleanupDisposition.SAFE_TO_ARCHIVE

    return CleanupDisposition.REVIEW_MANUALLY


def inspect_opportunities() -> tuple[
    list[CleanupCandidate],
    dict[str, int],
]:
    candidates: list[CleanupCandidate] = []

    stats = {
        "total": 0,
        "pipeline_protected": 0,
        "accepted": 0,
        "safe_to_archive": 0,
        "review_manually": 0,
    }

    with SessionLocal() as db:
        opportunities = list(
            db.scalars(
                select(Opportunity).order_by(
                    Opportunity.id.asc()
                )
            ).all()
        )

        stats["total"] = len(opportunities)

        for opportunity in opportunities:
            if opportunity.is_lead:
                stats["pipeline_protected"] += 1
                continue

            decision = classify_opportunity_with_reason(
                build_raw_opportunity(opportunity)
            )

            if decision.opportunity is not None:
                stats["accepted"] += 1
                continue

            rejection_reason = (
                decision.rejection_reason
                or "Rejected by classifier"
            )

            disposition = classify_cleanup_disposition(
                opportunity,
                rejection_reason,
            )

            if disposition == CleanupDisposition.SAFE_TO_ARCHIVE:
                stats["safe_to_archive"] += 1
            else:
                stats["review_manually"] += 1

            candidates.append(
                CleanupCandidate(
                    opportunity_id=opportunity.id,
                    title=opportunity.title,
                    organisation_name=opportunity.organisation_name,
                    source_url=opportunity.source_url,
                    rejection_reason=rejection_reason,
                    disposition=disposition,
                )
            )

    return candidates, stats


def print_candidate(
    index: int,
    candidate: CleanupCandidate,
) -> None:
    print("-" * 88)
    print(
        f"[{index}] ID {candidate.opportunity_id} "
        f"[{candidate.disposition.value.upper()}]"
    )
    print(f"Title: {candidate.title}")
    print(
        "Organisation: "
        f"{candidate.organisation_name or 'Not specified'}"
    )
    print(f"Reason: {candidate.rejection_reason}")
    print(f"Source: {candidate.source_url}")


def print_report(
    candidates: list[CleanupCandidate],
    stats: dict[str, int],
    *,
    show_review: bool,
) -> None:
    print("=" * 88)
    print("Opportunity Radar - Conservative Opportunity Cleanup")
    print("=" * 88)
    print()
    print(f"Total opportunities:          {stats['total']}")
    print(
        "Pipeline protected:           "
        f"{stats['pipeline_protected']}"
    )
    print(f"Still accepted:               {stats['accepted']}")
    print(
        "SAFE TO ARCHIVE:              "
        f"{stats['safe_to_archive']}"
    )
    print(
        "REVIEW MANUALLY:              "
        f"{stats['review_manually']}"
    )

    safe_candidates = [
        item
        for item in candidates
        if item.disposition
        == CleanupDisposition.SAFE_TO_ARCHIVE
    ]

    review_candidates = [
        item
        for item in candidates
        if item.disposition
        == CleanupDisposition.REVIEW_MANUALLY
    ]

    print()
    print("=" * 88)
    print("SAFE TO ARCHIVE")
    print("=" * 88)

    if not safe_candidates:
        print("No records are currently safe to archive automatically.")
    else:
        for index, candidate in enumerate(
            safe_candidates,
            start=1,
        ):
            print_candidate(index, candidate)

    if show_review:
        print()
        print("=" * 88)
        print("REVIEW MANUALLY - WILL NOT BE CHANGED BY --apply")
        print("=" * 88)

        if not review_candidates:
            print("No manual-review records.")
        else:
            for index, candidate in enumerate(
                review_candidates,
                start=1,
            ):
                print_candidate(index, candidate)

    print()
    print("=" * 88)
    print(
        f"Safe automatic archive count: {len(safe_candidates)}"
    )
    print(
        f"Manual review count:           {len(review_candidates)}"
    )
    print("=" * 88)


def apply_cleanup(
    candidates: list[CleanupCandidate],
) -> int:
    safe_ids = {
        candidate.opportunity_id
        for candidate in candidates
        if candidate.disposition
        == CleanupDisposition.SAFE_TO_ARCHIVE
    }

    if not safe_ids:
        return 0

    updated = 0

    with SessionLocal() as db:
        opportunities = list(
            db.scalars(
                select(Opportunity).where(
                    Opportunity.id.in_(safe_ids)
                )
            ).all()
        )

        for opportunity in opportunities:
            # Protect pipeline items again at write time.
            if opportunity.is_lead:
                print(
                    "[PROTECTED] "
                    f"{opportunity.id}: {opportunity.title}"
                )
                continue

            # Re-run the current classifier immediately before modifying.
            decision = classify_opportunity_with_reason(
                build_raw_opportunity(opportunity)
            )

            if decision.opportunity is not None:
                print(
                    "[SKIPPED - NOW ACCEPTED] "
                    f"{opportunity.id}: {opportunity.title}"
                )
                continue

            rejection_reason = (
                decision.rejection_reason
                or "Rejected by classifier"
            )

            disposition = classify_cleanup_disposition(
                opportunity,
                rejection_reason,
            )

            if disposition != CleanupDisposition.SAFE_TO_ARCHIVE:
                print(
                    "[SKIPPED - REQUIRES REVIEW] "
                    f"{opportunity.id}: {opportunity.title}"
                )
                continue

            opportunity.status = "Rejected"
            opportunity.is_expired = True

            updated += 1

            print(
                "[ARCHIVED] "
                f"{opportunity.id}: {opportunity.title}"
            )

        db.commit()

    return updated


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Conservatively re-evaluate stored Opportunity Radar "
            "records using the current classifier."
        )
    )

    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Archive only records classified SAFE TO ARCHIVE. "
            "Manual-review and pipeline records are never modified."
        ),
    )

    parser.add_argument(
        "--show-review",
        action="store_true",
        help=(
            "Also print the full manual-review group. "
            "By default the dry run prints only the safe group "
            "and summary counts."
        ),
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    candidates, stats = inspect_opportunities()

    print_report(
        candidates,
        stats,
        show_review=args.show_review,
    )

    if not args.apply:
        print()
        print("DRY RUN ONLY.")
        print("No database records were changed.")
        print()
        print(
            "The --apply option will modify ONLY the "
            "SAFE TO ARCHIVE group."
        )
        print(
            "REVIEW MANUALLY and Pipeline records remain untouched."
        )
        return 0

    print()
    print("APPLY MODE ENABLED.")
    print(
        "Re-checking every safe candidate before modifying it..."
    )
    print()

    updated = apply_cleanup(candidates)

    print()
    print("=" * 88)
    print(
        f"Cleanup complete. {updated} opportunities archived."
    )
    print(
        "Pipeline and manual-review opportunities were not modified."
    )
    print("=" * 88)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
