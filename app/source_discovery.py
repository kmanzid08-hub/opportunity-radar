from __future__ import annotations

import os
import re
import time
from dotenv import load_dotenv
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse, urlunparse
from datetime import datetime, timezone

from app.discovery.categories import (
    DISCOVERY_CATEGORIES,
    DiscoveryCategory,
    infer_discovery_tags,
)
from app.discovery.metadata import (
    merge_discovery_metadata,
)
from app.source_quality import SourceQualityEvaluator

import requests
from sqlalchemy import or_, select

from app.database import Base, SessionLocal, engine
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
    REQUEST_DELAY_SECONDS = 1.0

    RESULTS_PER_QUERY = 20
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
    ) -> None:
        self.country = (
            country
            or os.getenv("DISCOVERY_COUNTRY", "Rwanda")
        ).strip()

        if not self.country:
            raise ValueError("A discovery country is required.")

        self.api_key = os.getenv(
            "BRAVE_API_KEY",
            "",
        ).strip()
        self.quality_evaluator = SourceQualityEvaluator()

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

    def run(self) -> dict[str, int | float]:
        """
        Run all configured discovery queries.

        Returns summary counts.
        """
        Base.metadata.create_all(
            bind=engine
        )

        total_results = 0
        candidate_results = 0
        added_sources = 0
        existing_sources = 0
        rejected_results = 0
        failed_queries = 0
        queries_executed = 0
        query_runtime_total = 0.0

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
                        candidate = self._build_candidate(
                            result=result,
                            query=query,
                            category=category,
                        )

                        if candidate is None:
                            rejected_results += 1
                            query_rejected += 1
                            category_stats.rejected += 1
                            continue

                        candidate_results += 1
                        query_accepted += 1
                        category_stats.accepted += 1
                        was_added = self._save_candidate(candidate)

                        if was_added:
                            added_sources += 1
                            print(
                                "  [NEW SOURCE] "
                                f"{candidate['organisation_name']}"
                            )
                            print(f"      {candidate['monitor_url']}")
                            print(
                                "      Discovery score: "
                                f"{candidate['discovery_score']}"
                            )
                            print(
                                "      Tags: "
                                + ", ".join(candidate["tags"])
                            )
                        else:
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
            "existing_sources": existing_sources,
            "rejected_results": rejected_results,
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
        response = self.session.get(
            self.API_URL,
            params={
                "q": query,
                "count": self.RESULTS_PER_QUERY,
                "search_lang": "en",
                "safesearch": "moderate",
            },
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
        assessment = self.quality_evaluator.assess(
            title=result.title,
            description=result.description,
            url=result.url,
            discovery_query=query,
            country=self.country,
        )

        if not assessment.accepted:
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
            "organisation_name": (
                assessment.organisation_name
            ),
            "base_url": assessment.base_url,
            "monitor_url": (
                assessment.monitor_url
            ),
            "domain": domain,
            "source_type": (
                assessment.source_type
            ),
            "discovered_from": result.url,
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
            "is_active": True,
            "is_approved": True,
            "is_auto_disabled": False,
            "last_discovered_at": (
                datetime.now(timezone.utc)
            ),
        }

    def _save_candidate(
        self,
        candidate: dict[str, object],
    ) -> bool:
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
            return False

        candidate = {
            **candidate,
            "monitor_url": monitor_url,
            "domain": domain,
        }

        discovery_metadata = merge_discovery_metadata(
            None,
            categories=(str(candidate["discovery_category"]),),
            queries=(str(candidate["discovery_query"]),),
            tags=tuple(candidate["tags"]),
        )

        with SessionLocal() as db:
            existing = db.scalar(
                select(Source).where(
                    or_(
                        Source.monitor_url
                        == monitor_url,
                        Source.domain
                        == domain,
                    )
                )
            )

            if existing is not None:
                self._improve_existing_source(
                    existing=existing,
                    candidate=candidate,
                )

                db.commit()
                return False

            source = Source(
                organisation_name=str(
                    candidate["organisation_name"]
                ),
                base_url=str(
                    candidate["base_url"]
                ),
                monitor_url=monitor_url,
                domain=domain,
                source_type=str(
                    candidate["source_type"]
                ),
                discovered_from=str(
                    candidate["discovered_from"]
                ),
                discovery_query=discovery_metadata,
                confidence_score=float(
                    candidate["confidence_score"]
                ),
                priority_score=float(
                    candidate["priority_score"]
                ),
                url_relevance_score=float(
                    candidate[
                        "url_relevance_score"
                    ]
                ),
                is_active=True,
                is_approved=True,
                is_auto_disabled=False,
                scan_interval_hours=12,
                last_discovered_at=(
                    datetime.now(timezone.utc)
                ),
            )

            db.add(source)
            db.commit()

            return True
        
    def _improve_existing_source(
        self,
        existing: Source,
        candidate: dict[str, object],
    ) -> None:
        candidate_confidence = float(
            candidate["confidence_score"]
        )

        candidate_url_score = float(
            candidate["url_relevance_score"]
        )

        existing_url_score = float(
            existing.url_relevance_score or 0
        )

        existing.discovery_query = merge_discovery_metadata(
            existing.discovery_query,
            categories=(str(candidate["discovery_category"]),),
            queries=(str(candidate["discovery_query"]),),
            tags=tuple(candidate["tags"]),
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
            float(candidate["priority_score"]),
        )

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

        existing.is_approved = True
        existing.is_active = True
        existing.is_auto_disabled = False
        existing.disabled_reason = None
        existing.last_discovered_at = (
            datetime.now(timezone.utc)
        )
        
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
