from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import select

from app.database import SessionLocal
from app.intelligence.opportunity_pipeline import (
    OpportunityProcessingStatus,
    link_analysis_to_saved_opportunity,
    process_opportunity_candidate,
)
from app.intelligence.matching_engine import (
    CompanyMatchProcessingStatus,
    CompanyProfileInput,
    load_active_company_profiles,
    match_verified_opportunity,
)
from app.models import Opportunity, Source
from app.intelligence.triage_engine import triage_verified_opportunity
from app.schemas import FilteredOpportunity, RawOpportunity
from app.scanners.base import BaseScanner
from app.scanners.company_websites import CompanyWebsiteScanner
from app.scanners.job_in_rwanda import JobInRwandaScanner


ACTIVE_STATUSES: tuple[str, ...] = (
    "New",
    "Seen",
    "Interested",
)


def current_utc_time() -> datetime:
    return datetime.now(timezone.utc)


def approve_existing_sources() -> int:
    """
    Remove the manual approval step for existing active websites.
    """
    approved_count = 0

    with SessionLocal() as db:
        sources = db.scalars(
            select(Source).where(
                Source.is_active.is_(True),
            )
        ).all()

        for source in sources:
            if not source.is_approved:
                source.is_approved = True
                approved_count += 1

            if source.scan_interval_hours is None:
                source.scan_interval_hours = 12

            if source.priority_score is None:
                source.priority_score = 50.0

            if source.url_relevance_score is None:
                source.url_relevance_score = 0.0

            if source.total_opportunities_found is None:
                source.total_opportunities_found = 0

            if source.is_auto_disabled is None:
                source.is_auto_disabled = False

        db.commit()

    return approved_count


def opportunity_has_expired(
    deadline: date | None,
) -> bool:
    if deadline is None:
        return False

    return deadline < date.today()


def clean_organisation_name(
    organisation_name: str | None,
) -> str | None:
    if not organisation_name:
        return None

    cleaned = organisation_name.strip()
    cleaned = cleaned.rstrip("-–—:;| ").strip()

    return cleaned or None


def update_expiry_status(
    opportunity: Opportunity,
) -> bool:
    expired = opportunity_has_expired(
        opportunity.deadline
    )

    record_changed = False

    if opportunity.is_expired != expired:
        opportunity.is_expired = expired
        record_changed = True

    if expired and opportunity.status in ACTIVE_STATUSES:
        opportunity.status = "Expired"
        record_changed = True

    if not expired and opportunity.status == "Expired":
        opportunity.status = "New"
        record_changed = True

    return record_changed


def save_opportunity(
    opportunity: FilteredOpportunity,
) -> bool:
    now = current_utc_time()

    with SessionLocal() as db:
        existing = db.scalar(
            select(Opportunity).where(
                Opportunity.source_url
                == opportunity.source_url
            )
        )

        cleaned_organisation = clean_organisation_name(
            opportunity.organisation_name
        )

        if existing is not None:
            record_changed = False
            existing.last_seen_at = now

            if (
                cleaned_organisation
                and existing.organisation_name
                != cleaned_organisation
            ):
                existing.organisation_name = (
                    cleaned_organisation
                )
                record_changed = True

            if (
                opportunity.title
                and existing.title != opportunity.title
            ):
                existing.title = opportunity.title
                record_changed = True

            if (
                opportunity.description
                and existing.description
                != opportunity.description
            ):
                existing.description = (
                    opportunity.description
                )
                record_changed = True

            if (
                opportunity.deadline is not None
                and existing.deadline
                != opportunity.deadline
            ):
                existing.deadline = opportunity.deadline
                record_changed = True

            if (
                existing.source_name
                != opportunity.source_name
            ):
                existing.source_name = (
                    opportunity.source_name
                )
                record_changed = True

            if existing.category != opportunity.category:
                existing.category = opportunity.category
                record_changed = True

            if (
                existing.match_reason
                != opportunity.match_reason
            ):
                existing.match_reason = (
                    opportunity.match_reason
                )
                record_changed = True

            if (
                existing.match_score
                != opportunity.match_score
            ):
                existing.match_score = (
                    opportunity.match_score
                )
                record_changed = True

            if update_expiry_status(existing):
                record_changed = True

            if record_changed:
                existing.updated_at = now

            db.commit()
            return False

        expired = opportunity_has_expired(
            opportunity.deadline
        )

        initial_status = "Expired" if expired else "New"

        database_record = Opportunity(
            organisation_name=cleaned_organisation,
            title=opportunity.title,
            category=opportunity.category,
            description=opportunity.description,
            deadline=opportunity.deadline,
            source_name=opportunity.source_name,
            source_url=opportunity.source_url,
            match_reason=opportunity.match_reason,
            match_score=opportunity.match_score,
            status=initial_status,
            is_expired=expired,
            first_discovered_at=now,
            last_seen_at=now,
            updated_at=now,
        )

        db.add(database_record)
        db.commit()

        return True


def refresh_duplicate_opportunity(
    opportunity_id: int,
    raw_opportunity: RawOpportunity,
) -> None:
    """Refresh safe fields while retaining the canonical source URL."""
    with SessionLocal() as db:
        opportunity = db.get(Opportunity, opportunity_id)
        if opportunity is None:
            return

        description = getattr(raw_opportunity, "description", None)
        deadline = getattr(raw_opportunity, "deadline", None)
        organisation_name = clean_organisation_name(
            getattr(raw_opportunity, "organisation_name", None)
        )
        opportunity.last_seen_at = current_utc_time()
        if not opportunity.description and description:
            opportunity.description = str(description)
        if opportunity.deadline is None and deadline is not None:
            opportunity.deadline = deadline
        if not opportunity.organisation_name and organisation_name:
            opportunity.organisation_name = organisation_name
        update_expiry_status(opportunity)
        db.commit()


def match_saved_opportunity(
    opportunity_id: int,
    profiles: list[CompanyProfileInput],
) -> tuple[int, int]:
    outcomes = match_verified_opportunity(
        opportunity_id,
        profiles=profiles,
    )
    try:
        triage_outcomes = triage_verified_opportunity(
            opportunity_id, profiles=profiles, matches=outcomes,
        )
        for outcome in triage_outcomes:
            print(f"Triage opportunity={opportunity_id} company={outcome.company_id}: {outcome.status}")
    except Exception:
        # Matching/verification already committed; triage cannot invalidate them.
        print(f"Triage unavailable for opportunity={opportunity_id}")
    return (
        sum(
            outcome.status is CompanyMatchProcessingStatus.ERROR
            for outcome in outcomes
        ),
        sum(outcome.qualifies_for_feed for outcome in outcomes),
    )


def mark_expired_opportunities() -> int:
    updated_count = 0
    now = current_utc_time()

    with SessionLocal() as db:
        opportunities = db.scalars(
            select(Opportunity).where(
                Opportunity.deadline.is_not(None)
            )
        ).all()

        for opportunity in opportunities:
            if update_expiry_status(opportunity):
                opportunity.updated_at = now
                updated_count += 1

        if updated_count:
            db.commit()

    return updated_count


def get_scanners() -> list[BaseScanner]:
    return [
        JobInRwandaScanner(),
        CompanyWebsiteScanner(),
    ]


def get_scanner_name(
    scanner: BaseScanner,
) -> str:
    source_name = getattr(
        scanner,
        "source_name",
        None,
    )

    if source_name:
        return str(source_name)

    name = getattr(
        scanner,
        "name",
        None,
    )

    if name:
        return str(name)

    return scanner.__class__.__name__


def run_scanner() -> None:
    print(
        "Gemini opportunity intelligence: ON "
        "(explicit scanner operation only)"
    )

    approved_count = approve_existing_sources()
    expired_count = mark_expired_opportunities()
    scanners = get_scanners()
    try:
        company_profiles = load_active_company_profiles(None)
    except Exception as exc:
        company_profiles = []
        print(
            "Company profile loading failed: "
            f"{type(exc).__name__}: {exc}"
        )

    total_found = 0
    total_relevant = 0
    total_saved = 0
    total_updated_or_existing = 0
    total_scan_failures = 0
    total_classification_failures = 0
    total_save_failures = 0
    total_matching_failures = 0
    total_qualifying_matches = 0

    print("=" * 60)
    print("Opportunity Radar")
    print("=" * 60)

    if approved_count:
        print(
            f"Automatically approved "
            f"{approved_count} active website sources."
        )

    if expired_count:
        print(
            f"Marked {expired_count} stored "
            "opportunities as expired."
        )

    for source_scanner in scanners:
        scanner_name = get_scanner_name(
            source_scanner
        )

        print(f"\nScanning {scanner_name}...")

        try:
            raw_opportunities = source_scanner.scan()
        except Exception as exc:
            total_scan_failures += 1

            print(
                f"Scan failed for {scanner_name}: "
                f"{type(exc).__name__}: {exc}"
            )
            continue

        source_total = len(raw_opportunities)
        source_relevant = 0
        source_saved = 0

        total_found += source_total

        print(f"Found {source_total} possible listings")

        for raw_opportunity in raw_opportunities:
            try:
                intelligence = process_opportunity_candidate(
                    raw_opportunity,
                    country_context=raw_opportunity.country_context,
                )
            except Exception as exc:
                total_classification_failures += 1

                print(
                    "  Classification failed for: "
                    f"{raw_opportunity.title[:80]}"
                )
                print(
                    f"    {type(exc).__name__}: {exc}"
                )
                continue

            if intelligence.status is OpportunityProcessingStatus.ERROR:
                total_classification_failures += 1
                print(
                    "  Gemini analysis unavailable for: "
                    f"{raw_opportunity.title[:80]}"
                )
                continue

            if intelligence.status in {
                OpportunityProcessingStatus.REJECTED,
                OpportunityProcessingStatus.REVIEW,
            }:
                continue

            total_relevant += 1
            source_relevant += 1

            if intelligence.duplicate_opportunity_id is not None:
                total_updated_or_existing += 1
                refresh_duplicate_opportunity(
                    intelligence.duplicate_opportunity_id,
                    raw_opportunity,
                )
                try:
                    failures, qualifying = match_saved_opportunity(
                        intelligence.duplicate_opportunity_id,
                        company_profiles,
                    )
                    total_matching_failures += failures
                    total_qualifying_matches += qualifying
                except Exception as exc:
                    total_matching_failures += 1
                    print(
                        "  Company matching failed for existing opportunity: "
                        f"{type(exc).__name__}: {exc}"
                    )
                print(
                    "  [EXISTING] [AI duplicate] "
                    f"{raw_opportunity.title[:90]}"
                )
                continue

            filtered = intelligence.filtered_opportunity
            if filtered is None:
                total_classification_failures += 1
                continue

            try:
                was_saved = save_opportunity(filtered)
                opportunity_id = link_analysis_to_saved_opportunity(
                    filtered.source_url
                )
            except Exception as exc:
                total_save_failures += 1

                print(
                    "  Save failed for: "
                    f"{filtered.title[:80]}"
                )
                print(
                    f"    {type(exc).__name__}: {exc}"
                )
                continue

            if opportunity_id is not None:
                try:
                    failures, qualifying = match_saved_opportunity(
                        opportunity_id,
                        company_profiles,
                    )
                    total_matching_failures += failures
                    total_qualifying_matches += qualifying
                except Exception as exc:
                    total_matching_failures += 1
                    print(
                        "  Company matching failed for: "
                        f"{filtered.title[:80]}"
                    )
                    print(f"    {type(exc).__name__}: {exc}")

            if was_saved:
                total_saved += 1
                source_saved += 1
                record_status = "NEW"
            else:
                total_updated_or_existing += 1
                record_status = "EXISTING"

            organisation = (
                clean_organisation_name(
                    filtered.organisation_name
                )
                or "Organisation not specified"
            )

            print(
                f"  [{record_status}] "
                f"[{filtered.category}] "
                f"{filtered.title[:90]}"
            )
            print(
                f"      Organisation: "
                f"{organisation[:100]}"
            )
            print(
                f"      Match score: "
                f"{filtered.match_score}"
            )

        print(f"\n{scanner_name} summary:")
        print(f"  Possible listings: {source_total}")
        print(f"  Relevant listings: {source_relevant}")
        print(f"  New listings saved: {source_saved}")

    print("\n" + "=" * 60)
    print("Overall summary")
    print("=" * 60)
    print(
        f"Possible listings found:       "
        f"{total_found}"
    )
    print(
        f"Relevant opportunities:        "
        f"{total_relevant}"
    )
    print(
        f"New opportunities saved:       "
        f"{total_saved}"
    )
    print(
        f"Existing or updated records:   "
        f"{total_updated_or_existing}"
    )
    print(
        f"Scanner failures:              "
        f"{total_scan_failures}"
    )
    print(
        f"Classification failures:       "
        f"{total_classification_failures}"
    )
    print(
        f"Database save failures:        "
        f"{total_save_failures}"
    )
    print(
        f"Company matching failures:     "
        f"{total_matching_failures}"
    )
    print(
        f"Qualifying company matches:    "
        f"{total_qualifying_matches}"
    )
    print("Finished.")
    print("=" * 60)


def main() -> None:
    run_scanner()


if __name__ == "__main__":
    main()
