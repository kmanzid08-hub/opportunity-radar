"""Explicit development integration harness; never imported by application startup."""

from collections import Counter
from collections.abc import Callable
from contextlib import ExitStack, nullcontext, redirect_stdout
from dataclasses import dataclass, field
from hashlib import sha256
from io import StringIO
from itertools import islice
import json
from typing import Any, TypeVar, cast
from unittest.mock import patch

from pydantic import BaseModel
import requests
from sqlalchemy import or_, select

from app import database, scanner
from app.controlled_network import BoundedCrawler, BoundedWeb, display_url
from app.core.config import get_settings
from app.discovery.categories import DISCOVERY_CATEGORIES
from app.intelligence.gemini_client import (
    GeminiClient,
    GeminiAPIError,
    GeminiRequestBudgetError,
)
from app.intelligence.downstream_diagnostics import record_failure
from app.intelligence import (
    matching_engine,
    opportunity_pipeline,
    triage_engine,
)
from app.models import Organization, Source
from app.source_discovery import SourceDiscovery


ResponseModel = TypeVar("ResponseModel", bound=BaseModel)
STAGES = {
    "SourceAnalysis": "SOURCE",
    "OpportunityAnalysis": "OPPORTUNITY",
    "CompanyMatch": "MATCH",
    "TriageResult": "TRIAGE",
    "RequirementExtraction": "REQUIREMENTS",
    "ReadinessAnalysis": "READINESS",
}


@dataclass(frozen=True)
class RunLimits:
    sources: int = 3
    pages: int = 5
    companies: int = 2
    ai_calls: int = 20

    def __post_init__(self) -> None:
        for value, ceiling in (
            (self.sources, 3),
            (self.pages, 5),
            (self.companies, 2),
            (self.ai_calls, 43),
        ):
            if not 1 <= value <= ceiling:
                raise ValueError("Controlled test limit outside permitted bounds")

    def stage_caps(self) -> dict[str, int]:
        pairs = self.pages * self.companies
        return {
            "SOURCE": self.sources,
            "OPPORTUNITY": self.pages,
            "MATCH": pairs,
            "TRIAGE": pairs,
            "REQUIREMENTS": self.pages,
            "READINESS": pairs,
        }


class BudgetedGemini:
    """Durable stage caches run first; this second guard also remembers failures."""

    def __init__(
        self,
        backend: GeminiClient,
        limits: RunLimits,
        emit: Callable[[str], None] = print,
    ):
        self.backend = backend
        self.limits = limits
        self.emit = emit
        self.calls: Counter[str] = Counter()
        self.operations: Counter[str] = Counter()
        self.cache_hits = 0
        self.failed: list[tuple[str, str]] = []
        self.seen: dict[tuple[str, str], BaseModel | None] = {}
        self.rendering = False
        self.render_attempts = 0

    def generate_structured(
        self,
        prompt: str,
        response_schema: type[ResponseModel],
        *,
        system_instruction: str | None = None,
    ) -> ResponseModel:
        if self.rendering:
            self.render_attempts += 1
            raise GeminiAPIError("AI is forbidden during rendering")
        stage = STAGES[response_schema.__name__]
        digest = sha256(
            json.dumps([prompt, system_instruction], ensure_ascii=False).encode()
        ).hexdigest()
        key = (stage, digest)
        if key in self.seen:
            result = self.seen[key]
            if result is None:
                raise GeminiAPIError("Repeated failed input blocked")
            self.cache_hits += 1
            return response_schema.model_validate(result)
        self.seen[key] = None
        if (
            sum(self.calls.values()) >= self.limits.ai_calls
            or self.operations[stage] >= self.limits.stage_caps()[stage]
        ):
            self.failed.append((stage, "REQUEST_BUDGET_EXHAUSTED"))
            self.emit(f"{stage}: REQUEST_BUDGET_EXHAUSTED fingerprint={digest}")
            raise GeminiRequestBudgetError("Controlled request limit reached")
        self.operations[stage] += 1

        def reserve_attempt() -> None:
            if self.rendering or sum(self.calls.values()) >= self.limits.ai_calls:
                self.failed.append((stage, "REQUEST_BUDGET_EXHAUSTED"))
                self.emit(f"{stage}: REQUEST_BUDGET_EXHAUSTED fingerprint={digest}")
                raise GeminiRequestBudgetError("Controlled request limit reached")
            self.calls[stage] += 1
            self.emit(f"Gemini request: {stage} fingerprint={digest}")

        try:
            if isinstance(self.backend, GeminiClient):
                result = self.backend.generate_structured(
                    prompt,
                    response_schema,
                    system_instruction=system_instruction,
                    before_attempt=reserve_attempt,
                )
            else:
                # Simple injected test backends have no internal retry mechanism.
                reserve_attempt()
                result = self.backend.generate_structured(
                    prompt,
                    response_schema,
                    system_instruction=system_instruction,
                )
            result = response_schema.model_validate(result)
        except Exception as exc:
            category = record_failure(exc, stage=stage, schema=response_schema).category
            self.failed.append((stage, category))
            self.emit(f"{stage}: failure={category} fingerprint={digest}")
            # Preserve the typed failure for the engine's safe diagnostics.
            raise
        # These stages still need domain validation. Only their durable engine
        # caches may mark success; retain the per-run repeat guard on failure.
        if stage not in {"TRIAGE", "REQUIREMENTS", "READINESS"}:
            self.seen[key] = result
        self.emit(f"{stage}: success fingerprint={digest}")
        return result


@dataclass
class RunReport:
    sources: int = 0
    pages: int = 0
    companies: int = 0
    verified: list[int] = field(default_factory=list)
    matches: int = 0
    triaged: int = 0
    readiness: int = 0
    cache_hits: int = 0
    failures: list[str] = field(default_factory=list)
    render_checks: list[dict[str, Any]] = field(default_factory=list)
    ai_calls: dict[str, int] = field(default_factory=dict)
    run_success: bool = False
    qualifying_result_found: bool = False
    qualifying_opportunities: list[int] = field(default_factory=list)
    complete_flow: bool = False


def selected_profiles(limits: RunLimits, company_ids: list[int] | None):
    if company_ids is not None and (
        not company_ids
        or len(set(company_ids)) != len(company_ids)
        or len(company_ids) > limits.companies
    ):
        raise ValueError("Select at most two distinct active companies")
    if company_ids is None:
        with database.SessionLocal() as db:
            company_ids = list(
                db.scalars(
                    select(Organization.id)
                    .where(Organization.is_active.is_(True))
                    .order_by(Organization.id)
                    .limit(limits.companies)
                )
            )
    profiles = matching_engine.load_active_company_profiles(company_ids)
    if not profiles or len(profiles) != len(company_ids):
        raise ValueError(
            "Create/select existing active Business Profiles before running"
        )
    return profiles


def verify_rendering(
    opportunity_ids: list[int], company_ids: list[int], budget: BudgetedGemini
) -> list[dict[str, Any]]:
    from fastapi import Request
    from urllib.parse import urlencode
    from app import main

    class OperatorRenderer:
        # Local operator rendering exercises the existing view functions directly;
        # private-beta HTTP authentication is tested with real sessions separately.
        def get(self, path, params=None):
            values = params or {}
            request = Request({"type": "http", "method": "GET", "path": path,
                               "headers": [], "query_string": urlencode(values).encode(),
                               "scheme": "http", "server": ("local-render", 80),
                               "app": main.app, "router": main.app.router})
            request.state.user = None
            request.state.csrf = "local-operator-render"
            if path == "/":
                return main.home(request, **values)
            return main.opportunity_detail(int(path.rsplit("/", 1)[1]), request, **values)


    network_attempts = 0

    def forbidden(*_args: object, **_kwargs: object):
        nonlocal network_attempts
        network_attempts += 1
        raise AssertionError("External I/O forbidden during rendering")

    checks: list[dict[str, Any]] = []
    budget.rendering = True
    with ExitStack() as stack:
        stack.enter_context(
            patch.object(GeminiClient, "generate_structured", forbidden)
        )
        stack.enter_context(patch.object(GeminiClient, "generate_text", forbidden))
        stack.enter_context(
            patch.object(requests.sessions.Session, "request", forbidden)
        )
        stack.enter_context(patch("urllib.request.urlopen", forbidden))
        with nullcontext(OperatorRenderer()) as client:
            if client.get("/").status_code != 200:
                raise AssertionError("Discover did not render")
            for company_id in company_ids:
                for opportunity_id in opportunity_ids:
                    # Search the bounded result explicitly so pagination cannot hide it.
                    with database.SessionLocal() as db:
                        from app.models import Opportunity

                        opportunity = db.get(Opportunity, opportunity_id)
                        title = opportunity.title if opportunity else ""
                    feed = client.get(
                        "/", params={"company_id": company_id, "q": title}
                    )
                    detail = client.get(
                        f"/opportunities/{opportunity_id}",
                        params={"company_id": company_id},
                    )
                    if feed.status_code != 200 or detail.status_code != 200:
                        checks.append(
                            {
                                "opportunity": opportunity_id,
                                "company": company_id,
                                "render_error": True,
                            }
                        )
                        continue
                    shown = next(
                        (
                            item
                            for item in feed.context["opportunities"]
                            if item.id == opportunity_id
                        ),
                        None,
                    )
                    item = detail.context["opportunity"]
                    readiness = detail.context.get("readiness")
                    checks.append(
                        {
                            "opportunity": opportunity_id,
                            "company": company_id,
                            "discover": shown is not None,
                            "stored_match": detail.context["stored_match"] is not None,
                            "triage": getattr(item, "display_triage", None) is not None,
                            "readiness": bool(readiness and readiness.summary),
                            "feed_triage": bool(
                                shown and getattr(shown, "display_triage", None)
                            ),
                            "feed_readiness": bool(
                                shown and getattr(shown, "display_readiness", None)
                            ),
                            "readiness_consistent": shown is None
                            or getattr(shown, "display_readiness", None)
                            == (readiness.summary if readiness else None),
                            "triage_consistent": shown is None
                            or getattr(shown, "display_triage", None)
                            == getattr(item, "display_triage", None),
                            "match_consistent": shown is None
                            or all(
                                getattr(shown, name, None) == getattr(item, name, None)
                                for name in (
                                    "display_match_score",
                                    "display_match_strength",
                                    "display_match_reason",
                                )
                            ),
                        }
                    )
    budget.rendering = False
    if network_attempts or budget.render_attempts:
        raise AssertionError("Rendering attempted AI/crawler I/O")
    return checks


def run_controlled(
    *,
    backend: GeminiClient,
    limits: RunLimits = RunLimits(),
    country: str | None = None,
    company_ids: list[int] | None = None,
    emit: Callable[[str], None] = print,
) -> RunReport:
    # CLI validates file path/schema before imports; this guard also protects direct use.
    if (
        database.engine.url.get_backend_name() != "sqlite"
        or get_settings().enable_internal_scheduler
    ):
        raise ValueError("Runner requires local SQLite and a disabled scheduler")
    profiles = selected_profiles(limits, company_ids)
    countries = list(
        dict.fromkeys(
            str(value)
            for profile in profiles
            for value in cast(list[str], profile.profile.get("countries", []))
        )
    )
    if country is None:
        country = countries[0] if countries else None
    if not country or (
        countries
        and country.casefold() not in {value.casefold() for value in countries}
    ):
        raise ValueError(
            "Choose a country configured in the selected Business Profiles"
        )
    report = RunReport(companies=len(profiles))
    budget = BudgetedGemini(backend, limits, emit)
    web = BoundedWeb(limits.sources, limits.pages)
    crawler = BoundedCrawler(web, limits.pages)
    discovery = SourceDiscovery(country=country, content_fetcher=web.source_text)
    discovery.RESULTS_PER_QUERY = limits.sources
    discovery.session.max_redirects = 0
    emit(
        f"Controlled run: country={country}; sources<={limits.sources}; pages<={limits.pages}; companies<={limits.companies}; AI calls<={limits.ai_calls}"
    )
    seen_sources: set[str] = set()
    seen_pages: set[str] = set()
    try:
        with patch.object(GeminiClient, "from_environment", return_value=budget):
            category = next(
                item
                for item in DISCOVERY_CATEGORIES
                if item.name == "Consulting opportunities"
            )
            query = category.queries_for(country)[0]
            try:
                results = discovery._search(query)  # Exactly one search, never .run().
            except Exception:
                results = []
                report.failures.append("DISCOVERY: search unavailable")
            for search_result in islice(results, limits.sources):
                try:
                    candidate = discovery._build_candidate(
                        search_result, query, category
                    )
                    if candidate is None:
                        continue
                    url = str(candidate["monitor_url"])
                    if url in seen_sources:
                        report.cache_hits += 1
                        continue
                    seen_sources.add(url)
                    report.sources += 1
                    outcome = discovery._save_candidate(candidate)
                    report.cache_hits += int(
                        not outcome.analyzed and outcome.status != "ERROR"
                    )
                    emit(
                        f"SOURCE URL={display_url(url)} decision={outcome.status} confidence={outcome.confidence}"
                    )
                    if outcome.status != "ACCEPT":
                        if outcome.status == "ERROR":
                            report.failures.append("SOURCE: analysis/content failure")
                        continue
                    with database.SessionLocal() as db:
                        source = db.scalar(
                            select(Source).where(
                                or_(
                                    Source.monitor_url == url,
                                    Source.domain == str(candidate["domain"]),
                                )
                            )
                        )
                    if source is None or not source.is_approved or not source.is_active:
                        continue
                    # Suppress legacy crawler diagnostics that may contain full URLs.
                    with redirect_stdout(StringIO()):
                        raw_pages = crawler._scan_source(source)
                except Exception:
                    report.failures.append("SOURCE/CRAWLER: source failed")
                    continue
                for raw in raw_pages:
                    if len(seen_pages) >= limits.pages:
                        break
                    if (
                        raw.source_url in seen_pages
                        or raw.source_url not in crawler.fetched
                    ):
                        continue
                    seen_pages.add(raw.source_url)
                    report.pages += 1
                    try:
                        processed = opportunity_pipeline.process_opportunity_candidate(
                            raw, country_context=country
                        )
                        report.cache_hits += int(not processed.analyzed)
                        analysis = processed.analysis
                        emit(
                            f"OPPORTUNITY URL={display_url(raw.source_url)} decision={processed.status.value} confidence={analysis.confidence if analysis else None}"
                        )
                        if analysis:
                            emit(
                                json.dumps(
                                    {
                                        "title": analysis.title,
                                        "buyer": analysis.buyer_name,
                                        "deadline": str(analysis.deadline),
                                    },
                                    ensure_ascii=True,
                                )
                            )
                        if (
                            processed.status
                            is not opportunity_pipeline.OpportunityProcessingStatus.VERIFIED
                        ):
                            if (
                                processed.status
                                is opportunity_pipeline.OpportunityProcessingStatus.ERROR
                            ):
                                report.failures.append("OPPORTUNITY: analysis failure")
                            continue
                        opportunity_id = processed.duplicate_opportunity_id
                        if opportunity_id is None:
                            if processed.filtered_opportunity is None:
                                raise ValueError("Verified data unavailable")
                            scanner.save_opportunity(processed.filtered_opportunity)
                            opportunity_id = (
                                opportunity_pipeline.link_analysis_to_saved_opportunity(
                                    raw.source_url
                                )
                            )
                        else:
                            scanner.refresh_duplicate_opportunity(opportunity_id, raw)
                        if opportunity_id is None:
                            raise ValueError("Verified analysis not linked")
                        if opportunity_id in report.verified:
                            continue
                        report.verified.append(opportunity_id)
                        matches = matching_engine.match_verified_opportunity(
                            opportunity_id, profiles=profiles
                        )
                        # Business outcome comes from authoritative match results,
                        # independently of triage success or current feed visibility.
                        if any(
                            match.match and match.qualifies_for_feed
                            for match in matches
                        ):
                            report.qualifying_opportunities.append(opportunity_id)
                        for match in matches:
                            report.cache_hits += int(match.status.value == "CACHED")
                            if match.match:
                                report.matches += 1
                                emit(
                                    f"MATCH company={match.company_id} score={match.match.score} strength={match.match.match_strength.value}"
                                )
                            else:
                                report.failures.append(
                                    f"MATCH: company={match.company_id}"
                                )
                        triages = triage_engine.triage_verified_opportunity(
                            opportunity_id, profiles=profiles, matches=matches
                        )
                        for triage in triages:
                            report.cache_hits += int(triage.status == "CACHED")
                            if triage.result:
                                report.triaged += 1
                                emit(
                                    f"TRIAGE company={triage.company_id} priority={triage.result.priority.value} readiness={triage.result.bid_readiness.value}"
                                )
                            elif triage.status == "ERROR":
                                report.failures.append(
                                    f"TRIAGE: company={triage.company_id} category={triage.error_category}"
                                )
                        # Readiness is an explicit user/preparation operation,
                        # never a prerequisite or automatic step of ingestion.
                        if analysis:
                            # Opportunity facts are public; company-derived prose can
                            # expose confidential track record/personnel. Keep it in UI.
                            emit(
                                "QUALITY "
                                + json.dumps(
                                    {
                                        "title": analysis.title,
                                        "buyer": analysis.buyer_name,
                                        "opportunity_type": analysis.opportunity_type.value,
                                        "deadline": str(analysis.deadline),
                                        "summary": analysis.summary,
                                        "why_it_matches": "Review stored company explanation on the local detail page (private).",
                                        "critical_gaps": {
                                            str(item.company_id): len(
                                                item.result.critical_gaps
                                            )
                                            for item in triages
                                            if item.result
                                        },
                                        "next_action": "Review the stored company-specific next action and preparation checklist locally.",
                                    },
                                    ensure_ascii=True,
                                )
                            )
                    except Exception:
                        report.failures.append(
                            "OPPORTUNITY: downstream integration failure"
                        )
            try:
                report.render_checks = verify_rendering(
                    report.verified,
                    [profile.company_id for profile in profiles],
                    budget,
                )
                for check in report.render_checks:
                    if check.get("render_error") or not all(
                        check.get(name, True)
                        for name in (
                            "match_consistent",
                            "triage_consistent",
                            "readiness_consistent",
                        )
                    ):
                        report.failures.append(
                            "RENDER: stored data mismatch or HTTP error"
                        )
            except Exception:
                report.failures.append(
                    "RENDER: verification failed or attempted external I/O"
                )
    finally:
        discovery.session.close()
        discovery.content_session.close()
        crawler.session.close()
        web.session.close()
    report.ai_calls = dict(budget.calls)
    report.cache_hits += budget.cache_hits
    report.failures.extend(f"{stage}: {category}" for stage, category in budget.failed)
    failed_fetches = sum(value is None for value in web.cache.values())
    if failed_fetches:
        report.failures.append(f"HTTP: {failed_fetches} fetches failed; no retries")
    report.complete_flow = any(
        all(
            check.get(name, False)
            for name in (
                "discover",
                "stored_match",
                "triage",
                "feed_triage",
                "match_consistent",
                "triage_consistent",
                "readiness_consistent",
            )
        )
        for check in report.render_checks
    )
    # Rejections and gated skips are valid outcomes. Only unrecovered technical
    # failures affect run health; complete_flow remains a visibility diagnostic.
    report.run_success = not report.failures
    report.qualifying_result_found = bool(report.qualifying_opportunities)
    emit(f"Run status: {'SUCCESS' if report.run_success else 'FAILED'}")
    emit(f"Qualifying opportunities: {len(report.qualifying_opportunities)}")
    emit("RESULT " + json.dumps(report.__dict__, ensure_ascii=True))
    return report
