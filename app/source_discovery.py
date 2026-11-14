from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from dotenv import load_dotenv
from dataclasses import dataclass
from typing import Any, Iterable, cast
from urllib.parse import urljoin, urlparse, urlunparse
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from app.discovery.categories import (
    DISCOVERY_CATEGORIES,
    DiscoveryCategory,
    infer_discovery_tags,
)
from app.discovery.metadata import (
    merge_discovery_metadata,
)
from app.intelligence.gemini_client import GeminiError
from app.intelligence.schemas import AnalysisDecision, SourceAnalysis
from app.intelligence.source_analyzer import analyze_source
from app.source_quality import SourceQualityEvaluator
from app.source_fetch_diagnostics import SourceFetchError, report_fetch_failure

import requests
from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import Source
load_dotenv()


@dataclass
class SearchResult:
    title: str
    url: str
    description: str


@dataclass
class CategoryStatistics:
    queries_executed: int = 0
    results: int = 0
    accepted: int = 0
    duplicates: int = 0
    rejected: int = 0
    runtime_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class SourceProcessingOutcome:
    status: str
    created: bool
    analyzed: bool
    confidence: int | None = None
    reason: str | None = None


SourceAnalyzer = Callable[..., SourceAnalysis]
ContentFetcher = Callable[[str], str]


class SourceContentError(SourceFetchError):
    """Raised when a candidate page cannot provide analyzable text."""


def build_source_fingerprint(
    *,
    url: str,
    meaningful_content: str,
    country: str,
) -> str:
    """Hash the normalized source identity and semantic input."""
    normalized_content = " ".join(meaningful_content.split())
    payload = "\n".join(
        (
            url.strip(),
            country.strip().casefold(),
            normalized_content,
        )
    )
    return sha256(payload.encode("utf-8")).hexdigest()


class SourceDiscovery:
    """
    Discover organisation and opportunity websites for a country.

    Qualified sources are saved with:

        is_active = True
        is_approved = True

    Their discovery score becomes the initial priority used by the
    existing source scheduler.
    """

    API_URL = (
        "https://api.search.brave.com/"
        "res/v1/web/search"
    )

    REQUEST_TIMEOUT = 30
    CONTENT_REQUEST_TIMEOUT = 20
    REQUEST_DELAY_SECONDS = 1.0
    MAX_FETCHED_CONTENT_CHARS = 100_000

    RESULTS_PER_QUERY = 20
    MAX_MONITOR_URLS_PER_DOMAIN = 4
    DISCOVERY_CATEGORIES = DISCOVERY_CATEGORIES

    OPPORTUNITY_TERMS = (
        "tender",
        "tenders",
        "procurement",
        "request for proposal",
        "request for proposals",
        "rfp",
        "expression of interest",
        "expressions of interest",
        "eoi",
        "consultancy",
        "consultancies",
        "consultant",
        "consulting",
        "invitation to bid",
        "invitation for bids",
        "terms of reference",
        "vacancy",
        "vacancies",
        "career",
        "careers",
        "job",
        "jobs",
        "opportunity",
        "opportunities",
        "call for proposals",
        "call for applications",
        "supplier",
        "vendor",
        "prequalification",
        "grant",
        "grants",
    )

    HIGH_VALUE_TERMS = (
        "request for proposal",
        "expression of interest",
        "invitation to bid",
        "terms of reference",
        "procurement",
        "tender",
        "consultancy",
        "audit",
        "accounting",
        "recruitment",
        "tax",
        "training",
        "research",
        "environmental",
        "monitoring and evaluation",
    )

    EXCLUDED_DOMAINS = (
        "google.com",
        "bing.com",
        "brave.com",
        "facebook.com",
        "instagram.com",
        "linkedin.com",
        "twitter.com",
        "x.com",
        "youtube.com",
        "tiktok.com",
        "wikipedia.org",
        "pinterest.com",
        "reddit.com",
        "glassdoor.com",
        "indeed.com",
    )

    FILE_EXTENSIONS = (
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
        ".zip",
    )

    def __init__(
        self,
        country: str | None = None,
        *,
        source_analyzer: SourceAnalyzer | None = None,
        content_fetcher: ContentFetcher | None = None,
    ) -> None:
        self.country = (
            country
            or os.getenv("DISCOVERY_COUNTRY", "Rwanda")
            or "Rwanda"
        ).strip()

        if not self.country:
            raise ValueError("A discovery country is required.")

        self.api_key = os.getenv(
            "BRAVE_API_KEY",
            "",
        ).strip()
        self.quality_evaluator = SourceQualityEvaluator()
        self.source_analyzer = source_analyzer or analyze_source

        if not self.api_key:
            raise RuntimeError(
                "BRAVE_API_KEY is not configured. "
                "Set it before running source discovery."
            )

        self.session = requests.Session()

        self.session.headers.update(
            {
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-Subscription-Token": (
                    self.api_key
                ),
                "User-Agent": (
                    "OpportunityRadar/1.0 source-discovery"
                ),
            }
        )

        self.content_session = requests.Session()
        self.content_session.headers.update(
            {
                "Accept": "text/html,application/xhtml+xml,text/plain",
                "User-Agent": "OpportunityRadar/1.0 source-analysis",
            }
        )
        self.content_fetcher = content_fetcher or self._fetch_source_content

    def _log_diagnostic(self, **fields: object) -> None:
        """Record every candidate decision for post-run root-cause analysis.

        Disabled unless explicitly configured by the GitHub discovery workflow.
        These records contain public search URLs, not private API credentials.
        Diagnostics failures must not stop a discovery job.
        """
        path = os.getenv("DISCOVERY_DIAGNOSTICS_PATH", "").strip()
        if not path:
            return
        record = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "country": self.country,
            **fields,
        }
        try:
            output = Path(path)
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except OSError as exc:
            print(f"Discovery diagnostic export unavailable: {type(exc).__name__}")

    def run(self) -> dict[str, int | float]:
        """
        Run all configured discovery queries.

        Returns summary counts.
        """
        total_results = 0
        candidate_results = 0
        added_sources = 0
        existing_sources = 0
        rejected_results = 0
        review_results = 0
        ai_failures = 0
        accepted_sources = 0
        failed_queries = 0
        queries_executed = 0
        query_runtime_total = 0.0
        seen_urls: set[str] = set()
        domain_slots: dict[str, int] = {}

        category_queries = tuple(
            (
                category,
                category.queries_for(self.country),
            )
            for category in self.DISCOVERY_CATEGORIES
        )
        total_configured_queries = sum(
            len(queries)
            for _, queries in category_queries
        )
        run_started_at = time.perf_counter()
        configured_delay_seconds = (
            max(total_configured_queries - 1, 0)
            * self.REQUEST_DELAY_SECONDS
        )

        print("=" * 65)
        print("Opportunity Radar - Source Discovery")
        print("=" * 65)
        print(f"Country: {self.country}")
        print(f"Categories: {len(category_queries)}")
        print(f"Queries configured: {total_configured_queries}")
        print(
            "Configured delay per query: "
            f"{self.REQUEST_DELAY_SECONDS:.3f} seconds"
        )
        print(
            "Configured delay across run: "
            f"{configured_delay_seconds:.3f} seconds"
        )

        for category, queries in category_queries:
            category_stats = CategoryStatistics()

            print("\n" + "-" * 65)
            print(f"Category: {category.name}")
            print("-" * 65)

            for query in queries:
                query_started_at = time.perf_counter()
                queries_executed += 1
                category_stats.queries_executed += 1
                query_result_count = 0
                query_accepted = 0
                query_duplicates = 0
                query_rejected = 0
                query_failed = False

                print(
                    f"[QUERY {queries_executed}/"
                    f"{total_configured_queries}] {query}"
                )

                try:
                    results = self._search(query)
                except Exception as exc:
                    failed_queries += 1
                    query_failed = True
                    print(
                        "  Search failed: "
                        f"{type(exc).__name__}: {exc}"
                    )
                else:
                    query_result_count = len(results)
                    total_results += query_result_count
                    category_stats.results += query_result_count

                    for result in results:
                        self._log_diagnostic(
                            event="search_result",
                            category=category.name,
                            query=query,
                            result_url=result.url[:2000],
                            result_title=result.title[:250],
                        )
                        candidate = self._build_candidate(
                            result=result,
                            query=query,
                            category=category,
                        )

                        if candidate is None:
                            self._log_diagnostic(
                                event="candidate_filtered",
                                reason="unsupported_or_invalid_url",
                                category=category.name,
                                query=query,
                                result_url=result.url[:2000],
                            )
                            rejected_results += 1
                            query_rejected += 1
                            category_stats.rejected += 1
                            continue

                        candidate_url = str(candidate["monitor_url"])
                        candidate_domain = str(candidate["domain"])
                        if candidate_url in seen_urls:
                            self._log_diagnostic(
                                event="duplicate_within_run",
                                reason="same_monitor_url",
                                query=query,
                                result_url=result.url[:2000],
                                monitor_url=candidate_url,
                            )
                            existing_sources += 1
                            query_duplicates += 1
                            category_stats.duplicates += 1
                            continue

                        # Monitor distinct procurement, consultancy and career
                        # paths on the same site, without unlimited requests.
                        if domain_slots.get(candidate_domain, 0) >= self.MAX_MONITOR_URLS_PER_DOMAIN:
                            self._log_diagnostic(
                                event="duplicate_within_run",
                                reason="domain_candidate_limit",
                                query=query,
                                result_url=result.url[:2000],
                                monitor_url=candidate_url,
                            )
                            query_duplicates += 1
                            category_stats.duplicates += 1
                            continue
                        seen_urls.add(candidate_url)
                        domain_slots[candidate_domain] = domain_slots.get(candidate_domain, 0) + 1

                        candidate_results += 1
                        outcome = self._save_candidate(candidate)
                        self._log_diagnostic(
                            event="source_assessment",
                            query=query,
                            category=category.name,
                            result_url=result.url[:2000],
                            monitor_url=candidate_url,
                            domain=candidate_domain,
                            status=outcome.status,
                            created=outcome.created,
                            analyzed=outcome.analyzed,
                            ai_confidence=outcome.confidence,
                            reason=outcome.reason,
                            document_evidence_url=candidate.get("document_evidence_url"),
                        )

                        if outcome.created:
                            added_sources += 1

                        if outcome.status == AnalysisDecision.ACCEPT.value:
                            accepted_sources += 1
                            query_accepted += 1
                            category_stats.accepted += 1
                            print(
                                "  [AI ACCEPT] "
                                f"{candidate['organisation_name']}"
                            )
                            print(f"      {candidate['monitor_url']}")
                            print(
                                "      AI confidence: "
                                f"{outcome.confidence}"
                            )
                            print(
                                "      Tags: "
                                + ", ".join(
                                    str(tag)
                                    for tag in cast(
                                        Iterable[object],
                                        candidate["tags"],
                                    )
                                )
                            )
                        elif outcome.status == AnalysisDecision.REVIEW.value:
                            review_results += 1
                            print(
                                "  [AI REVIEW] "
                                f"{candidate['organisation_name']}"
                            )
                        elif outcome.status in {AnalysisDecision.REJECT.value, "DOMAIN_CAP"}:
                            rejected_results += 1
                            query_rejected += 1
                            category_stats.rejected += 1
                        else:
                            ai_failures += 1

                        if not outcome.created and outcome.status != "DOMAIN_CAP":
                            existing_sources += 1
                            query_duplicates += 1
                            category_stats.duplicates += 1

                query_runtime_seconds = (
                    time.perf_counter() - query_started_at
                )
                query_runtime_total += query_runtime_seconds
                category_stats.runtime_seconds += query_runtime_seconds

                print(f"  Results returned: {query_result_count}")
                print(f"  Accepted: {query_accepted}")
                print(f"  Duplicates: {query_duplicates}")
                print(f"  Rejected: {query_rejected}")
                print(
                    "  Query runtime: "
                    f"{query_runtime_seconds:.3f} seconds"
                )
                print(
                    "  Query status: "
                    f"{'FAILED' if query_failed else 'COMPLETED'}"
                )

                if queries_executed < total_configured_queries:
                    self._wait()

            print(f"Category: {category.name}")
            print(
                "  Queries executed: "
                f"{category_stats.queries_executed}"
            )
            print(f"  Results: {category_stats.results}")
            print(f"  Accepted: {category_stats.accepted}")
            print(f"  Duplicates: {category_stats.duplicates}")
            print(f"  Rejected: {category_stats.rejected}")
            print(
                "  Runtime: "
                f"{category_stats.runtime_seconds:.3f} seconds"
            )

        total_runtime_seconds = (
            time.perf_counter()
            - run_started_at
        )
        average_runtime_seconds = (
            query_runtime_total / queries_executed
            if queries_executed
            else 0.0
        )

        print("\n" + "=" * 65)
        print("Source discovery summary")
        print("=" * 65)
        print(f"Total queries:                 {queries_executed}")
        print(f"Total results:                 {total_results}")
        print(f"New sources:                   {added_sources}")
        print(f"AI accepted:                   {accepted_sources}")
        print(f"AI review:                     {review_results}")
        print(f"AI failures:                   {ai_failures}")
        print(f"Duplicates:                    {existing_sources}")
        print(f"Rejected:                      {rejected_results}")
        print(f"Failed queries:                {failed_queries}")
        print(
            "Average runtime:               "
            f"{average_runtime_seconds:.3f} seconds"
        )
        print(
            f"Configured query delay:        "
            f"{configured_delay_seconds:.3f} seconds"
        )
        print(
            f"Total runtime:                 "
            f"{total_runtime_seconds:.3f} seconds"
        )
        print(
            "\nQualified sources were saved with discovery priority."
        )
        print("=" * 65)

        return {
            "queries_executed": queries_executed,
            "total_results": total_results,
            "candidate_results": candidate_results,
            "added_sources": added_sources,
            "accepted_sources": accepted_sources,
            "existing_sources": existing_sources,
            "rejected_results": rejected_results,
            "review_results": review_results,
            "ai_failures": ai_failures,
            "failed_queries": failed_queries,
            "configured_delay_seconds": (
                configured_delay_seconds
            ),
            "total_runtime_seconds": total_runtime_seconds,
            "average_runtime_seconds": average_runtime_seconds,
            "categories_processed": len(category_queries),
        }

    def _search(
        self,
        query: str,
    ) -> list[SearchResult]:
        """
        Search Brave and return standardised web results.
        """
        params: dict[str, str | int] = {
            "q": query,
            "count": self.RESULTS_PER_QUERY,
            "search_lang": "en",
            "safesearch": "moderate",
        }

        response = self.session.get(
            self.API_URL,
            params=params,
            timeout=self.REQUEST_TIMEOUT,
        )

        if not response.ok:
            raise RuntimeError(
                f"Brave Search returned HTTP {response.status_code}: "
                f"{response.text[:1000]}"
            )

        payload: dict[str, Any] = (
            response.json()
        )

        raw_results = (
            payload
            .get("web", {})
            .get("results", [])
        )

        results: list[SearchResult] = []

        for item in raw_results:
            if not isinstance(item, dict):
                continue

            title = self._clean_text(
                item.get("title")
            )

            url = self._clean_text(
                item.get("url")
            )

            description = self._clean_text(
                item.get("description")
            )

            if not title or not url:
                continue

            results.append(
                SearchResult(
                    title=title,
                    url=url,
                    description=description,
                )
            )

        return results
    def _build_candidate(
        self,
        result: SearchResult,
        query: str,
        category: DiscoveryCategory,
    ) -> dict[str, object] | None:
        # Direct tender documents are evidence of the publisher's site, not
        # standalone websites to be monitored as organization homepages.
        original_url = self.quality_evaluator.normalise_url(result.url)
        document_url = (
            original_url if self._is_document_url(original_url) else None
        )
        assessment = self.quality_evaluator.assess(
            title=result.title,
            description=result.description,
            url=result.url,
            discovery_query=query,
            country=self.country,
        )

        if not assessment.base_url or not assessment.monitor_url:
            return None

        parsed = urlparse(
            assessment.base_url
        )

        domain = (
            self.quality_evaluator
            .normalise_domain(parsed.netloc)
        )
        tags = infer_discovery_tags(
            category=category,
            title=result.title,
            description=result.description,
            url=assessment.monitor_url,
            source_type=assessment.source_type,
        )

        return {
            "url": assessment.monitor_url,
            "organisation_name": (
                assessment.organisation_name
            ),
            "base_url": assessment.base_url,
            "monitor_url": (
                assessment.monitor_url
            ),
            "domain": domain,
            "page_title": result.title,
            "description": result.description,
            "country_context": self.country,
            "discovery_method": "Brave Search API",
            "source_type": (
                assessment.source_type
            ),
            "discovered_from": result.url,
            "document_evidence_url": document_url,
            "discovery_query": query,
            "discovery_category": category.name,
            "tags": tags,
            "discovery_score": assessment.discovery_score,
            "score_components": assessment.score_components,
            "confidence_score": (
                assessment.confidence_score
            ),
            "priority_score": (
                assessment.priority_score
            ),
            "url_relevance_score": (
                assessment.url_relevance_score
            ),
            "is_active": False,
            "is_approved": False,
            "is_auto_disabled": False,
            "last_discovered_at": (
                datetime.now(timezone.utc)
            ),
        }

    def _save_candidate(
        self,
        candidate: dict[str, object],
    ) -> SourceProcessingOutcome:
        monitor_url = str(
            candidate["monitor_url"]
        )
        monitor_url = self.quality_evaluator.normalise_url(
            monitor_url
        )

        domain = str(
            candidate["domain"]
        )
        domain = self.quality_evaluator.normalise_domain(
            domain
        )

        if not monitor_url or not domain:
            return SourceProcessingOutcome(
                status="ERROR",
                created=False,
                analyzed=False,
            )

        candidate = {
            **candidate,
            "monitor_url": monitor_url,
            "domain": domain,
        }

        # Unique source identity is monitor_url, not domain. Avoid unbounded
        # monitoring growth or AI analysis on excess same-domain pages.
        with SessionLocal() as db:
            current = db.scalar(
                select(Source).where(Source.monitor_url == monitor_url)
            )
            count_on_domain = db.scalar(
                select(func.count(Source.id)).where(Source.domain == domain)
            ) or 0
            if current is None and count_on_domain >= self.MAX_MONITOR_URLS_PER_DOMAIN:
                return SourceProcessingOutcome(
                    status="DOMAIN_CAP",
                    created=False,
                    analyzed=False,
                )

        try:
            meaningful_content = self.content_fetcher(monitor_url)
            if not meaningful_content.strip():
                raise SourceContentError(
                    "EMPTY_CONTENT"
                )
        except Exception as exc:
            return self._persist_analysis_error(
                candidate,
                "Source content could not be fetched for AI analysis. "
                + report_fetch_failure(exc),
            )

        # New publication-document evidence must trigger a re-evaluation
        # even when the organization's homepage HTML remains unchanged.
        document_url = str(candidate.get("document_evidence_url") or "")
        analysis_content = meaningful_content
        if document_url:
            analysis_content += (
                "\nPublisher-hosted tender document found by search: "
                + document_url
                + "\nSearch result title: "
                + str(candidate.get("page_title", ""))[:250]
                + "\nSearch result summary: "
                + str(candidate.get("description", ""))[:400]
            )

        fingerprint = build_source_fingerprint(
            url=monitor_url,
            meaningful_content=analysis_content,
            country=str(candidate.get("country_context", self.country)),
        )

        with SessionLocal() as db:
            existing = db.scalar(
                select(Source).where(Source.monitor_url == monitor_url)
            )

            if (
                existing is not None
                and existing.source_fingerprint == fingerprint
                and existing.ai_status
                in {
                    AnalysisDecision.ACCEPT.value,
                    AnalysisDecision.REJECT.value,
                    AnalysisDecision.REVIEW.value,
                }
            ):
                self._improve_existing_source(
                    existing=existing,
                    candidate=candidate,
                )
                db.commit()
                return SourceProcessingOutcome(
                    status=existing.ai_status,
                    created=False,
                    analyzed=False,
                    confidence=existing.ai_confidence,
                    reason=existing.ai_reason,
                )

        analysis_input = "\n".join(
            (
                f"Page title: {candidate.get('page_title', '')}",
                f"Search description: {candidate.get('description', '')}",
                "Website content and publisher document evidence:",
                analysis_content,
            )
        )

        try:
            analysis = self.source_analyzer(
                url=monitor_url,
                page_content=analysis_input,
                source_name_hint=str(
                    candidate.get("organisation_name", "")
                )
                or None,
                country_hint=str(
                    candidate.get("country_context", self.country)
                ),
            )
        except GeminiError:
            return self._persist_analysis_error(
                candidate,
                "Gemini source analysis failed.",
            )

        if not isinstance(analysis, SourceAnalysis):
            return self._persist_analysis_error(
                candidate,
                "Gemini returned an invalid source analysis.",
            )

        discovery_metadata = merge_discovery_metadata(
            None,
            categories=(str(candidate.get("discovery_category", "")),),
            queries=(str(candidate.get("discovery_query", "")),),
            tags=tuple(
                str(tag)
                for tag in cast(
                    Iterable[object],
                    candidate.get("tags", ()),
                )
            ),
        )

        with SessionLocal() as db:
            existing = db.scalar(
                select(Source).where(Source.monitor_url == monitor_url)
            )

            if existing is not None:
                self._improve_existing_source(
                    existing=existing,
                    candidate=candidate,
                )
                source = existing
                created = False
            else:
                source = Source(
                    organisation_name=(
                        str(candidate.get("organisation_name", "")) or None
                    ),
                    base_url=str(candidate.get("base_url", monitor_url)),
                    monitor_url=monitor_url,
                    domain=domain,
                    source_type=str(candidate.get("source_type", "Unknown")),
                    discovered_from=str(candidate.get("discovered_from", ""))
                    or None,
                    discovery_query=discovery_metadata,
                    confidence_score=float(
                        str(candidate.get("confidence_score", 0.0))
                    ),
                    priority_score=float(
                        str(candidate.get("priority_score", 0.0))
                    ),
                    url_relevance_score=float(
                        str(candidate.get("url_relevance_score", 0.0))
                    ),
                    is_active=False,
                    is_approved=False,
                    is_auto_disabled=False,
                    scan_interval_hours=12,
                    last_discovered_at=datetime.now(timezone.utc),
                )
                db.add(source)
                created = True

            self._apply_source_analysis(
                source=source,
                analysis=analysis,
                fingerprint=fingerprint,
            )
            db.commit()

            return SourceProcessingOutcome(
                status=analysis.decision.value,
                created=created,
                analyzed=True,
                confidence=analysis.confidence,
                reason=analysis.reason,
            )

    def _persist_analysis_error(
        self,
        candidate: dict[str, object],
        reason: str,
    ) -> SourceProcessingOutcome:
        monitor_url = self.quality_evaluator.normalise_url(
            str(candidate["monitor_url"])
        )
        domain = self.quality_evaluator.normalise_domain(
            str(candidate["domain"])
        )
        if not monitor_url or not domain:
            return SourceProcessingOutcome(
                status="ERROR",
                created=False,
                analyzed=False,
            )

        with SessionLocal() as db:
            source = db.scalar(
                select(Source).where(Source.monitor_url == monitor_url)
            )
            created = source is None
            if source is None:
                source = Source(
                    organisation_name=(
                        str(candidate.get("organisation_name", "")) or None
                    ),
                    base_url=str(candidate.get("base_url", monitor_url)),
                    monitor_url=monitor_url,
                    domain=domain,
                    source_type=str(candidate.get("source_type", "Unknown")),
                    discovered_from=str(candidate.get("discovered_from", ""))
                    or None,
                    discovery_query=merge_discovery_metadata(
                        None,
                        categories=(
                            str(candidate.get("discovery_category", "")),
                        ),
                        queries=(str(candidate.get("discovery_query", "")),),
                        tags=tuple(
                            str(tag)
                            for tag in cast(
                                Iterable[object],
                                candidate.get("tags", ()),
                            )
                        ),
                    ),
                    confidence_score=float(
                        str(candidate.get("confidence_score", 0.0))
                    ),
                    priority_score=float(
                        str(candidate.get("priority_score", 0.0))
                    ),
                    url_relevance_score=float(
                        str(candidate.get("url_relevance_score", 0.0))
                    ),
                    is_active=False,
                    is_approved=False,
                    is_auto_disabled=False,
                    scan_interval_hours=12,
                    last_discovered_at=datetime.now(timezone.utc),
                )
                db.add(source)
            else:
                self._improve_existing_source(
                    existing=source,
                    candidate=candidate,
                )

            source.ai_status = "ERROR"
            source.ai_confidence = None
            source.ai_source_type = None
            source.ai_reason = reason
            source.ai_analyzed_at = datetime.now(timezone.utc)
            source.source_fingerprint = None
            source.is_active = False
            source.is_approved = False
            db.commit()

        return SourceProcessingOutcome(
            status="ERROR",
            created=created,
            analyzed=True,
            reason=reason,
        )

    @staticmethod
    def _apply_source_analysis(
        *,
        source: Source,
        analysis: SourceAnalysis,
        fingerprint: str,
    ) -> None:
        source.ai_status = analysis.decision.value
        source.ai_confidence = analysis.confidence
        source.ai_source_type = analysis.source_type
        source.source_role = analysis.source_role.value
        source.ai_reason = analysis.reason
        source.ai_analyzed_at = datetime.now(timezone.utc)
        source.source_fingerprint = fingerprint
        source.is_approved = analysis.decision is AnalysisDecision.ACCEPT
        source.is_active = source.is_approved

        if analysis.source_name:
            source.organisation_name = analysis.source_name
        elif analysis.organisation:
            source.organisation_name = analysis.organisation

        if analysis.source_type:
            source.source_type = analysis.source_type

        if source.is_approved:
            source.is_auto_disabled = False
            source.disabled_reason = None
        else:
            source.disabled_reason = analysis.reason[:500]
        
    def _improve_existing_source(
        self,
        existing: Source,
        candidate: dict[str, object],
    ) -> None:
        candidate_confidence = float(
            str(candidate["confidence_score"])
        )

        candidate_url_score = float(
            str(candidate["url_relevance_score"])
        )

        existing_url_score = float(
            existing.url_relevance_score or 0
        )

        existing.discovery_query = merge_discovery_metadata(
            existing.discovery_query,
            categories=(str(candidate.get("discovery_category", "")),),
            queries=(str(candidate.get("discovery_query", "")),),
            tags=tuple(
                str(tag)
                for tag in cast(
                    Iterable[object],
                    candidate.get("tags", ()),
                )
            ),
        )

        if (
            candidate_confidence
            > float(existing.confidence_score or 0)
        ):
            existing.confidence_score = (
                candidate_confidence
            )

            existing.discovered_from = str(
                candidate["discovered_from"]
            )

        if candidate_url_score > existing_url_score:
            existing.monitor_url = str(
                candidate["monitor_url"]
            )

            existing.url_relevance_score = (
                candidate_url_score
            )

        existing.priority_score = max(
            float(existing.priority_score or 0),
            float(str(candidate["priority_score"])),
        )

        # A direct tender document is useful scanner evidence even if the
        # publisher's overall discovery score has not increased.
        new_evidence = str(candidate.get("document_evidence_url") or "")
        old_evidence = str(existing.discovered_from or "")
        if new_evidence and not self._is_document_url(old_evidence):
            existing.discovered_from = new_evidence

        if not existing.organisation_name:
            existing.organisation_name = str(
                candidate["organisation_name"]
            )

        if (
            not existing.source_type
            or existing.source_type
            in {"Unknown", "Organisation"}
        ):
            existing.source_type = str(
                candidate["source_type"]
            )

        existing.last_discovered_at = (
            datetime.now(timezone.utc)
        )

    def _fetch_source_content(self, url: str) -> str:
        response = self.content_session.get(
            url,
            timeout=self.CONTENT_REQUEST_TIMEOUT,
        )
        if not response.ok:
            raise SourceContentError(
                "BLOCKED" if response.status_code in {401, 403, 429, 451} else "HTTP_ERROR",
                response.status_code,
            )

        content_type = response.headers.get("Content-Type", "").lower()
        if content_type and not any(
            value in content_type
            for value in ("text/html", "application/xhtml+xml", "text/plain")
        ):
            raise SourceContentError(
                "UNSUPPORTED_CONTENT", response.status_code,
            )

        if "text/plain" in content_type:
            text = response.text
        else:
            soup = BeautifulSoup(response.text, "html.parser")
            for element in soup(("script", "style", "noscript", "template")):
                element.decompose()
            # Expose real publication navigation to the AI; a site's home
            # text alone frequently omits its tender/announcements URLs.
            publisher_netloc = urlparse(url).netloc.lower().removeprefix("www.")
            useful_terms = (
                "tender", "procurement", "announcement", "notice",
                "opportunit", "consult", "call for", "rfp", "rfq",
                "eoi", "vacanc", "careers", "bid", ".pdf",
            )
            links: list[str] = []
            for anchor in soup.find_all("a", href=True):
                href = str(anchor.get("href") or "").strip()
                target = urljoin(url, href)
                parsed = urlparse(target)
                target_domain = parsed.netloc.lower().removeprefix("www.")
                if parsed.scheme not in {"http", "https"} or target_domain != publisher_netloc:
                    continue
                label = anchor.get_text(" ", strip=True)[:100]
                if any(term in (label + " " + parsed.path).lower() for term in useful_terms):
                    links.append(f"{label}: {target}")
                if len(links) >= 24:
                    break
            text = soup.get_text(" ", strip=True)
            if links:
                # Put links first so the 50k-character AI context limit
                # never hides publication navigation behind a long homepage.
                text = (
                    "Publisher's public opportunity navigation:\n"
                    + "\n".join(links) + "\n" + text
                )

        meaningful_content = self._clean_text(text)
        if not meaningful_content:
            raise SourceContentError(
                "EMPTY_CONTENT", response.status_code,
            )

        return meaningful_content[: self.MAX_FETCHED_CONTENT_CHARS]
        
    def _calculate_confidence(
            self,
            combined_text: str,
            domain: str,
            url: str,
        ) -> float:
            """
            Calculate a source confidence score from 0 to 100.
            """
            score = 0.0

            if ".gov." in domain or ".ac." in domain:
                score += 30

            if self.country.lower() in combined_text:
                score += 20

            opportunity_matches = sum(
                1
                for term in self.OPPORTUNITY_TERMS
                if term in combined_text
            )

            score += min(
                opportunity_matches * 5,
                25,
            )

            high_value_matches = sum(
                1
                for term in self.HIGH_VALUE_TERMS
                if term in combined_text
            )

            score += min(
                high_value_matches * 5,
                20,
            )

            opportunity_path_terms = (
                "/tender",
                "/procurement",
                "/career",
                "/vacanc",
                "/job",
                "/opportun",
                "/consult",
                "/rfp",
                "/eoi",
            )

            if any(
                term in url.lower()
                for term in opportunity_path_terms
            ):
                score += 15

            return min(
                round(score, 2),
                100.0,
            )

    def _looks_country_relevant(
            self,
            combined_text: str,
            domain: str,
        ) -> bool:
            if ".gov." in domain or ".ac." in domain:
                return True

            return self.country.lower() in combined_text

    def _infer_organisation_name(
            self,
            title: str,
            domain: str,
        ) -> str:
            """
            Infer a readable organisation name from the result title.
            """
            cleaned_title = self._clean_text(
                title
            )

            separators = (
                " | ",
                " - ",
                " – ",
                " — ",
                " :: ",
            )

            parts = [cleaned_title]

            for separator in separators:
                if separator in cleaned_title:
                    parts = [
                        part.strip()
                        for part in cleaned_title.split(
                            separator
                        )
                        if part.strip()
                    ]
                    break

            generic_phrases = (
                "tender",
                "tenders",
                "procurement",
                "vacancy",
                "vacancies",
                "career",
                "careers",
                "opportunity",
                "opportunities",
                "request for proposal",
                "expression of interest",
                "consultancy",
                "job",
                "jobs",
                "home",
            )

            for part in reversed(parts):
                lowered_part = part.lower()

                if len(part) < 3:
                    continue

                if any(
                    phrase == lowered_part
                    for phrase in generic_phrases
                ):
                    continue

                if len(part) <= 255:
                    return part

            domain_name = domain.split(".")[0]

            domain_name = re.sub(
                r"[-_]+",
                " ",
                domain_name,
            )

            return domain_name.title()

    def _infer_source_type(
            self,
            combined_text: str,
        ) -> str:
            type_rules = (
                (
                    "Government Institution",
                    (
                        "government",
                        "ministry",
                        "authority",
                        "district",
                        "public institution",
                        ".gov.",
                    ),
                ),
                (
                    "University",
                    (
                        "university",
                        "college",
                        "institute of higher education",
                        ".ac.",
                        ".edu.",
                    ),
                ),
                (
                    "NGO",
                    (
                        "non-governmental organisation",
                        "non-governmental organization",
                        "ngo",
                        "humanitarian",
                        "charity",
                    ),
                ),
                (
                    "Development Partner",
                    (
                        "united nations",
                        "world bank",
                        "development partner",
                        "embassy",
                        "international development",
                    ),
                ),
                (
                    "Job Portal",
                    (
                        "job portal",
                        "jobs portal",
                        "vacancy portal",
                    ),
                ),
                (
                    "Procurement Portal",
                    (
                        "procurement portal",
                        "tender portal",
                        "e-procurement",
                    ),
                ),
                (
                    "Private Company",
                    (
                        "company",
                        "limited",
                        "ltd",
                        "plc",
                        "corporation",
                        "bank",
                        "insurance",
                    ),
                ),
            )

            for source_type, terms in type_rules:
                if any(
                    term in combined_text
                    for term in terms
                ):
                    return source_type

            return "Unknown"

    def _is_excluded_domain(
            self,
            domain: str,
        ) -> bool:
            return any(
                domain == excluded
                or domain.endswith(
                    f".{excluded}"
                )
                for excluded
                in self.EXCLUDED_DOMAINS
            )

    def _is_document_url(
            self,
            url: str,
        ) -> bool:
            path = urlparse(
                url
            ).path.lower()

            return path.endswith(
                self.FILE_EXTENSIONS
            )

    def _normalise_url(
            self,
            url: str,
        ) -> str:
            cleaned_url = self._clean_text(
                url
            )

            if not cleaned_url:
                return ""

            if not cleaned_url.startswith(
                ("http://", "https://")
            ):
                return ""

            parsed = urlparse(
                cleaned_url
            )

            if not parsed.netloc:
                return ""

            path = (
                parsed.path.rstrip("/")
                or "/"
            )

            return urlunparse(
                (
                    parsed.scheme.lower(),
                    parsed.netloc.lower(),
                    path,
                    "",
                    "",
                    "",
                )
            )

    def _normalise_domain(
            self,
            domain: str,
        ) -> str:
            cleaned_domain = (
                self._clean_text(
                    domain
                )
                .lower()
                .split(":")[0]
            )

            if cleaned_domain.startswith(
                "www."
            ):
                cleaned_domain = (
                    cleaned_domain[4:]
                )

            return cleaned_domain.rstrip(
                "."
            )

    def _wait(self) -> None:
            if self.REQUEST_DELAY_SECONDS > 0:
                time.sleep(
                    self.REQUEST_DELAY_SECONDS
                )

    @staticmethod
    def _clean_text(
            value: object,
        ) -> str:
            if value is None:
                return ""

            text = str(value)
            text = text.replace(
                "\xa0",
                " ",
            )

            text = re.sub(
                r"<[^>]+>",
                " ",
                text,
            )

            text = re.sub(
                r"\s+",
                " ",
                text,
            )

            return text.strip()


def run_source_discovery(
    country: str | None = None,
) -> dict[str, int | float]:
    discovery = SourceDiscovery(country=country)

    return discovery.run()


if __name__ == "__main__":
    run_source_discovery()
