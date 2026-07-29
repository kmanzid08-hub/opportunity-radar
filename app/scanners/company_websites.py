from __future__ import annotations

import re
import time
from datetime import date, datetime, timedelta, timezone
from urllib import robotparser
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup, Tag
from sqlalchemy import select

from app.database import SessionLocal
from app.models import Source
from app.schemas import RawOpportunity
from app.scanners.base import BaseScanner


class CompanyWebsiteScanner(BaseScanner):
    """
    Crawl approved organisation websites and return possible opportunities.

    Scheduling behaviour:
    - each active approved source is scanned at most once every 24 hours;
    - sources never scanned before are scanned immediately;
    - high-priority sources are scanned first;
    - source health is recorded after every scan;
    - failing sources are automatically disabled after ten failures.
    """

    source_name = "Company Websites"

    REQUEST_TIMEOUT = 20
    REQUEST_DELAY_SECONDS = 0.5

    MAX_SOURCES_PER_SCAN = 500
    MAX_PAGES_PER_SOURCE = 30
    MAX_CRAWL_DEPTH = 2

    FAILURE_DISABLE_THRESHOLD = 10
    DEFAULT_SCAN_INTERVAL_HOURS = 24

    MIN_PAGE_TEXT_LENGTH = 80
    MAX_DESCRIPTION_LENGTH = 20_000

    USER_AGENT = (
        "OpportunityRadar/1.0 "
        "(public opportunity monitoring; UT CPA Ltd)"
    )

    DOCUMENT_EXTENSIONS = (
        ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"
    )

    PAGE_LINK_TERMS = (
        "tender", "tenders", "procurement", "bid", "bids", "bidding",
        "opportunity", "opportunities", "consultancy", "consultancies",
        "consultant", "consulting", "proposal", "proposals",
        "expression of interest", "expressions of interest",
        "request for proposal", "request for proposals", "rfp", "eoi",
        "grant", "grants", "call for applications", "call for proposals",
        "announcement", "announcements", "notice", "notices",
        "supplier", "suppliers", "vendor", "vendors", "prequalification",
    )

    OPPORTUNITY_TERMS = (
        "request for proposal", "request for proposals",
        "request for quotation", "request for quotations",
        "request for expressions of interest",
        "request for expression of interest", "expression of interest",
        "expressions of interest", "invitation to bid",
        "invitation for bids", "invitation to tender", "tender notice",
        "procurement notice", "terms of reference", "consultancy services",
        "consulting services", "call for proposals", "call for applications",
        "call for consultants", "framework agreement", "submission deadline",
        "application deadline", "closing date", "applications are invited",
        "bids are invited", "eligible bidders", "interested consultants",
        "interested firms", "interested companies", "supplier registration",
        "prequalification",
    )

    EXCLUDED_LINK_TERMS = (
        "login", "sign in", "sign-in", "register account", "privacy",
        "cookie", "terms and conditions", "facebook", "twitter", "linkedin",
        "instagram", "youtube", "whatsapp", "mailto:", "tel:", "javascript:",
        "career", "careers", "vacancy", "vacancies", "job", "jobs",
    )

    DEADLINE_PATTERNS = (
        re.compile(
            r"\b(?:submission|application|bid|proposal)?\s*deadline\s*:?\s*"
            r"(\d{1,2}[-/.]\d{1,2}[-/.]\d{4})", re.IGNORECASE
        ),
        re.compile(
            r"\b(?:submission|application|bid|proposal)?\s*deadline\s*:?\s*"
            r"(\d{4}[-/.]\d{1,2}[-/.]\d{1,2})", re.IGNORECASE
        ),
        re.compile(
            r"\b(?:submission|application|bid|proposal)?\s*deadline\s*:?\s*"
            r"(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s+\d{4})",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bclosing date\s*:?\s*(\d{1,2}[-/.]\d{1,2}[-/.]\d{4})",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bclosing date\s*:?\s*"
            r"(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s+\d{4})",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bapplications? close(?:s|d)?\s*:?\s*"
            r"(\d{1,2}[-/.]\d{1,2}[-/.]\d{4})", re.IGNORECASE
        ),
        re.compile(
            r"\bapplications? close(?:s|d)?\s*:?\s*"
            r"(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s+\d{4})",
            re.IGNORECASE,
        ),
    )

    DATE_FORMATS = (
        "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d",
        "%Y/%m/%d", "%Y.%m.%d", "%d %B %Y", "%d %b %Y",
        "%B %d %Y", "%b %d %Y",
    )

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": self.USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-GB,en;q=0.9",
                "Connection": "keep-alive",
            }
        )
        self.robot_parsers: dict[str, robotparser.RobotFileParser] = {}

    def scan(self) -> list[RawOpportunity]:
        sources = self._get_sources()
        print(f"Found {len(sources)} organisation websites due for daily scanning")

        opportunities: dict[str, RawOpportunity] = {}

        for source_number, source in enumerate(sources, start=1):
            organisation_name = source.organisation_name or source.domain
            print(
                f"\nScanning organisation website {source_number}/{len(sources)}: "
                f"{organisation_name}"
            )

            started_at = time.perf_counter()
            self._record_scan_started(source.id)

            try:
                source_opportunities = self._scan_source(source)
                elapsed = time.perf_counter() - started_at
                self._record_scan_success(
                    source_id=source.id,
                    duration_seconds=elapsed,
                    opportunities_found=len(source_opportunities),
                )
            except Exception as exc:
                elapsed = time.perf_counter() - started_at
                status_code = getattr(getattr(exc, "response", None), "status_code", None)
                self._record_scan_failure(
                    source_id=source.id,
                    duration_seconds=elapsed,
                    http_status=status_code,
                    error_message=f"{type(exc).__name__}: {exc}",
                )
                print(f"  Website scan failed: {type(exc).__name__}: {exc}")
                continue

            for opportunity in source_opportunities:
                opportunities[opportunity.source_url] = opportunity

            print(f"  Possible listings collected: {len(source_opportunities)}")

        print(
            f"\nCompany website scan completed: "
            f"{len(opportunities)} unique possible listings"
        )
        return list(opportunities.values())

    def _get_sources(self) -> list[Source]:
        now = datetime.now(timezone.utc)

        with SessionLocal() as db:
            active_sources = db.scalars(
                select(Source).where(
                    Source.is_active.is_(True),
                    Source.is_auto_disabled.is_(False),
                )
            ).all()

            changed = False
            due_source_ids: list[int] = []

            for source in active_sources:
                if not source.is_approved:
                    source.is_approved = True
                    changed = True

                if not source.scan_interval_hours or source.scan_interval_hours < 1:
                    source.scan_interval_hours = self.DEFAULT_SCAN_INTERVAL_HOURS
                    changed = True

                if source.priority_score is None:
                    source.priority_score = self._calculate_priority(source)
                    changed = True

                if self._source_is_due(source, now):
                    due_source_ids.append(source.id)

            if changed:
                db.commit()

            if not due_source_ids:
                return []

            sources = db.scalars(
                select(Source)
                .where(
                    Source.id.in_(due_source_ids),
                    Source.is_active.is_(True),
                    Source.is_approved.is_(True),
                    Source.is_auto_disabled.is_(False),
                )
                .order_by(
                    Source.priority_score.desc(),
                    Source.confidence_score.desc(),
                    Source.organisation_name.asc(),
                )
                .limit(self.MAX_SOURCES_PER_SCAN)
            ).all()

            return list(sources)

    def _source_is_due(self, source: Source, now: datetime) -> bool:
        if source.last_scanned_at is None:
            return True

        last_scanned_at = source.last_scanned_at
        if last_scanned_at.tzinfo is None:
            last_scanned_at = last_scanned_at.replace(tzinfo=timezone.utc)

        interval_hours = int(
            source.scan_interval_hours or self.DEFAULT_SCAN_INTERVAL_HOURS
        )
        return now >= last_scanned_at + timedelta(hours=interval_hours)

    def _scan_source(self, source: Source) -> list[RawOpportunity]:
        start_url = self._normalise_url(source.monitor_url)
        if not start_url:
            return []

        base_domain = self._normalise_domain(source.domain or urlparse(start_url).netloc)
        if not base_domain:
            return []

        queue: list[tuple[str, int]] = [(start_url, 0)]
        base_url = self._normalise_url(source.base_url)
        if base_url and base_url != start_url:
            queue.append((base_url, 0))

        visited: set[str] = set()
        possible_opportunities: dict[str, RawOpportunity] = {}

        while queue and len(visited) < self.MAX_PAGES_PER_SOURCE:
            current_url, depth = queue.pop(0)
            current_url = self._normalise_url(current_url)

            if not current_url or current_url in visited:
                continue
            if not self._is_same_domain(current_url, base_domain):
                continue
            if not self._can_fetch(current_url):
                print(f"  Skipped by robots.txt: {current_url}")
                continue

            visited.add(current_url)

            if self._is_document_url(current_url):
                document_opportunity = self._build_document_opportunity(
                    url=current_url,
                    organisation_name=source.organisation_name,
                    source_name=source.organisation_name or source.domain,
                )
                if document_opportunity is not None:
                    possible_opportunities[current_url] = document_opportunity
                continue

            try:
                soup = self._get_soup(current_url)
            except requests.RequestException as exc:
                print(f"  Could not load page: {current_url}")
                print(f"    {type(exc).__name__}: {exc}")
                continue

            page_opportunity = self._build_page_opportunity(
                soup=soup,
                page_url=current_url,
                organisation_name=source.organisation_name,
                source_name=source.organisation_name or source.domain,
            )
            if page_opportunity is not None:
                possible_opportunities[current_url] = page_opportunity

            if depth < self.MAX_CRAWL_DEPTH:
                for discovered_url in self._extract_relevant_links(
                    soup=soup,
                    page_url=current_url,
                    base_domain=base_domain,
                ):
                    if discovered_url not in visited:
                        queue.append((discovered_url, depth + 1))

            self._wait_between_requests()

        print(f"  Pages visited: {len(visited)}")
        return list(possible_opportunities.values())

    def _build_page_opportunity(self, soup: BeautifulSoup, page_url: str,
                                organisation_name: str | None,
                                source_name: str) -> RawOpportunity | None:
        title = self._extract_title(soup)
        description = self._extract_description(soup)
        combined_text = self._clean_text(f"{title} {description}")

        if not self._looks_like_opportunity(combined_text):
            return None
        if not title:
            title = self._title_from_url(page_url)
        if not title:
            return None

        return RawOpportunity(
            organisation_name=organisation_name or source_name,
            title=title[:500],
            source_name=source_name[:100],
            source_url=page_url,
            description=(description[: self.MAX_DESCRIPTION_LENGTH] if description else title),
            deadline=self._extract_deadline(combined_text),
        )

    def _build_document_opportunity(self, url: str,
                                    organisation_name: str | None,
                                    source_name: str) -> RawOpportunity | None:
        title = self._title_from_url(url)
        if not title:
            return None

        combined_text = self._clean_text(f"{title} {url}")
        if not self._looks_like_opportunity(combined_text):
            return None

        return RawOpportunity(
            organisation_name=organisation_name or source_name,
            title=title[:500],
            source_name=source_name[:100],
            source_url=url,
            description=f"Opportunity document discovered on {source_name}: {title}",
            deadline=self._extract_deadline(combined_text),
        )

    def _extract_relevant_links(self, soup: BeautifulSoup, page_url: str,
                                base_domain: str) -> list[str]:
        scored_links: dict[str, int] = {}

        for anchor in soup.find_all("a", href=True):
            if not isinstance(anchor, Tag):
                continue

            raw_href = self._clean_text(anchor.get("href"))
            link_text = self._clean_text(anchor.get_text(" ", strip=True))
            if not raw_href:
                continue

            absolute_url = self._normalise_url(urljoin(page_url, raw_href))
            if not absolute_url or not self._is_same_domain(absolute_url, base_domain):
                continue

            link_information = self._clean_text(f"{link_text} {absolute_url}").lower()
            if any(term in link_information for term in self.EXCLUDED_LINK_TERMS):
                continue

            score = self._link_relevance_score(link_information)
            if self._is_document_url(absolute_url):
                if score < 1:
                    continue
                score += 5
            elif score < 1:
                continue

            previous_score = scored_links.get(absolute_url)
            if previous_score is None or score > previous_score:
                scored_links[absolute_url] = score

        ordered_links = sorted(scored_links.items(), key=lambda item: item[1], reverse=True)
        return [url for url, _score in ordered_links]

    def _link_relevance_score(self, link_information: str) -> int:
        score = sum(1 for term in self.PAGE_LINK_TERMS if term in link_information)
        for term in (
            "request for proposal", "expression of interest", "terms of reference",
            "procurement", "tender", "consultancy",
        ):
            if term in link_information:
                score += 3
        return score

    def _looks_like_opportunity(self, text: str) -> bool:
        lowered_text = text.lower()
        if len(lowered_text) < self.MIN_PAGE_TEXT_LENGTH:
            return False
        return any(term in lowered_text for term in self.OPPORTUNITY_TERMS)

    def _extract_title(self, soup: BeautifulSoup) -> str:
        for selector in (
            "main h1", "article h1", ".page-title", ".entry-title",
            ".post-title", ".tender-title", "h1", "h2",
        ):
            element = soup.select_one(selector)
            if element is not None:
                title = self._clean_text(element.get_text(" ", strip=True))
                if title:
                    return title

        og_title = soup.select_one('meta[property="og:title"]')
        if og_title is not None:
            title = self._clean_text(og_title.get("content"))
            if title:
                return title

        if soup.title is not None:
            return self._clean_text(soup.title.get_text(" ", strip=True))
        return ""

    def _extract_description(self, soup: BeautifulSoup) -> str:
        for selector in (
            "article", "main", ".entry-content", ".post-content",
            ".page-content", ".tender-description", ".content", "body",
        ):
            element = soup.select_one(selector)
            if element is None:
                continue

            copied = BeautifulSoup(str(element), "html.parser")
            for unwanted in copied.select(
                "script, style, nav, form, footer, header, aside, noscript, iframe, "
                ".menu, .navigation, .breadcrumb, .social-share, .share, .sidebar"
            ):
                unwanted.decompose()

            description = self._clean_text(copied.get_text(" ", strip=True))
            if len(description) >= self.MIN_PAGE_TEXT_LENGTH:
                return description

        meta_description = soup.select_one('meta[name="description"]')
        if meta_description is not None:
            return self._clean_text(meta_description.get("content"))
        return ""

    def _extract_deadline(self, text: str) -> date | None:
        cleaned_text = self._clean_text(text)
        for pattern in self.DEADLINE_PATTERNS:
            match = pattern.search(cleaned_text)
            if match:
                parsed_date = self._parse_date(match.group(1))
                if parsed_date is not None:
                    return parsed_date
        return None

    def _parse_date(self, value: str) -> date | None:
        cleaned_value = self._clean_text(value)
        cleaned_value = re.sub(
            r"(\d)(st|nd|rd|th)\b", r"\1", cleaned_value, flags=re.IGNORECASE
        ).replace(",", "")

        for date_format in self.DATE_FORMATS:
            try:
                return datetime.strptime(cleaned_value, date_format).date()
            except ValueError:
                continue
        return None

    def _get_soup(self, url: str) -> BeautifulSoup:
        response = self.session.get(url, timeout=self.REQUEST_TIMEOUT)
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "").lower()
        if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
            raise requests.RequestException("URL did not return HTML")
        return BeautifulSoup(response.text, "html.parser")

    def _can_fetch(self, url: str) -> bool:
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        parser = self.robot_parsers.get(robots_url)

        if parser is None:
            parser = robotparser.RobotFileParser()
            parser.set_url(robots_url)
            try:
                parser.read()
            except Exception:
                parser = robotparser.RobotFileParser()
                parser.parse([])
            self.robot_parsers[robots_url] = parser

        try:
            return parser.can_fetch(self.USER_AGENT, url)
        except Exception:
            return True

    def _record_scan_started(self, source_id: int) -> None:
        now = datetime.now(timezone.utc)
        with SessionLocal() as db:
            source = db.get(Source, source_id)
            if source is None:
                return
            source.last_scan_started_at = now
            source.last_scanned_at = now
            db.commit()

    def _record_scan_success(self, *, source_id: int, duration_seconds: float,
                             opportunities_found: int) -> None:
        now = datetime.now(timezone.utc)
        with SessionLocal() as db:
            source = db.get(Source, source_id)
            if source is None:
                return

            source.last_scanned_at = now
            source.last_successful_scan_at = now
            source.last_scan_duration_seconds = round(duration_seconds, 3)
            source.last_http_status = 200
            source.consecutive_failures = 0
            source.is_auto_disabled = False
            source.disabled_reason = None

            if opportunities_found > 0:
                source.total_opportunities_found = int(source.total_opportunities_found or 0) + opportunities_found
                source.last_opportunity_found_at = now

            source.priority_score = self._calculate_priority(source)
            db.commit()

    def _record_scan_failure(self, *, source_id: int, duration_seconds: float,
                             http_status: int | None, error_message: str) -> None:
        now = datetime.now(timezone.utc)
        with SessionLocal() as db:
            source = db.get(Source, source_id)
            if source is None:
                return

            source.last_scanned_at = now
            source.last_scan_duration_seconds = round(duration_seconds, 3)
            source.last_http_status = http_status
            source.consecutive_failures = int(source.consecutive_failures or 0) + 1

            if source.consecutive_failures >= self.FAILURE_DISABLE_THRESHOLD:
                source.is_active = False
                source.is_auto_disabled = True
                source.disabled_reason = (
                    "Automatically disabled after "
                    f"{source.consecutive_failures} consecutive scan failures. "
                    f"Latest error: {error_message[:300]}"
                )

            source.priority_score = self._calculate_priority(source)
            db.commit()

    def _calculate_priority(self, source: Source) -> float:
        score = float(source.confidence_score or 0) * 0.55
        score += float(source.url_relevance_score or 0) * 0.20
        score += min(int(source.total_opportunities_found or 0) * 2, 20)
        score -= min(int(source.consecutive_failures or 0) * 5, 35)
        if source.last_successful_scan_at:
            score += 5
        if source.is_auto_disabled:
            score = 0
        return max(0.0, min(round(score, 2), 100.0))

    def _is_document_url(self, url: str) -> bool:
        return urlparse(url).path.lower().endswith(self.DOCUMENT_EXTENSIONS)

    def _is_same_domain(self, url: str, expected_domain: str) -> bool:
        actual_domain = self._normalise_domain(urlparse(url).netloc)
        expected_domain = self._normalise_domain(expected_domain)
        if not actual_domain or not expected_domain:
            return False
        return (
            actual_domain == expected_domain
            or actual_domain.endswith(f".{expected_domain}")
            or expected_domain.endswith(f".{actual_domain}")
        )

    def _normalise_url(self, url: str | None) -> str:
        cleaned_url = self._clean_text(url)
        if not cleaned_url:
            return ""
        if cleaned_url.startswith("//"):
            cleaned_url = f"https:{cleaned_url}"
        if not cleaned_url.startswith(("http://", "https://")):
            cleaned_url = f"https://{cleaned_url}"

        parsed = urlparse(cleaned_url)
        if not parsed.netloc:
            return ""

        normalised_path = parsed.path.rstrip("/") or "/"
        return urlunparse(
            (
                parsed.scheme.lower(), parsed.netloc.lower(), normalised_path,
                "", parsed.query, "",
            )
        )

    def _normalise_domain(self, domain: str | None) -> str:
        cleaned_domain = self._clean_text(domain).lower()
        if not cleaned_domain:
            return ""
        if "://" in cleaned_domain:
            cleaned_domain = urlparse(cleaned_domain).netloc
        cleaned_domain = cleaned_domain.split(":")[0]
        if cleaned_domain.startswith("www."):
            cleaned_domain = cleaned_domain[4:]
        return cleaned_domain.rstrip(".")

    def _title_from_url(self, url: str) -> str:
        filename = urlparse(url).path.rstrip("/").split("/")[-1]
        if not filename:
            return ""
        for extension in self.DOCUMENT_EXTENSIONS:
            if filename.lower().endswith(extension):
                filename = filename[: -len(extension)]
                break
        filename = re.sub(r"[_\-]+", " ", filename)
        filename = re.sub(r"\s+", " ", filename)
        return filename.strip().title()

    def _wait_between_requests(self) -> None:
        if self.REQUEST_DELAY_SECONDS > 0:
            time.sleep(self.REQUEST_DELAY_SECONDS)

    @staticmethod
    def _clean_text(value: object) -> str:
        if value is None:
            return ""
        text = str(value).replace("\xa0", " ")
        return re.sub(r"\s+", " ", text).strip()
