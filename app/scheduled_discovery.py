from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text

from app.core.config import (
    FALSE_VALUES,
    TRUE_VALUES,
    SettingsError,
)
from app.database import SessionLocal, engine
from app.models import (
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
    """
    Determine which markets Website Discovery
    should search.

    Priority:

    1. Business Profile target countries.
    2. Organization home country.
    3. Rwanda as the temporary development fallback.

    Once authentication is introduced, the scheduler
    can iterate over all active organizations instead
    of using the first active workspace.
    """

    with SessionLocal() as db:
        organization = db.scalar(
            select(
                Organization
            )
            .where(
                Organization
                .is_active
                .is_(True)
            )
            .order_by(
                Organization
                .created_at
                .asc()
            )
            .limit(
                1
            )
        )

        if organization is None:
            return [
                DEFAULT_DISCOVERY_COUNTRY
            ]

        preference = db.scalar(
            select(
                OpportunityPreference
            )
            .where(
                OpportunityPreference
                .organization_id
                == organization.id
            )
        )

        if (
            preference is not None
            and preference.countries
        ):
            countries = (
                _deduplicate_countries(
                    list(
                        preference.countries
                    )
                )
            )

            if countries:
                return countries

        home_country = (
            _clean_country(
                organization.country
            )
        )

        if home_country:
            return [
                home_country
            ]

    return [
        DEFAULT_DISCOVERY_COUNTRY
    ]


def _ensure_state_table() -> None:
    """
    Ensure the scheduler state table exists.

    This preserves the current scheduler behavior.
    The table should eventually move fully under Alembic.
    """

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS
                automation_job_state (
                    job_name VARCHAR(100)
                        PRIMARY KEY,

                    last_success_at
                        TIMESTAMP WITH TIME ZONE
                )
                """
            )
        )


def _last_success() -> datetime | None:
    """
    Return the most recent successful Website
    Discovery run.
    """

    with engine.begin() as connection:
        value = connection.execute(
            text(
                """
                SELECT last_success_at
                FROM automation_job_state
                WHERE job_name = :job_name
                """
            ),
            {
                "job_name": (
                    JOB_NAME
                ),
            },
        ).scalar_one_or_none()

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

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO automation_job_state (
                    job_name,
                    last_success_at
                )
                VALUES (
                    :job_name,
                    :completed_at
                )
                ON CONFLICT (job_name)
                DO UPDATE SET
                    last_success_at =
                        EXCLUDED.last_success_at
                """
            ),
            {
                "job_name": (
                    JOB_NAME
                ),

                "completed_at": (
                    completed_at
                ),
            },
        )


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
        "existing_sources": 0,
        "rejected_results": 0,
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
        "existing_sources",
        "rejected_results",
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

    _ensure_state_table()

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
