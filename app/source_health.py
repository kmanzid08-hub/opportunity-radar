from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Source


class SourceHealthManager:
    FAILURE_DISABLE_THRESHOLD = 10

    def record_scan_started(
        self,
        source_id: int,
    ) -> None:
        with SessionLocal() as db:
            source = db.get(
                Source,
                source_id,
            )

            if source is None:
                return

            source.last_scan_started_at = (
                datetime.utcnow()
            )

            db.commit()

    def record_success(
        self,
        *,
        source_id: int,
        duration_seconds: float,
        http_status: int | None,
        opportunities_found: int,
    ) -> None:
        with SessionLocal() as db:
            source = db.get(
                Source,
                source_id,
            )

            if source is None:
                return

            now = datetime.utcnow()

            source.last_scanned_at = now
            source.last_successful_scan_at = now
            source.last_scan_duration_seconds = (
                round(duration_seconds, 3)
            )
            source.last_http_status = http_status
            source.consecutive_failures = 0
            source.is_auto_disabled = False
            source.disabled_reason = None

            if opportunities_found > 0:
                source.total_opportunities_found = (
                    int(
                        source.total_opportunities_found
                        or 0
                    )
                    + opportunities_found
                )

                source.last_opportunity_found_at = (
                    now
                )

            source.priority_score = (
                self.calculate_priority(source)
            )

            db.commit()

    def record_failure(
        self,
        *,
        source_id: int,
        duration_seconds: float,
        http_status: int | None,
        error_message: str,
    ) -> None:
        with SessionLocal() as db:
            source = db.get(
                Source,
                source_id,
            )

            if source is None:
                return

            source.last_scanned_at = (
                datetime.utcnow()
            )

            source.last_scan_duration_seconds = (
                round(duration_seconds, 3)
            )

            source.last_http_status = (
                http_status
            )

            source.consecutive_failures = (
                int(
                    source.consecutive_failures
                    or 0
                )
                + 1
            )

            if (
                source.consecutive_failures
                >= self.FAILURE_DISABLE_THRESHOLD
            ):
                source.is_active = False
                source.is_auto_disabled = True
                source.disabled_reason = (
                    "Automatically disabled after "
                    f"{source.consecutive_failures} "
                    "consecutive scan failures. "
                    f"Latest error: "
                    f"{error_message[:300]}"
                )

            source.priority_score = (
                self.calculate_priority(source)
            )

            db.commit()

    def refresh_all_priorities(
        self,
    ) -> int:
        with SessionLocal() as db:
            sources = db.scalars(
                select(Source)
            ).all()

            for source in sources:
                source.priority_score = (
                    self.calculate_priority(
                        source
                    )
                )

            db.commit()

            return len(sources)

    def calculate_priority(
        self,
        source: Source,
    ) -> float:
        score = float(
            source.confidence_score or 0
        ) * 0.55

        score += float(
            source.url_relevance_score or 0
        ) * 0.20

        total_found = int(
            source.total_opportunities_found
            or 0
        )

        score += min(
            total_found * 2,
            20,
        )

        failures = int(
            source.consecutive_failures
            or 0
        )

        score -= min(
            failures * 5,
            35,
        )

        if source.last_successful_scan_at:
            score += 5

        if source.is_auto_disabled:
            score = 0

        return max(
            0.0,
            min(round(score, 2), 100.0),
        )