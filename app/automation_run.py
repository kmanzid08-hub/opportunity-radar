"""Scheduled Brave source discovery and AI opportunity matching.

GitHub Actions serializes runs with its workflow concurrency group. Avoid
holding a PostgreSQL connection open across long external HTTP/AI operations:
Neon can close idle SSL connections before cleanup.
"""
from __future__ import annotations

import logging
import os

from app.scanner import run_scanner
from app.source_discovery import SourceDiscovery

logger = logging.getLogger("opportunity_radar.automation")


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url.startswith(
        ("postgresql://", "postgres://", "postgresql+psycopg://")
    ):
        raise RuntimeError(
            "Scheduled discovery requires the shared PostgreSQL DATABASE_URL"
        )
    if not os.environ.get("BRAVE_API_KEY", "").strip():
        raise RuntimeError("BRAVE_API_KEY is required for source discovery")
    if not os.environ.get("GEMINI_API_KEY", "").strip():
        raise RuntimeError("GEMINI_API_KEY is required for AI evaluation")

    mode = os.environ.get("AUTOMATION_MODE", "scan").strip().lower()
    if mode not in {"all", "discover", "scan", "scheduled"}:
        raise ValueError("Invalid AUTOMATION_MODE")

    # The GitHub workflow decides when discovery is due. A "scheduled"
    # invocation is scanning-only; the distinct discovery cron uses "all".
    if mode in {"all", "discover"}:
        logger.info("Starting Brave source discovery for Rwanda")
        summary = SourceDiscovery(country="Rwanda").run()
        logger.info(
            "Discovery finished: new=%s accepted=%s queries=%s failures=%s",
            summary["added_sources"],
            summary["accepted_sources"],
            summary["queries_executed"],
            summary["failed_queries"],
        )
        if summary["queries_executed"] and (
            summary["failed_queries"] == summary["queries_executed"]
        ):
            raise RuntimeError("All Brave discovery queries failed")

    if mode in {"all", "scan", "scheduled"}:
        logger.info("Scanning saved sources and evaluating business matches")
        run_scanner()
        logger.info("Opportunity scanning and matching finished")


if __name__ == "__main__":
    main()
