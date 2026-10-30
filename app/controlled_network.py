"""Small dev-runner crawl envelope. Production scanner defaults are untouched."""

from ipaddress import ip_address
import socket
import time
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup
import requests

from app.scanners.company_websites import CompanyWebsiteScanner
from app.source_fetch_diagnostics import SourceFetchError


def display_url(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.hostname or "", parsed.path, "", ""))


class BoundedWeb:
    """No redirects/retries, one attempt per URL, capped response size and time."""

    def __init__(self, sources: int, pages: int) -> None:
        self.limits = {"SOURCE": sources, "PAGE": pages, "ROBOTS": sources}
        self.attempts = {stage: 0 for stage in self.limits}
        self.cache: dict[str, str | None] = {}
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers["User-Agent"] = CompanyWebsiteScanner.USER_AGENT

    @staticmethod
    def validate_url(url: str) -> None:
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.port not in {None, 80, 443}
        ):
            raise SourceFetchError("BLOCKED")
        addresses = socket.getaddrinfo(
            parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)
        )
        if not addresses or any(
            not ip_address(item[4][0]).is_global for item in addresses
        ):
            raise SourceFetchError("BLOCKED")

    def fetch(self, url: str, stage: str) -> str:
        if url in self.cache:
            cached = self.cache[url]
            if cached is None:
                raise requests.RequestException("Earlier fetch failed; no retry")
            return cached
        if self.attempts[stage] >= self.limits[stage]:
            raise requests.RequestException("Controlled HTTP budget exhausted")
        self.attempts[stage] += 1
        self.cache[url] = None
        self.validate_url(url)
        started = time.monotonic()
        with self.session.get(
            url, timeout=(5, 15), allow_redirects=False, stream=True
        ) as response:
            if stage == "ROBOTS" and response.status_code == 404:
                self.cache[url] = ""
                return ""
            if response.status_code != 200:
                raise SourceFetchError(
                    "BLOCKED" if response.status_code in {401, 403, 429, 451} else "HTTP_ERROR",
                    response.status_code,
                )
            if not any(
                content_type in response.headers.get("Content-Type", "").lower()
                for content_type in ("text/html", "application/xhtml+xml", "text/plain")
            ):
                raise SourceFetchError("UNSUPPORTED_CONTENT", response.status_code)
            content = bytearray()
            for chunk in response.iter_content(16_384):
                content.extend(chunk)
                if len(content) > 2_000_000 or time.monotonic() - started > 30:
                    raise requests.RequestException(
                        "Page exceeds development size/time limit"
                    )
            text = content.decode(response.encoding or "utf-8", errors="replace")
        self.cache[url] = text
        return text

    def source_text(self, url: str) -> str:
        soup = BeautifulSoup(self.fetch(url, "SOURCE"), "html.parser")
        for element in soup(("script", "style", "noscript", "template")):
            element.decompose()
        return soup.get_text(" ", strip=True)[:50_000]


class BoundedCrawler(CompanyWebsiteScanner):
    def __init__(self, web: BoundedWeb, page_limit: int) -> None:
        super().__init__()
        self.web = web
        self.page_limit = page_limit
        self.considered: set[str] = set()
        self.fetched: set[str] = set()
        self.MAX_PAGES_PER_SOURCE = page_limit
        self.MAX_CRAWL_DEPTH = 1

    def _can_fetch(self, url: str) -> bool:
        if url in self.considered or len(self.considered) >= self.page_limit:
            return False
        self.considered.add(url)
        if self._is_document_url(url):
            return False  # No synthetic PDF candidates: only actually fetched pages.
        parts = urlsplit(url)
        robots_url = urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))
        parser = self.robot_parsers.get(robots_url)
        if parser is None:
            parser = RobotFileParser()
            try:
                parser.parse(self.web.fetch(robots_url, "ROBOTS").splitlines())
            except requests.RequestException:
                parser.parse(["User-agent: *", "Disallow: /"])
            self.robot_parsers[robots_url] = parser
        return parser.can_fetch(self.USER_AGENT, url)

    def _get_soup(self, url: str) -> BeautifulSoup:
        soup = BeautifulSoup(self.web.fetch(url, "PAGE"), "html.parser")
        self.fetched.add(url)
        return soup

    def _looks_like_opportunity(self, text: str) -> bool:
        # In this tiny sample, every fetched textual page can reach Gemini's gate.
        # No keyword classifier is used to accept an opportunity.
        return bool(text.strip())
