from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv
from sqlalchemy import select

from app.database import SessionLocal
from app.discovery.queries import build_discovery_queries
from app.discovery.source_classifier import classify_source
from app.discovery.source_repository import save_discovered_source
from app.models import Organization, OpportunityPreference


load_dotenv()


BRAVE_SEARCH_ENDPOINT = (
    "https://api.search.brave.com/res/v1/web/search"
)

DEFAULT_RESULTS_PER_QUERY = 20
DEFAULT_REQUEST_TIMEOUT = 30
DEFAULT_MINIMUM_CONFIDENCE = 40.0
DEFAULT_DELAY_BETWEEN_REQUESTS = 1.0

DEFAULT_DISCOVERY_COUNTRY = "Rwanda"


@dataclass
class SearchResult:
    title: str
    url: str
    description: str


@dataclass
class DiscoveryStatistics:
    queries_processed: int = 0
    results_received: int = 0
    sources_qualified: int = 0
    new_sources_saved: int = 0
    duplicate_sources: int = 0
    rejected_sources: int = 0
    failed_queries: int = 0


@dataclass(frozen=True)
class DiscoveryQuery:
    country: str
    query: str


class BraveSearchError(Exception):
    """
    Raised when Brave Search cannot complete a request.
    """


def get_api_key() -> str:
    """
    Read the Brave Search API key.

    Both names are supported because local development
    and GitHub Actions may use different historical names.
    """

    return (
        os.getenv(
            "BRAVE_SEARCH_API_KEY",
            "",
        ).strip()
        or os.getenv(
            "BRAVE_API_KEY",
            "",
        ).strip()
    )


def clean_text(
    value: Any,
) -> str:
    """
    Convert an API value into clean single-line text.
    """

    if value is None:
        return ""

    text = str(
        value
    )

    return " ".join(
        text.split()
    ).strip()


def clean_url(
    value: Any,
) -> str:
    """
    Convert a URL value into a normalized string.
    """

    if value is None:
        return ""

    return str(
        value
    ).strip()


def is_http_url(
    url: str,
) -> bool:
    """
    Accept only normal HTTP and HTTPS URLs.
    """

    try:
        parsed = urlparse(
            url
        )

    except ValueError:
        return False

    return (
        parsed.scheme
        in {
            "http",
            "https",
        }
        and bool(
            parsed.netloc
        )
    )


def normalize_country_name(
    value: str | None,
) -> str:
    """
    Normalize one configured country name.
    """

    if not value:
        return ""

    return " ".join(
        value.split()
    ).strip()


def deduplicate_countries(
    countries: list[str],
) -> list[str]:
    """
    Remove duplicate country names while preserving order.
    """

    cleaned: list[str] = []
    seen: set[str] = set()

    for value in countries:
        country = normalize_country_name(
            value
        )

        if not country:
            continue

        key = country.casefold()

        if key in seen:
            continue

        seen.add(
            key
        )

        cleaned.append(
            country
        )

    return cleaned


def get_discovery_countries() -> list[str]:
    """
    Determine which countries Website Discovery should search.

    Priority:

    1. OpportunityPreference.countries
    2. Organization.country
    3. Rwanda fallback

    This is temporary single-workspace behavior.

    Authentication / tenant context will later determine
    which organization's configuration is used.
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

        configured_countries: list[str] = []

        if (
            preference is not None
            and preference.countries
        ):
            configured_countries.extend(
                preference.countries
            )

        countries = deduplicate_countries(
            configured_countries
        )

        if countries:
            return countries

        organization_country = (
            normalize_country_name(
                organization.country
            )
        )

        if organization_country:
            return [
                organization_country
            ]

    return [
        DEFAULT_DISCOVERY_COUNTRY
    ]


def build_profile_discovery_queries() -> list[DiscoveryQuery]:
    """
    Generate Website Discovery queries using saved
    Business Profile target countries.
    """

    countries = (
        get_discovery_countries()
    )

    discovered_queries: list[
        DiscoveryQuery
    ] = []

    seen_queries: set[str] = set()

    for country in countries:
        country_queries = (
            build_discovery_queries(
                country
            )
        )

        for query in country_queries:
            normalized_query = (
                " ".join(
                    query.split()
                )
                .strip()
            )

            if not normalized_query:
                continue

            key = (
                normalized_query.casefold()
            )

            if key in seen_queries:
                continue

            seen_queries.add(
                key
            )

            discovered_queries.append(
                DiscoveryQuery(
                    country=country,
                    query=normalized_query,
                )
            )

    return discovered_queries


def search_brave(
    query: str,
    count: int = DEFAULT_RESULTS_PER_QUERY,
    timeout: int = DEFAULT_REQUEST_TIMEOUT,
) -> list[SearchResult]:
    """
    Search the web through Brave Search API.

    Geography is expressed in the search query itself.

    We intentionally do not force Brave's `country`
    parameter because Opportunity Radar now supports
    arbitrary target countries instead of always using RW.
    """

    api_key = get_api_key()

    if not api_key:
        raise BraveSearchError(
            "Brave Search API key is not configured. "
            "Set BRAVE_SEARCH_API_KEY or BRAVE_API_KEY."
        )

    safe_count = max(
        1,
        min(
            count,
            20,
        ),
    )

    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": api_key,
        "User-Agent": "OpportunityRadar/1.0",
    }

    params = {
        "q": query,
        "count": safe_count,
        "search_lang": "en",
        "safesearch": "moderate",
        "text_decorations": False,
        "spellcheck": True,
    }

    try:
        response = requests.get(
            BRAVE_SEARCH_ENDPOINT,
            headers=headers,
            params=params,
            timeout=timeout,
        )

    except requests.Timeout as exc:
        raise BraveSearchError(
            f"Search timed out for query: {query}"
        ) from exc

    except requests.RequestException as exc:
        raise BraveSearchError(
            "Network error while searching for "
            f"{query}: {exc}"
        ) from exc

    if response.status_code == 401:
        raise BraveSearchError(
            "Brave Search rejected the API key."
        )

    if response.status_code == 403:
        raise BraveSearchError(
            "Access to Brave Search was forbidden. "
            "Check the API subscription and permissions."
        )

    if response.status_code == 429:
        raise BraveSearchError(
            "Brave Search rate limit reached."
        )

    if response.status_code != 200:
        response_preview = (
            response.text[:300]
        )

        raise BraveSearchError(
            "Brave Search returned HTTP "
            f"{response.status_code}: "
            f"{response_preview}"
        )

    try:
        payload: dict[
            str,
            Any,
        ] = response.json()

    except ValueError as exc:
        raise BraveSearchError(
            "Brave Search returned invalid JSON."
        ) from exc

    web_results = (
        payload
        .get(
            "web",
            {},
        )
        .get(
            "results",
            [],
        )
    )

    if not isinstance(
        web_results,
        list,
    ):
        return []

    results: list[
        SearchResult
    ] = []

    for item in web_results:
        if not isinstance(
            item,
            dict,
        ):
            continue

        title = clean_text(
            item.get(
                "title"
            )
        )

        url = clean_url(
            item.get(
                "url"
            )
        )

        description = clean_text(
            item.get(
                "description"
            )
        )

        if (
            not title
            or not url
        ):
            continue

        if not is_http_url(
            url
        ):
            continue

        results.append(
            SearchResult(
                title=title,
                url=url,
                description=description,
            )
        )

    return results


def infer_organisation_name(
    title: str,
    url: str,
) -> str | None:
    """
    Produce a preliminary organization name.

    The website scanner may later replace this with
    better metadata extracted from the website.
    """

    parsed = urlparse(
        url
    )

    domain = (
        parsed.netloc
        .lower()
    )

    if domain.startswith(
        "www."
    ):
        domain = domain[
            4:
        ]

    if not domain:
        return None

    domain_name = (
        domain.split(
            "."
        )[0]
    )

    organisation = (
        domain_name
        .replace(
            "-",
            " ",
        )
        .replace(
            "_",
            " ",
        )
        .strip()
        .title()
    )

    generic_domains = {
        "google",
        "bing",
        "brave",
        "facebook",
        "linkedin",
        "youtube",
        "twitter",
        "x",
    }

    if (
        domain_name
        in generic_domains
    ):
        return None

    if organisation:
        return organisation

    if title:
        return title[
            :255
        ]

    return None


def should_ignore_result(
    url: str,
) -> bool:
    """
    Exclude search engines, social-media pages
    and unsupported binary/media links.
    """

    parsed = urlparse(
        url
    )

    domain = (
        parsed.netloc
        .lower()
    )

    if domain.startswith(
        "www."
    ):
        domain = domain[
            4:
        ]

    blocked_domains = {
        "google.com",
        "bing.com",
        "search.brave.com",
        "facebook.com",
        "instagram.com",
        "twitter.com",
        "x.com",
        "youtube.com",
        "tiktok.com",
    }

    if (
        domain
        in blocked_domains
    ):
        return True

    blocked_extensions = (
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".webp",
        ".mp4",
        ".mp3",
        ".zip",
    )

    path = (
        parsed.path
        .lower()
    )

    return path.endswith(
        blocked_extensions
    )


def process_search_result(
    result: SearchResult,
    query: str,
    country: str,
    minimum_confidence: float,
) -> tuple[str, float]:
    """
    Classify and save one search result.

    Returns:

    saved
    duplicate
    rejected
    ignored
    """

    if should_ignore_result(
        result.url
    ):
        return (
            "ignored",
            0.0,
        )

    classification = (
        classify_source(
            title=result.title,
            description=result.description,
            url=result.url,
            country=country,
        )
    )

    if (
        classification.confidence_score
        < minimum_confidence
    ):
        return (
            "rejected",
            classification.confidence_score,
        )

    organisation_name = (
        infer_organisation_name(
            title=result.title,
            url=result.url,
        )
    )

    was_saved = (
        save_discovered_source(
            monitor_url=result.url,
            organisation_name=(
                organisation_name
            ),
            source_type=(
                classification.source_type
            ),
            discovered_from=(
                "Brave Search API"
            ),
            discovery_query=(
                query
            ),
            confidence_score=(
                classification.confidence_score
            ),
        )
    )

    if was_saved:
        return (
            "saved",
            classification.confidence_score,
        )

    return (
        "duplicate",
        classification.confidence_score,
    )


def run_search_discovery(
    queries: list[str] | None = None,
    results_per_query: int = DEFAULT_RESULTS_PER_QUERY,
    minimum_confidence: float = DEFAULT_MINIMUM_CONFIDENCE,
    delay_between_requests: float = DEFAULT_DELAY_BETWEEN_REQUESTS,
) -> DiscoveryStatistics:
    """
    Run autonomous source discovery.

    When `queries` is omitted, Website Discovery reads
    the active Business Profile countries and generates
    country-specific discovery queries automatically.

    Explicit queries remain supported for tests and
    manual diagnostics.
    """

    statistics = (
        DiscoveryStatistics()
    )

    api_key = (
        get_api_key()
    )

    if not api_key:
        print(
            "=" * 60
        )

        print(
            "Opportunity Radar - Source Discovery"
        )

        print(
            "=" * 60
        )

        print()

        print(
            "Discovery is configured but currently inactive."
        )

        print(
            "No Brave Search API key was found."
        )

        print()

        print(
            "Configure either:"
        )

        print(
            "BRAVE_SEARCH_API_KEY=..."
        )

        print(
            "or:"
        )

        print(
            "BRAVE_API_KEY=..."
        )

        print(
            "=" * 60
        )

        return statistics

    if queries is not None:
        fallback_country = (
            get_discovery_countries()[0]
        )

        active_queries = [
            DiscoveryQuery(
                country=(
                    fallback_country
                ),
                query=query,
            )
            for query in queries
            if query.strip()
        ]

    else:
        active_queries = (
            build_profile_discovery_queries()
        )

    countries = (
        deduplicate_countries(
            [
                item.country
                for item
                in active_queries
            ]
        )
    )

    print(
        "=" * 70
    )

    print(
        "Opportunity Radar - Autonomous Source Discovery"
    )

    print(
        "=" * 70
    )

    print(
        "Target countries: "
        + ", ".join(
            countries
        )
    )

    print(
        f"Countries scheduled: "
        f"{len(countries)}"
    )

    print(
        f"Queries scheduled: "
        f"{len(active_queries)}"
    )

    print(
        f"Results per query: "
        f"{results_per_query}"
    )

    print(
        "Minimum source confidence: "
        f"{minimum_confidence}"
    )

    print(
        "Delay between queries: "
        f"{max(0, delay_between_requests):.2f}s"
    )

    print(
        "=" * 70
    )

    total_started_at = (
        time.perf_counter()
    )

    current_country: str | None = (
        None
    )

    for query_number, item in enumerate(
        active_queries,
        start=1,
    ):
        if (
            item.country
            != current_country
        ):
            current_country = (
                item.country
            )

            print()

            print(
                "-" * 70
            )

            print(
                f"Country: "
                f"{current_country}"
            )

            print(
                "-" * 70
            )

        print()

        print(
            f"[{query_number}/"
            f"{len(active_queries)}] "
            f"Searching: "
            f"{item.query}"
        )

        query_started_at = (
            time.perf_counter()
        )

        try:
            results = search_brave(
                query=item.query,
                count=results_per_query,
            )

        except BraveSearchError as exc:
            statistics.failed_queries += (
                1
            )

            query_elapsed = (
                time.perf_counter()
                - query_started_at
            )

            print(
                "Search failed "
                f"after "
                f"{query_elapsed:.2f}s: "
                f"{exc}"
            )

            continue

        statistics.queries_processed += (
            1
        )

        statistics.results_received += (
            len(
                results
            )
        )

        print(
            f"Results received: "
            f"{len(results)}"
        )

        query_saved = 0
        query_duplicates = 0
        query_rejected = 0

        for result in results:
            status, score = (
                process_search_result(
                    result=result,
                    query=item.query,
                    country=item.country,
                    minimum_confidence=(
                        minimum_confidence
                    ),
                )
            )

            if status == "saved":
                statistics.sources_qualified += (
                    1
                )

                statistics.new_sources_saved += (
                    1
                )

                query_saved += 1

                print(
                    f"  SAVED "
                    f"[{score:.0f}] "
                    f"{result.title[:80]}"
                )

            elif status == "duplicate":
                statistics.sources_qualified += (
                    1
                )

                statistics.duplicate_sources += (
                    1
                )

                query_duplicates += (
                    1
                )

                print(
                    f"  EXISTS "
                    f"[{score:.0f}] "
                    f"{result.title[:80]}"
                )

            elif status in {
                "rejected",
                "ignored",
            }:
                statistics.rejected_sources += (
                    1
                )

                query_rejected += (
                    1
                )

        query_elapsed = (
            time.perf_counter()
            - query_started_at
        )

        print(
            "Query summary: "
            f"saved={query_saved}, "
            f"duplicates={query_duplicates}, "
            f"rejected={query_rejected}, "
            f"runtime={query_elapsed:.2f}s"
        )

        if (
            query_number
            < len(
                active_queries
            )
        ):
            time.sleep(
                max(
                    0,
                    delay_between_requests,
                )
            )

    total_elapsed = (
        time.perf_counter()
        - total_started_at
    )

    print()

    print(
        "=" * 70
    )

    print(
        "Source Discovery Completed"
    )

    print(
        "=" * 70
    )

    print(
        "Countries searched:       "
        f"{len(countries)}"
    )

    print(
        "Queries processed:        "
        f"{statistics.queries_processed}"
    )

    print(
        "Failed queries:           "
        f"{statistics.failed_queries}"
    )

    print(
        "Search results received:  "
        f"{statistics.results_received}"
    )

    print(
        "Qualified sources:        "
        f"{statistics.sources_qualified}"
    )

    print(
        "New sources saved:        "
        f"{statistics.new_sources_saved}"
    )

    print(
        "Existing sources:         "
        f"{statistics.duplicate_sources}"
    )

    print(
        "Rejected results:         "
        f"{statistics.rejected_sources}"
    )

    print(
        "Total runtime:            "
        f"{total_elapsed:.2f}s"
    )

    print(
        "=" * 70
    )

    return statistics


if __name__ == "__main__":
    run_search_discovery()
