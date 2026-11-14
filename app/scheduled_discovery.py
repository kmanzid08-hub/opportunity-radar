from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.config import (
    FALSE_VALUES,
    TRUE_VALUES,
    SettingsError,
)
from app.database import SessionLocal
from app.models import (
    AutomationJobState,
    OpportunityPreference,
    Organization,
)
from app.source_discovery import (
    run_source_discovery,
)


JOB_NAME = "website_discovery"

# Website discovery runs at most once per day unless
# FORCE_WEBSITE_DISCOVERY=true.
INTERVAL = timedelta(
    days=1
)

FORCE_SETTING = (
    "FORCE_WEBSITE_DISCOVERY"
)

DEFAULT_DISCOVERY_COUNTRY = (
    "Rwanda"
)


def _force_discovery_enabled() -> bool:
    """
    Return whether the scheduled discovery job
    should ignore its normal daily interval.
    """

    value = os.getenv(
        FORCE_SETTING,
        "false",
    )

    normalised = (
        value
        .strip()
        .lower()
    )

    if normalised in TRUE_VALUES:
        return True

    if normalised in FALSE_VALUES:
        return False

    accepted = ", ".join(
        sorted(
            TRUE_VALUES
            | FALSE_VALUES
        )
    )

    raise SettingsError(
        f"{FORCE_SETTING} must be one of: "
        f"{accepted}; "
        f"received {value!r}."
    )


def _utc_now() -> datetime:
    return datetime.now(
        timezone.utc
    )


def _clean_country(
    value: object,
) -> str:
    """
    Normalize one configured country name.
    """

    if value is None:
        return ""

    return (
        " ".join(
            str(
                value
            ).split()
        )
        .strip()
    )


def _deduplicate_countries(
    values: list[object],
) -> list[str]:
    """
    Remove duplicate country names while
    preserving their configured order.
    """

    countries: list[str] = []
    seen: set[str] = set()

    for value in values:
        country = _clean_country(
            value
        )

        if not country:
            continue

        key = (
            country.casefold()
        )

        if key in seen:
            continue

        seen.add(
            key
        )

        countries.append(
            country
        )

    return countries


def _discovery_countries() -> list[str]:
    """Search the union of markets requested by ALL active business profiles.

    A first-company-only lookup silently excluded markets of other active
    companies. Prefer each company's configured target countries, falling back
    to its registered country. Rwanda remains the fallback for an empty workspace.
    Keep the search bounded to avoid unapproved API spending across many markets.
    """
    with SessionLocal() as db:
        rows = db.execute(
            select(Organization, OpportunityPreference)
            .outerjoin(
                OpportunityPreference,
                OpportunityPreference.organization_id == Organization.id,
            )
            .where(Organization.is_active.is_(True))
            .order_by(Organization.created_at.asc(), Organization.id.asc())
        ).all()

    requested: list[object] = []
    for organization, preference in rows:
        preferred = list(preference.countries or []) if preference else []
        if preferred:
            requested.extend(preferred)
        elif organization.country:
            requested.append(organization.country)

    countries = _deduplicate_countries(requested)
    if not countries:
        return [DEFAULT_DISCOVERY_COUNTRY]

    # A single discovery invocation should not fan out without bounds.
    maximum = 8
    if len(countries) > maximum:
        print(
            "Discovery market limit reached; using first "
            f"{maximum} of {len(countries)} configured countries."
        )
    return countries[:maximum]


def _last_success() -> datetime | None:
    """
    Return the most recent successful Website
    Discovery run.
    """

    with SessionLocal() as db:
        state = db.get(
            AutomationJobState,
            JOB_NAME,
        )
        value = (
            state.last_success_at
            if state is not None
            else None
        )

    if value is None:
        return None

    #
    # SQLite may return timestamps as strings
    # depending on driver configuration.
    #
    if isinstance(
        value,
        str,
    ):
        try:
            value = (
                datetime.fromisoformat(
                    value
                )
            )

        except ValueError:
            return None

    if value.tzinfo is None:
        value = (
            value.replace(
                tzinfo=timezone.utc
            )
        )

    return value.astimezone(
        timezone.utc
    )


def _record_success(
    completed_at: datetime,
) -> None:
    """
    Record successful completion of the complete
    multi-country discovery run.
    """

    with SessionLocal() as db:
        state = db.get(
            AutomationJobState,
            JOB_NAME,
        )

        if state is None:
            state = AutomationJobState(
                job_name=JOB_NAME,
                last_success_at=completed_at,
            )
            db.add(state)
        else:
            state.last_success_at = completed_at

        db.commit()


def _empty_summary() -> dict[
    str,
    int | float,
]:
    """
    Return aggregate counters for a multi-country
    discovery run.
    """

    return {
        "countries_processed": 0,
        "queries_executed": 0,
        "total_results": 0,
        "candidate_results": 0,
        "added_sources": 0,
        "accepted_sources": 0,
        "existing_sources": 0,
        "rejected_results": 0,
        "review_results": 0,
        "ai_failures": 0,
        "failed_queries": 0,
        "configured_delay_seconds": 0.0,
        "total_runtime_seconds": 0.0,
    }


def _merge_result(
    summary: dict[
        str,
        int | float,
    ],
    result: dict[
        str,
        int | float,
    ],
) -> None:
    """
    Add one country's discovery metrics
    into the complete run summary.
    """

    integer_metrics = (
        "queries_executed",
        "total_results",
        "candidate_results",
        "added_sources",
        "accepted_sources",
        "existing_sources",
        "rejected_results",
        "review_results",
        "ai_failures",
        "failed_queries",
    )

    float_metrics = (
        "configured_delay_seconds",
        "total_runtime_seconds",
    )

    for metric in integer_metrics:
        summary[
            metric
        ] = (
            int(
                summary.get(
                    metric,
                    0,
                )
            )
            + int(
                result.get(
                    metric,
                    0,
                )
            )
        )

    for metric in float_metrics:
        summary[
            metric
        ] = (
            float(
                summary.get(
                    metric,
                    0.0,
                )
            )
            + float(
                result.get(
                    metric,
                    0.0,
                )
            )
        )


def _run_country_discovery(
    countries: list[str],
) -> dict[
    str,
    int | float,
]:
    """
    Run Website Discovery once for every configured
    country and return combined metrics.
    """

    summary = (
        _empty_summary()
    )

    total_countries = len(
        countries
    )

    print(
        "=" * 70
    )

    print(
        "Opportunity Radar - "
        "Business Profile Website Discovery"
    )

    print(
        "=" * 70
    )

    print(
        "Countries configured: "
        + ", ".join(
            countries
        )
    )

    print(
        "Total countries: "
        f"{total_countries}"
    )

    print(
        "=" * 70
    )

    for index, country in enumerate(
        countries,
        start=1,
    ):
        print()

        print(
            "#" * 70
        )

        print(
            f"Country "
            f"{index}/"
            f"{total_countries}: "
            f"{country}"
        )

        print(
            "#" * 70
        )

        result = (
            run_source_discovery(
                country=country
            )
        )

        _merge_result(
            summary,
            result,
        )

        summary[
            "countries_processed"
        ] = (
            int(
                summary[
                    "countries_processed"
                ]
            )
            + 1
        )

        print()

        print(
            f"Completed discovery "
            f"for {country}."
        )

        print(
            "Queries: "
            f"{result.get('queries_executed', 0)}"
        )

        print(
            "New sources: "
            f"{result.get('added_sources', 0)}"
        )

        print(
            "Existing sources: "
            f"{result.get('existing_sources', 0)}"
        )

        print(
            "Failed queries: "
            f"{result.get('failed_queries', 0)}"
        )

    queries_executed = int(
        summary[
            "queries_executed"
        ]
    )

    total_runtime = float(
        summary[
            "total_runtime_seconds"
        ]
    )

    summary[
        "average_runtime_seconds"
    ] = (
        total_runtime
        / queries_executed
        if queries_executed
        else 0.0
    )

    print()

    print(
        "=" * 70
    )

    print(
        "Multi-country discovery summary"
    )

    print(
        "=" * 70
    )

    print(
        "Countries processed:          "
        f"{summary['countries_processed']}"
    )

    print(
        "Queries executed:             "
        f"{summary['queries_executed']}"
    )

    print(
        "Search results:               "
        f"{summary['total_results']}"
    )

    print(
        "Qualified candidates:         "
        f"{summary['candidate_results']}"
    )

    print(
        "New sources:                  "
        f"{summary['added_sources']}"
    )

    print(
        "Existing sources:             "
        f"{summary['existing_sources']}"
    )

    print(
        "Rejected results:             "
        f"{summary['rejected_results']}"
    )

    print(
        "Failed queries:               "
        f"{summary['failed_queries']}"
    )

    print(
        "Discovery runtime:            "
        f"{float(summary['total_runtime_seconds']):.3f} "
        "seconds"
    )

    print(
        "=" * 70
    )

    return summary


def main() -> int:
    """
    Execute Website Discovery when due.

    Manual GitHub workflow runs can bypass the daily
    interval using FORCE_WEBSITE_DISCOVERY=true.
    """

    force_discovery = (
        _force_discovery_enabled()
    )

    now = (
        _utc_now()
    )

    last_success = (
        _last_success()
    )

    if (
        not force_discovery
        and last_success is not None
        and now
        < last_success
        + INTERVAL
    ):
        next_run = (
            last_success
            + INTERVAL
        )

        print(
            "Website discovery is not due. "
            f"Next due: "
            f"{next_run.isoformat()}"
        )

        return 0

    if force_discovery:
        print(
            "Website discovery was manually forced. "
            "Starting discovery..."
        )

    else:
        print(
            "Website discovery is due. "
            "Starting discovery..."
        )

    countries = (
        _discovery_countries()
    )

    print(
        "Business Profile markets: "
        + ", ".join(
            countries
        )
    )

    result = (
        _run_country_discovery(
            countries
        )
    )

    #
    # A discovery run where every search query failed
    # must NOT advance the scheduler's success timestamp.
    #
    queries_executed = int(
        result.get(
            "queries_executed",
            0,
        )
    )

    failed_queries = int(
        result.get(
            "failed_queries",
            0,
        )
    )

    if (
        queries_executed == 0
        and failed_queries > 0
    ):
        raise RuntimeError(
            "Website discovery failed: "
            "no search query completed successfully."
        )

    _record_success(
        _utc_now()
    )

    print()

    print(
        "Website discovery completed successfully."
    )

    print(
        f"Final result: {result}"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
