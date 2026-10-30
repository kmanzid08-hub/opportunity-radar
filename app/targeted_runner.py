"""Operator-only local replay; never imported by startup or scheduled discovery."""

from collections.abc import Callable
from contextlib import redirect_stdout
from io import StringIO
import json
import os
from urllib.parse import urlsplit
from unittest.mock import patch

from app import database, scanner
from app.controlled_network import BoundedCrawler, BoundedWeb, display_url
from app.controlled_runner import BudgetedGemini, RunLimits, selected_profiles
from app.core.config import get_settings
from app.intelligence import matching_engine, opportunity_pipeline, triage_engine
from app.intelligence.gemini_client import GeminiClient
from app.source_fetch_diagnostics import classify_fetch_failure


def validate_targets(urls: list[str], limit: int = 5) -> list[str]:
    """Offline syntax check. Public DNS is checked again by the shared fetcher."""
    if not urls or len(urls) > limit or len(set(urls)) != len(urls):
        raise ValueError("Supply one to five distinct explicit opportunity URLs")
    for url in urls:
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.port not in {None, 80, 443}
            or parsed.fragment
            or any(char.isspace() for char in url)
        ):
            raise ValueError("Target URL has an unsupported or unsafe format")
    return urls


def run_targeted(
    *,
    backend: GeminiClient,
    urls: list[str],
    limits: RunLimits = RunLimits(),
    company_ids: list[int] | None = None,
    emit: Callable[[str], None] = print,
) -> dict:
    """Fetch explicit pages, not links/listings; normal caches and gates apply."""
    validate_targets(urls, limits.pages)
    if (
        database.engine.url.get_backend_name() != "sqlite"
        or str(database.engine.url.database).startswith(("//", "\\\\"))
        or get_settings().enable_internal_scheduler
        or os.environ.get("RENDER")
        or os.environ.get("APP_ENV", "").lower() == "production"
        or limits.ai_calls > 20
    ):
        raise ValueError(
            "Targeted replay requires local SQLite, disabled scheduler and at most 20 AI attempts"
        )
    profiles = selected_profiles(limits, company_ids)
    budget = BudgetedGemini(backend, limits, emit)
    # Each explicit URL may have a different robots file. Still max five pages;
    # SOURCE/discovery is never called and production limits are untouched.
    web = BoundedWeb(len(urls), limits.pages)
    crawler = BoundedCrawler(web, limits.pages)
    reports: list[dict] = []
    try:
        with patch.object(GeminiClient, "from_environment", return_value=budget):
            for url in urls:
                if sum(budget.calls.values()) >= limits.ai_calls:
                    break
                item: dict = {
                    "url": display_url(url),
                    "fetch": "NOT_ATTEMPTED",
                    "matches": [],
                    "triage": [],
                    "cache_hits": 0,
                }
                reports.append(item)
                before_calls = budget.calls.copy()
                before_operations = budget.operations.copy()
                before_hits = budget.cache_hits
                try:
                    try:
                        # Validate the target before even requesting robots.txt.
                        web.validate_url(url)
                        if not crawler._can_fetch(url):
                            item["fetch"] = "ROBOTS_OR_DOCUMENT_BLOCKED"
                            continue
                        with redirect_stdout(StringIO()):
                            soup = crawler._get_soup(url)
                            raw = crawler._build_page_opportunity(
                                soup, url, None, urlsplit(url).hostname or ""
                            )
                        item["fetch"] = "SUCCESS"
                    except Exception as exc:
                        failure = classify_fetch_failure(exc)
                        item.update(
                            fetch="ERROR",
                            error=failure.category,
                            http_status=failure.http_status,
                        )
                        continue
                    if raw is None:
                        item["classification"] = "EMPTY_CONTENT"
                        continue
                    # No invented country: extraction uses the fetched notice.
                    item["opportunity_version"] = (
                        opportunity_pipeline.OPPORTUNITY_ANALYSIS_VERSION
                    )
                    item["opportunity_fingerprint"] = (
                        opportunity_pipeline.build_opportunity_content_fingerprint(raw)
                    )
                    processed = opportunity_pipeline.process_opportunity_candidate(raw)
                    item["classification"] = processed.status.value
                    item["cache_hits"] += int(
                        not processed.analyzed
                        and processed.status is not opportunity_pipeline.OpportunityProcessingStatus.ERROR
                    )
                    analysis = processed.analysis
                    if analysis:
                        bidder = analysis.bidder_eligibility
                        item["bidder_eligibility"] = bidder.status.value
                        item["applicant_types"] = [
                            value.value for value in bidder.allowed_applicant_types
                        ]
                        item["evidence_strength"] = bidder.evidence_strength.value
                        # Quotation text may contain embedded private/hostile content.
                        # Retain it in the stored analysis, not the terminal log.
                        item["direct_quotation"] = (
                            "Review stored evidence locally"
                            if bidder.evidence
                            else None
                        )
                    if (
                        processed.status
                        is opportunity_pipeline.OpportunityProcessingStatus.ERROR
                    ):
                        item["error"] = "OPPORTUNITY_ANALYSIS_FAILED"
                    if (
                        processed.status
                        is not opportunity_pipeline.OpportunityProcessingStatus.VERIFIED
                    ):
                        continue
                    oid = processed.duplicate_opportunity_id
                    if oid is None:
                        if processed.filtered_opportunity is None:
                            raise ValueError("Verified data unavailable")
                        scanner.save_opportunity(processed.filtered_opportunity)
                        oid = opportunity_pipeline.link_analysis_to_saved_opportunity(
                            raw.source_url
                        )
                    else:
                        scanner.refresh_duplicate_opportunity(oid, raw)
                    if oid is None:
                        raise ValueError("Verified analysis not linked")
                    item["opportunity_id"] = oid
                    matches = matching_engine.match_verified_opportunity(
                        oid, profiles=profiles
                    )
                    for outcome in matches:
                        item["cache_hits"] += int(outcome.status.value == "CACHED")
                        item["matches"].append(
                            {
                                "company": outcome.company_id,
                                "status": outcome.status.value,
                                "score": outcome.match.score if outcome.match else None,
                                "strength": outcome.match.match_strength.value
                                if outcome.match
                                else None,
                                "applicant_compatibility": outcome.match.applicant_eligibility.value
                                if outcome.match
                                else None,
                                "qualifies_for_feed": outcome.qualifies_for_feed,
                                "version": matching_engine.MATCH_ANALYSIS_VERSION,
                                "currentness": "CURRENT" if outcome.match else "UNAVAILABLE",
                            }
                        )
                        if outcome.match is None:
                            item["error"] = "MATCH_FAILED"
                    for triage in triage_engine.triage_verified_opportunity(
                        oid, profiles=profiles, matches=matches
                    ):
                        item["cache_hits"] += int(triage.status == "CACHED")
                        item["triage"].append(
                            {
                                "company": triage.company_id,
                                "status": triage.status,
                                "priority": triage.result.priority.value
                                if triage.result
                                else None,
                                "version": triage_engine.TRIAGE_VERSION,
                                "error": triage.error_category,
                            }
                        )
                        if triage.status == "ERROR":
                            item["error"] = "TRIAGE_FAILED"
                except Exception:
                    # No arbitrary exception messages, SQL, payloads or profile text.
                    item["error"] = "DOWNSTREAM_EXECUTION_FAILED"
                finally:
                    item["attempts"] = dict(budget.calls - before_calls)
                    operations = budget.operations - before_operations
                    item["retries"] = {
                        stage: max(0, count - operations[stage])
                        for stage, count in item["attempts"].items()
                    }
                    item["cache_hits"] += budget.cache_hits - before_hits
                    emit("TARGET " + json.dumps(item, ensure_ascii=True))
                # A spent request budget stops later targets; no unbounded work.
                if sum(budget.calls.values()) >= limits.ai_calls:
                    break
    finally:
        crawler.session.close()
        web.session.close()
    report = {
        "targets": reports,
        "attempts": dict(budget.calls),
        "budget_used": sum(budget.calls.values()),
        "budget_limit": limits.ai_calls,
        "cache_hits": sum(item["cache_hits"] for item in reports),
        "unprocessed_targets": len(urls) - len(reports),
        "failures": budget.failed,
    }
    report["run_success"] = (
        not budget.failed
        and len(reports) == len(urls)
        and all(
            not item.get("error") and item["fetch"] == "SUCCESS" for item in reports
        )
    )
    emit("RESULT " + json.dumps(report, ensure_ascii=True))
    return report
