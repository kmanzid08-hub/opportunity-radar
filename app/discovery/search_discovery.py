from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

from app.discovery.queries import DISCOVERY_QUERIES
from app.discovery.source_classifier import classify_source
from app.discovery.source_repository import save_discovered_source


load_dotenv()


BRAVE_SEARCH_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

DEFAULT_RESULTS_PER_QUERY = 20
DEFAULT_REQUEST_TIMEOUT = 30
DEFAULT_MINIMUM_CONFIDENCE = 40.0
DEFAULT_DELAY_BETWEEN_REQUESTS = 1.0


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


class BraveSearchError(Exception):
    """Raised when Brave Search cannot complete a request."""


def get_api_key() -> str:
    """
    Read the Brave Search API key from the environment.

    The application is fully configured even when the key is absent.
    Discovery will simply remain disabled until the key is added.
    """
    return os.getenv("BRAVE_SEARCH_API_KEY", "").strip()


def search_brave(
    query: str,
    count: int = DEFAULT_RESULTS_PER_QUERY,
    timeout: int = DEFAULT_REQUEST_TIMEOUT,
) -> list[SearchResult]:
    """
    Search the web through Brave Search API.

    Args:
        query:
            Search expression to send to Brave Search.
        count:
            Maximum number of results requested.
        timeout:
            HTTP timeout in seconds.

    Returns:
        A list of normalised SearchResult objects.

    Raises:
        BraveSearchError:
            When the API key is missing, the request fails, or Brave returns
            an unexpected response.
    """
    api_key = get_api_key()

    if not api_key:
        raise BraveSearchError(
            "BRAVE_SEARCH_API_KEY is not configured. "
            "Add it to the project's .env file."
        )

    safe_count = max(1, min(count, 20))

    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": api_key,
        "User-Agent": "OpportunityRadar/1.0",
    }

    params = {
        "q": query,
        "count": safe_count,
        "country": "RW",
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
            f"Network error while searching for: {query}. Error: {exc}"
        ) from exc

    if response.status_code == 401:
        raise BraveSearchError(
            "Brave Search rejected the API key. "
            "Check BRAVE_SEARCH_API_KEY in the .env file."
        )

    if response.status_code == 403:
        raise BraveSearchError(
            "Access to Brave Search was forbidden. "
            "Check the API subscription and permissions."
        )

    if response.status_code == 429:
        raise BraveSearchError(
            "Brave Search rate limit reached. "
            "Wait before running discovery again."
        )

    if response.status_code != 200:
        response_preview = response.text[:300]

        raise BraveSearchError(
            "Brave Search returned HTTP "
            f"{response.status_code}: {response_preview}"
        )

    try:
        payload: dict[str, Any] = response.json()
    except ValueError as exc:
        raise BraveSearchError(
            "Brave Search returned invalid JSON."
        ) from exc

    web_results = payload.get("web", {}).get("results", [])

    if not isinstance(web_results, list):
        return []

    results: list[SearchResult] = []

    for item in web_results:
        if not isinstance(item, dict):
            continue

        title = clean_text(item.get("title"))
        url = clean_url(item.get("url"))
        description = clean_text(item.get("description"))

        if not title or not url:
            continue

        if not is_http_url(url):
            continue

        results.append(
            SearchResult(
                title=title,
                url=url,
                description=description,
            )
        )

    return results


def clean_text(value: Any) -> str:
    """
    Convert an API value into clean single-line text.
    """
    if value is None:
        return ""

    text = str(value)

    return " ".join(text.split()).strip()


def clean_url(value: Any) -> str:
    """
    Convert a URL value into a normalised string.
    """
    if value is None:
        return ""

    return str(value).strip()


def is_http_url(url: str) -> bool:
    """
    Accept only normal HTTP and HTTPS URLs.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return False

    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.netloc)
    )


def infer_organisation_name(
    title: str,
    url: str,
) -> str | None:
    """
    Produce a preliminary organisation name.

    This is only a best-effort value. A later website crawler can replace it
    with a better name extracted from the website itself.
    """
    parsed = urlparse(url)

    domain = parsed.netloc.lower()

    if domain.startswith("www."):
        domain = domain[4:]

    if not domain:
        return None

    domain_name = domain.split(".")[0]

    organisation = (
        domain_name
        .replace("-", " ")
        .replace("_", " ")
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

    if domain_name in generic_domains:
        return None

    if organisation:
        return organisation

    if title:
        return title[:255]

    return None


def should_ignore_result(url: str) -> bool:
    """
    Exclude search engines, social media pages, and unsupported links.
    """
    parsed = urlparse(url)

    domain = parsed.netloc.lower()

    if domain.startswith("www."):
        domain = domain[4:]

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

    if domain in blocked_domains:
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

    path = parsed.path.lower()

    return path.endswith(blocked_extensions)


def process_search_result(
    result: SearchResult,
    query: str,
    minimum_confidence: float,
) -> tuple[str, float]:
    """
    Classify and save one result.

    Returns:
        A tuple containing the result status and confidence score.

    Status values:
        saved
        duplicate
        rejected
        ignored
    """
    if should_ignore_result(result.url):
        return "ignored", 0.0

    classification = classify_source(
        title=result.title,
        description=result.description,
        url=result.url,
    )

    if classification.confidence_score < minimum_confidence:
        return "rejected", classification.confidence_score

    organisation_name = infer_organisation_name(
        title=result.title,
        url=result.url,
    )

    was_saved = save_discovered_source(
        monitor_url=result.url,
        organisation_name=organisation_name,
        source_type=classification.source_type,
        discovered_from="Brave Search API",
        discovery_query=query,
        confidence_score=classification.confidence_score,
    )

    if was_saved:
        return "saved", classification.confidence_score

    return "duplicate", classification.confidence_score


def run_search_discovery(
    queries: list[str] | None = None,
    results_per_query: int = DEFAULT_RESULTS_PER_QUERY,
    minimum_confidence: float = DEFAULT_MINIMUM_CONFIDENCE,
    delay_between_requests: float = DEFAULT_DELAY_BETWEEN_REQUESTS,
) -> DiscoveryStatistics:
    """
    Run the complete autonomous source-discovery process.

    The function:
        1. Searches the internet using defined discovery queries.
        2. Classifies every result.
        3. Rejects weak or irrelevant results.
        4. Saves qualified website pages in the sources table.
        5. Avoids adding duplicate monitor URLs.

    If no API key is configured, it exits safely without affecting the rest
    of the Opportunity Radar application.
    """
    statistics = DiscoveryStatistics()

    api_key = get_api_key()

    if not api_key:
        print("=" * 60)
        print("Opportunity Radar - Source Discovery")
        print("=" * 60)
        print()
        print("Discovery is configured but currently inactive.")
        print("BRAVE_SEARCH_API_KEY was not found.")
        print()
        print("When the key is available, create a .env file containing:")
        print("BRAVE_SEARCH_API_KEY=your_real_key_here")
        print()
        print("No Python code will need to be changed.")
        print("=" * 60)

        return statistics

    active_queries = queries or DISCOVERY_QUERIES

    print("=" * 60)
    print("Opportunity Radar - Autonomous Source Discovery")
    print("=" * 60)
    print(f"Queries scheduled: {len(active_queries)}")
    print(f"Results per query: {results_per_query}")
    print(f"Minimum source confidence: {minimum_confidence}")
    print("=" * 60)

    for query_number, query in enumerate(active_queries, start=1):
        print()
        print(
            f"[{query_number}/{len(active_queries)}] "
            f"Searching: {query}"
        )

        try:
            results = search_brave(
                query=query,
                count=results_per_query,
            )
        except BraveSearchError as exc:
            statistics.failed_queries += 1

            print(f"Search failed: {exc}")

            continue

        statistics.queries_processed += 1
        statistics.results_received += len(results)

        print(f"Results received: {len(results)}")

        for result in results:
            status, score = process_search_result(
                result=result,
                query=query,
                minimum_confidence=minimum_confidence,
            )

            if status == "saved":
                statistics.sources_qualified += 1
                statistics.new_sources_saved += 1

                print(
                    f"  SAVED [{score:.0f}] "
                    f"{result.title[:80]}"
                )

            elif status == "duplicate":
                statistics.sources_qualified += 1
                statistics.duplicate_sources += 1

                print(
                    f"  EXISTS [{score:.0f}] "
                    f"{result.title[:80]}"
                )

            elif status == "rejected":
                statistics.rejected_sources += 1

            elif status == "ignored":
                statistics.rejected_sources += 1

        if query_number < len(active_queries):
            time.sleep(max(0, delay_between_requests))

    print()
    print("=" * 60)
    print("Source Discovery Completed")
    print("=" * 60)
    print(f"Queries processed:       {statistics.queries_processed}")
    print(f"Failed queries:          {statistics.failed_queries}")
    print(f"Search results received: {statistics.results_received}")
    print(f"Qualified sources:       {statistics.sources_qualified}")
    print(f"New sources saved:       {statistics.new_sources_saved}")
    print(f"Existing sources:        {statistics.duplicate_sources}")
    print(f"Rejected results:        {statistics.rejected_sources}")
    print("=" * 60)

    return statistics


if __name__ == "__main__":
    run_search_discovery()