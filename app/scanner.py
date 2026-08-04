from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import inspect, select, text

import app.lead_models  # noqa: F401
import app.proposal_models  # noqa: F401
from app.database import Base, SessionLocal, engine
from app.filters import classify_opportunity_with_reason
from app.models import Opportunity, Source
from app.schemas import FilteredOpportunity
from app.scanners.base import BaseScanner
from app.scanners.company_websites import CompanyWebsiteScanner
from app.scanners.job_in_rwanda import JobInRwandaScanner


ACTIVE_STATUSES: tuple[str, ...] = (
    "New",
    "Seen",
    "Interested",
)


SOURCE_COLUMNS: dict[str, str] = {
    "last_discovered_at": "DATETIME",
    "last_scan_started_at": "DATETIME",
    "last_opportunity_found_at": "DATETIME",
    "last_scan_duration_seconds": "FLOAT",
    "last_http_status": "INTEGER",
    "total_opportunities_found": (
        "INTEGER NOT NULL DEFAULT 0"
    ),
    "scan_interval_hours": (
        "INTEGER NOT NULL DEFAULT 12"
    ),
    "priority_score": (
        "FLOAT NOT NULL DEFAULT 50"
    ),
    "url_relevance_score": (
        "FLOAT NOT NULL DEFAULT 0"
    ),
    "is_auto_disabled": (
        "BOOLEAN NOT NULL DEFAULT 0"
    ),
    "disabled_reason": "VARCHAR(500)",
}


def current_utc_time() -> datetime:
    return datetime.now(timezone.utc)


def ensure_sqlite_schema() -> None:
    """
    Create tables and add missing Source columns for SQLite.

    SQLAlchemy create_all() creates new tables but does not add columns
    to an existing table, so Phase 1 columns are added using ALTER TABLE.
    """
    Base.metadata.create_all(bind=engine)

    if engine.dialect.name != "sqlite":
        return

    inspector = inspect(engine)

    if "sources" not in inspector.get_table_names():
        return

    existing_columns = {
        column["name"]
        for column in inspector.get_columns("sources")
    }

    missing_columns = {
        name: sql_type
        for name, sql_type in SOURCE_COLUMNS.items()
        if name not in existing_columns
    }

    if not missing_columns:
        return

    with engine.begin() as connection:
        for column_name, sql_type in missing_columns.items():
            connection.execute(
                text(
                    f'ALTER TABLE sources '
                    f'ADD COLUMN "{column_name}" {sql_type}'
                )
            )
            print(
                f"Added missing database column: "
                f"sources.{column_name}"
            )


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
    total_found = 0
    total_accepted = 0
    total_rejected = 0
    total_duplicates = 0
    total_saved = 0
    total_errors = 0

    print("=" * 60)
    print("Opportunity Radar")
    print("=" * 60)

    try:
        ensure_sqlite_schema()

        approved_count = approve_existing_sources()
        expired_count = mark_expired_opportunities()
        scanners = get_scanners()

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
                total_errors += 1

                print(
                    f"Scan failed for {scanner_name}: "
                    f"{type(exc).__name__}: {exc}"
                )
                continue

            source_total = len(raw_opportunities)
            source_accepted = 0
            source_rejected = 0
            source_duplicates = 0
            source_saved = 0
            source_errors = 0

            total_found += source_total

            print(f"Found {source_total} possible listings")

            for raw_opportunity in raw_opportunities:
                decision = classify_opportunity_with_reason(
                    raw_opportunity
                )
                filtered = decision.opportunity

                if filtered is None:
                    total_rejected += 1
                    source_rejected += 1

                    print(
                        "  [REJECTED] "
                        f"{raw_opportunity.title[:90]}"
                    )
                    print(
                        "      Reason: "
                        f"{decision.rejection_reason}"
                    )
                    continue

                total_accepted += 1
                source_accepted += 1

                try:
                    was_saved = save_opportunity(filtered)
                except Exception as exc:
                    total_errors += 1
                    source_errors += 1

                    print(
                        "  Save failed for: "
                        f"{filtered.title[:80]}"
                    )
                    print(
                        f"    {type(exc).__name__}: {exc}"
                    )
                    continue

                if was_saved:
                    total_saved += 1
                    source_saved += 1
                    record_status = "NEW"
                else:
                    total_duplicates += 1
                    source_duplicates += 1
                    record_status = "DUPLICATE"

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
            print(f"  Raw opportunities found: {source_total}")
            print(f"  Accepted: {source_accepted}")
            print(f"  Rejected: {source_rejected}")
            print(f"  Duplicates: {source_duplicates}")
            print(f"  Saved: {source_saved}")
            print(f"  Errors: {source_errors}")
    except Exception:
        total_errors += 1
        raise
    finally:
        print("\n" + "=" * 60)
        print("Overall summary")
        print("=" * 60)
        print(f"Raw opportunities found: {total_found}")
        print(f"Accepted: {total_accepted}")
        print(f"Rejected: {total_rejected}")
        print(f"Duplicates: {total_duplicates}")
        print(f"Saved: {total_saved}")
        print(f"Errors: {total_errors}")
        print("=" * 60)

    if total_errors:
        raise RuntimeError(
            "Opportunity scan completed with "
            f"{total_errors} error(s)."
        )


def main() -> None:
    run_scanner()


if __name__ == "__main__":
    main()
