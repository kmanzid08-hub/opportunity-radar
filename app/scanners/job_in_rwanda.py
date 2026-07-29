from __future__ import annotations

import json
import re
import time
from datetime import date, datetime
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag

from app.schemas import RawOpportunity
from app.scanners.base import BaseScanner


class JobInRwandaScanner(BaseScanner):
    """
    High-recall scanner for Job in Rwanda.

    The scanner deliberately collects broadly and leaves relevance
    classification to the filtering layer. This reduces the risk of missing
    audit, accounting, tax, recruitment, consultancy or other professional
    service opportunities.

    It scans:
    - Tender
    - Consultancy
    - All listings

    It also:
    - follows pagination;
    - opens individual listing pages;
    - extracts full descriptions;
    - extracts deadlines;
    - avoids duplicate URLs;
    - rejects obviously corrupted organisation names;
    - falls back safely when a detail page cannot be opened.
    """

    source_name = "Job in Rwanda"

    BASE_URL = "https://www.jobinrwanda.com"

    SECTION_URLS = (
        "https://www.jobinrwanda.com/jobs/tender",
        "https://www.jobinrwanda.com/jobs/consultancy",
        "https://www.jobinrwanda.com/jobs/all",
    )

    REQUEST_TIMEOUT = 30
    MAX_PAGES_PER_SECTION = 5
    REQUEST_DELAY_SECONDS = 0.25

    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/150.0 Safari/537.36 "
        "OpportunityRadar/1.0"
    )

    LISTING_PATH_PATTERN = re.compile(
        r"^/job/[a-zA-Z0-9][a-zA-Z0-9\-_/]*$",
        re.IGNORECASE,
    )

    DEADLINE_PATTERNS = (
        re.compile(
            r"\bdeadline\s*:?\s*"
            r"(\d{1,2}[-/.]\d{1,2}[-/.]\d{4})",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bdeadline\s*:?\s*"
            r"(\d{4}[-/.]\d{1,2}[-/.]\d{1,2})",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bdeadline\s*:?\s*"
            r"(\d{1,2}\s+[A-Za-z]+\s+\d{4})",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bclosing date\s*:?\s*"
            r"(\d{1,2}[-/.]\d{1,2}[-/.]\d{4})",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bclosing date\s*:?\s*"
            r"(\d{1,2}\s+[A-Za-z]+\s+\d{4})",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bsubmission deadline\s*:?\s*"
            r"(\d{1,2}[-/.]\d{1,2}[-/.]\d{4})",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bsubmission deadline\s*:?\s*"
            r"(\d{1,2}\s+[A-Za-z]+\s+\d{4})",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bapplications? close(?:s|d)?\s*:?\s*"
            r"(\d{1,2}[-/.]\d{1,2}[-/.]\d{4})",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bapplications? close(?:s|d)?\s*:?\s*"
            r"(\d{1,2}\s+[A-Za-z]+\s+\d{4})",
            re.IGNORECASE,
        ),
    )

    DATE_FORMATS = (
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%d.%m.%Y",
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y.%m.%d",
        "%d %B %Y",
        "%d %b %Y",
        "%B %d %Y",
        "%b %d %Y",
    )

    INVALID_ORGANISATION_PHRASES = (
        "testimonials",
        "help about us",
        "contact us",
        "job in rwanda",
        "request for proposal title",
        "job advertisement",
        "news testimonials",
        "home jobs",
        "latest jobs",
        "submit a job",
        "career advice",
        "privacy policy",
        "terms and conditions",
        "all rights reserved",
    )

    ORGANISATION_FIELD_LABELS = (
        "company",
        "employer",
        "organisation",
        "organization",
        "hiring organisation",
        "hiring organization",
        "client",
    )

    def __init__(self) -> None:
        self.session = requests.Session()

        self.session.headers.update(
            {
                "User-Agent": self.USER_AGENT,
                "Accept": (
                    "text/html,application/xhtml+xml,"
                    "application/xml;q=0.9,*/*;q=0.8"
                ),
                "Accept-Language": "en-GB,en;q=0.9",
                "Connection": "keep-alive",
            }
        )

    def scan(self) -> list[RawOpportunity]:
        """
        Scan all configured Job in Rwanda sections.

        Returns:
            Deduplicated raw opportunities.
        """
        print(f"Scanning {self.source_name}...")

        discovered_listings: dict[str, dict[str, str]] = {}

        for section_url in self.SECTION_URLS:
            section_name = self._section_name(section_url)

            print(f"  Scanning section: {section_name}")

            section_results = self._discover_section_listings(
                section_url=section_url,
            )

            print(
                f"  Found {len(section_results)} unique listing links "
                f"in {section_name}"
            )

            for listing_url, preview in section_results.items():
                existing = discovered_listings.get(listing_url)

                if existing is None:
                    discovered_listings[listing_url] = preview
                    continue

                discovered_listings[listing_url] = (
                    self._choose_richer_preview(
                        first=existing,
                        second=preview,
                    )
                )

        print(
            f"Found {len(discovered_listings)} unique Job in Rwanda "
            "listing URLs across all sections"
        )

        opportunities: list[RawOpportunity] = []
        total = len(discovered_listings)

        for number, (listing_url, preview) in enumerate(
            discovered_listings.items(),
            start=1,
        ):
            display_title = (
                preview.get("title")
                or listing_url
            )

            print(
                f"  Reading listing {number}/{total}: "
                f"{display_title[:80]}"
            )

            opportunity = self._build_opportunity(
                listing_url=listing_url,
                preview=preview,
            )

            if opportunity is not None:
                opportunities.append(opportunity)

            self._wait_between_requests()

        print(
            f"Completed {self.source_name}: "
            f"{len(opportunities)} opportunities extracted"
        )

        return opportunities

    def _discover_section_listings(
        self,
        section_url: str,
    ) -> dict[str, dict[str, str]]:
        """
        Visit several pages in one section and collect listing links.
        """
        discovered: dict[str, dict[str, str]] = {}

        for page_number in range(self.MAX_PAGES_PER_SECTION):
            page_url = self._build_page_url(
                section_url=section_url,
                page_number=page_number,
            )

            try:
                soup = self._get_soup(page_url)
            except requests.RequestException as exc:
                print(f"    Could not load {page_url}: {exc}")
                break

            page_results = self._extract_listing_links(soup)

            new_links = 0

            for listing_url, preview in page_results.items():
                existing = discovered.get(listing_url)

                if existing is None:
                    discovered[listing_url] = preview
                    new_links += 1
                    continue

                discovered[listing_url] = (
                    self._choose_richer_preview(
                        first=existing,
                        second=preview,
                    )
                )

            if not page_results:
                break

            if page_number > 0 and new_links == 0:
                break

            self._wait_between_requests()

        return discovered

    def _build_page_url(
        self,
        section_url: str,
        page_number: int,
    ) -> str:
        """
        Construct a paginated Job in Rwanda URL.
        """
        if page_number == 0:
            return section_url

        separator = "&" if "?" in section_url else "?"

        return f"{section_url}{separator}page={page_number}"

    def _extract_listing_links(
        self,
        soup: BeautifulSoup,
    ) -> dict[str, dict[str, str]]:
        """
        Extract every valid Job in Rwanda /job/... listing link.
        """
        results: dict[str, dict[str, str]] = {}

        for anchor in soup.find_all("a", href=True):
            href = self._clean_text(anchor.get("href"))

            if not self._is_listing_href(href):
                continue

            listing_url = self._normalise_listing_url(href)

            if not listing_url:
                continue

            title = self._clean_text(
                anchor.get_text(" ", strip=True)
            )

            container = self._find_listing_container(anchor)

            card_text = ""

            if container is not None:
                card_text = self._clean_text(
                    container.get_text(" ", strip=True)
                )

            if not title and container is not None:
                heading = container.find(
                    ["h1", "h2", "h3", "h4", "h5", "strong"]
                )

                if heading is not None:
                    title = self._clean_text(
                        heading.get_text(" ", strip=True)
                    )

            if not title:
                title = self._title_from_url(listing_url)

            organisation_name = (
                self._extract_organisation_from_card(
                    container=container,
                    title=title,
                )
            )

            preview = {
                "title": title,
                "card_text": card_text,
                "organisation_name": organisation_name,
            }

            existing = results.get(listing_url)

            if existing is None:
                results[listing_url] = preview
            else:
                results[listing_url] = (
                    self._choose_richer_preview(
                        first=existing,
                        second=preview,
                    )
                )

        return results

    def _choose_richer_preview(
        self,
        first: dict[str, str],
        second: dict[str, str],
    ) -> dict[str, str]:
        """
        Keep the preview containing the most useful information.
        """
        first_score = (
            len(first.get("title", ""))
            + len(first.get("card_text", ""))
            + len(first.get("organisation_name", ""))
        )

        second_score = (
            len(second.get("title", ""))
            + len(second.get("card_text", ""))
            + len(second.get("organisation_name", ""))
        )

        if second_score > first_score:
            return second

        return first

    def _is_listing_href(self, href: str) -> bool:
        """
        Return True only for valid Job in Rwanda listing pages.
        """
        if not href:
            return False

        absolute_url = urljoin(self.BASE_URL, href)
        parsed = urlparse(absolute_url)

        domain = parsed.netloc.lower()

        if domain.startswith("www."):
            domain = domain[4:]

        if domain != "jobinrwanda.com":
            return False

        path = parsed.path.rstrip("/")

        if not self.LISTING_PATH_PATTERN.match(path):
            return False

        excluded_paths = {
            "/job",
            "/jobs",
            "/job-seeker",
            "/job-alert",
        }

        return path.lower() not in excluded_paths

    def _normalise_listing_url(self, href: str) -> str:
        """
        Remove query strings and fragments from a listing URL.
        """
        absolute_url = urljoin(self.BASE_URL, href)
        parsed = urlparse(absolute_url)

        path = parsed.path.rstrip("/")

        if not path:
            return ""

        return f"{self.BASE_URL}{path}"

    def _find_listing_container(
        self,
        anchor: Tag,
    ) -> Tag | None:
        """
        Locate the nearest likely listing card or row.
        """
        preferred_classes = (
            "job",
            "tender",
            "consultancy",
            "listing",
            "views-row",
            "card",
            "item",
            "result",
        )

        current: Tag | None = anchor

        for _ in range(7):
            parent = current.parent

            if not isinstance(parent, Tag):
                break

            class_text = " ".join(
                parent.get("class", [])
            ).lower()

            if any(
                preferred_class in class_text
                for preferred_class in preferred_classes
            ):
                return parent

            if parent.name in {"article", "li"}:
                return parent

            current = parent

        for parent_name in ("article", "li", "div"):
            parent = anchor.find_parent(parent_name)

            if isinstance(parent, Tag):
                return parent

        return None

    def _build_opportunity(
        self,
        listing_url: str,
        preview: dict[str, str],
    ) -> RawOpportunity | None:
        """
        Open an individual listing and convert it into RawOpportunity.
        """
        try:
            soup = self._get_soup(listing_url)
        except requests.RequestException as exc:
            print(
                "    Detail page failed; using listing preview: "
                f"{exc}"
            )

            return self._build_from_preview(
                listing_url=listing_url,
                preview=preview,
            )

        structured_data = self._extract_structured_data(soup)

        title = self._first_valid_text(
            self._extract_detail_title(soup),
            structured_data.get("title"),
            preview.get("title"),
            self._title_from_url(listing_url),
        )

        detail_organisation = (
            self._extract_detail_organisation(soup)
        )

        structured_organisation = self._clean_text(
            structured_data.get("organisation_name")
        )

        preview_organisation = self._clean_text(
            preview.get("organisation_name")
        )

        organisation_name = (
            self._choose_organisation_name(
                detail_organisation=detail_organisation,
                structured_organisation=structured_organisation,
                preview_organisation=preview_organisation,
                title=title,
                soup=soup,
            )
        )

        description = self._first_valid_text(
            self._extract_detail_description(soup),
            structured_data.get("description"),
            preview.get("card_text"),
            title,
        )

        page_text = self._clean_text(
            soup.get_text(" ", strip=True)
        )

        deadline = (
            self._extract_deadline_from_structured_data(
                structured_data
            )
            or self._extract_deadline(page_text)
            or self._extract_deadline(
                preview.get("card_text", "")
            )
        )

        if not title:
            return None

        return RawOpportunity(
            organisation_name=organisation_name[:255],
            title=title[:500],
            description=description,
            deadline=deadline,
            source_name=self.source_name,
            source_url=listing_url,
        )

    def _choose_organisation_name(
        self,
        detail_organisation: str,
        structured_organisation: str,
        preview_organisation: str,
        title: str,
        soup: BeautifulSoup,
    ) -> str:
        """
        Select the best valid organisation name.

        Corrupted page-navigation text is discarded.
        """
        candidates = (
            detail_organisation,
            structured_organisation,
            preview_organisation,
        )

        for candidate in candidates:
            cleaned_candidate = self._clean_text(candidate)

            if self._is_valid_organisation_name(
                cleaned_candidate,
                title=title,
            ):
                return cleaned_candidate

        inferred = self._infer_organisation_from_page(
            soup=soup,
            title=title,
        )

        if self._is_valid_organisation_name(
            inferred,
            title=title,
        ):
            return inferred

        title_inferred = self._infer_organisation_from_title(
            title
        )

        if self._is_valid_organisation_name(
            title_inferred,
            title=title,
        ):
            return title_inferred

        return "Not specified"

    def _build_from_preview(
        self,
        listing_url: str,
        preview: dict[str, str],
    ) -> RawOpportunity | None:
        """
        Create an opportunity using listing-page information only.
        """
        title = self._clean_text(
            preview.get("title")
        )

        if not title:
            return None

        description = self._clean_text(
            preview.get("card_text")
        )

        organisation_name = self._clean_text(
            preview.get("organisation_name")
        )

        if not self._is_valid_organisation_name(
            organisation_name,
            title=title,
        ):
            organisation_name = (
                self._infer_organisation_from_title(title)
            )

        if not self._is_valid_organisation_name(
            organisation_name,
            title=title,
        ):
            organisation_name = "Not specified"

        return RawOpportunity(
            organisation_name=organisation_name[:255],
            title=title[:500],
            description=description or title,
            deadline=self._extract_deadline(description),
            source_name=self.source_name,
            source_url=listing_url,
        )

    def _get_soup(self, url: str) -> BeautifulSoup:
        """
        Download one HTML page and return BeautifulSoup.
        """
        response = self.session.get(
            url,
            timeout=self.REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        return BeautifulSoup(
            response.text,
            "html.parser",
        )

    def _extract_detail_title(
        self,
        soup: BeautifulSoup,
    ) -> str:
        """
        Extract the listing title.
        """
        selectors = (
            "h1.page-header",
            "h1.title",
            ".field--name-title h1",
            ".field--name-title",
            ".job-title",
            "main h1",
            "article h1",
            "h1",
        )

        for selector in selectors:
            element = soup.select_one(selector)

            if element is None:
                continue

            title = self._clean_text(
                element.get_text(" ", strip=True)
            )

            if title:
                return title

        og_title = soup.select_one(
            'meta[property="og:title"]'
        )

        if og_title is not None:
            title = self._clean_text(
                og_title.get("content")
            )

            if title:
                return self._remove_site_name(title)

        if soup.title is not None:
            title = self._clean_text(
                soup.title.get_text(" ", strip=True)
            )

            return self._remove_site_name(title)

        return ""

    def _extract_detail_organisation(
        self,
        soup: BeautifulSoup,
    ) -> str:
        """
        Extract the organisation from explicit page fields only.

        Broad selectors such as the full article or page wrapper are avoided
        because they can return navigation text instead of an organisation.
        """
        selectors = (
            ".field--name-field-company a",
            ".field--name-field-company",
            ".field--name-field-employer a",
            ".field--name-field-employer",
            ".field--name-field-organization a",
            ".field--name-field-organization",
            ".field--name-field-organisation a",
            ".field--name-field-organisation",
            ".company-name",
            ".employer-name",
            ".organization-name",
            ".organisation-name",
            ".job-company",
            ".job-employer",
            "[itemprop='hiringOrganization'] [itemprop='name']",
            "[itemprop='hiringOrganization']",
        )

        for selector in selectors:
            elements = soup.select(selector)

            for element in elements:
                organisation = self._clean_text(
                    element.get_text(" ", strip=True)
                )

                organisation = self._remove_field_label(
                    organisation,
                    labels=self.ORGANISATION_FIELD_LABELS,
                )

                if self._is_valid_organisation_name(
                    organisation
                ):
                    return organisation

        labelled_organisation = (
            self._extract_labelled_organisation(soup)
        )

        if self._is_valid_organisation_name(
            labelled_organisation
        ):
            return labelled_organisation

        return ""

    def _extract_labelled_organisation(
        self,
        soup: BeautifulSoup,
    ) -> str:
        """
        Look for short labelled fields such as 'Employer: ATL Ltd'.
        """
        labels = soup.find_all(
            string=re.compile(
                r"^\s*"
                r"(company|employer|organisation|organization|client)"
                r"\s*:?\s*$",
                re.IGNORECASE,
            )
        )

        for label_text in labels:
            parent = label_text.parent

            if not isinstance(parent, Tag):
                continue

            sibling = parent.find_next_sibling()

            if isinstance(sibling, Tag):
                candidate = self._clean_text(
                    sibling.get_text(" ", strip=True)
                )

                if self._is_valid_organisation_name(candidate):
                    return candidate

            parent_text = self._clean_text(
                parent.get_text(" ", strip=True)
            )

            candidate = self._remove_field_label(
                parent_text,
                labels=self.ORGANISATION_FIELD_LABELS,
            )

            if self._is_valid_organisation_name(candidate):
                return candidate

        return ""

    def _extract_detail_description(
        self,
        soup: BeautifulSoup,
    ) -> str:
        """
        Extract the full listing description.
        """
        selectors = (
            ".field--name-body",
            ".field--name-field-job-description",
            ".job-description",
            ".job-content",
            ".description",
            "article .content",
            "article .field--type-text-with-summary",
            "article",
            "main",
        )

        for selector in selectors:
            element = soup.select_one(selector)

            if element is None:
                continue

            copied = BeautifulSoup(
                str(element),
                "html.parser",
            )

            for unwanted in copied.select(
                "script, style, nav, form, footer, aside, "
                ".breadcrumb, .social-share, .share, "
                ".related-jobs, .similar-jobs"
            ):
                unwanted.decompose()

            description = self._clean_text(
                copied.get_text(" ", strip=True)
            )

            if len(description) >= 80:
                return description

        meta_description = soup.select_one(
            'meta[name="description"]'
        )

        if meta_description is not None:
            description = self._clean_text(
                meta_description.get("content")
            )

            if description:
                return description

        return ""

    def _extract_structured_data(
        self,
        soup: BeautifulSoup,
    ) -> dict[str, Any]:
        """
        Read JSON-LD structured data when available.
        """
        result: dict[str, Any] = {}

        for script in soup.select(
            'script[type="application/ld+json"]'
        ):
            script_text = (
                script.string
                or script.get_text()
            )

            if not script_text:
                continue

            try:
                payload = json.loads(script_text)
            except (json.JSONDecodeError, TypeError):
                continue

            objects = self._flatten_json_ld(payload)

            for item in objects:
                item_type = item.get("@type", "")

                if isinstance(item_type, list):
                    item_types = {
                        str(value).lower()
                        for value in item_type
                    }
                else:
                    item_types = {
                        str(item_type).lower()
                    }

                supported_types = {
                    "jobposting",
                    "article",
                    "creativework",
                    "newsarticle",
                    "webpage",
                }

                if not item_types.intersection(
                    supported_types
                ):
                    continue

                if not result.get("title"):
                    result["title"] = (
                        item.get("title")
                        or item.get("headline")
                        or item.get("name")
                    )

                if not result.get("description"):
                    result["description"] = (
                        item.get("description")
                    )

                if not result.get("deadline"):
                    result["deadline"] = (
                        item.get("validThrough")
                        or item.get("expires")
                        or item.get("dateModified")
                    )

                if not result.get("organisation_name"):
                    organisation = (
                        item.get("hiringOrganization")
                        or item.get("publisher")
                        or item.get("author")
                    )

                    result["organisation_name"] = (
                        self._extract_name_from_json_value(
                            organisation
                        )
                    )

        return result

    def _extract_name_from_json_value(
        self,
        value: Any,
    ) -> str:
        """
        Extract a name from a JSON-LD object.
        """
        if isinstance(value, dict):
            return self._clean_text(
                value.get("name")
            )

        if isinstance(value, list):
            for item in value:
                name = self._extract_name_from_json_value(item)

                if name:
                    return name

        if isinstance(value, str):
            return self._clean_text(value)

        return ""

    def _flatten_json_ld(
        self,
        payload: Any,
    ) -> list[dict[str, Any]]:
        """
        Flatten JSON-LD lists and @graph structures.
        """
        objects: list[dict[str, Any]] = []

        if isinstance(payload, list):
            for item in payload:
                objects.extend(
                    self._flatten_json_ld(item)
                )

            return objects

        if not isinstance(payload, dict):
            return objects

        graph = payload.get("@graph")

        if isinstance(graph, list):
            for item in graph:
                objects.extend(
                    self._flatten_json_ld(item)
                )

        objects.append(payload)

        return objects

    def _extract_deadline_from_structured_data(
        self,
        structured_data: dict[str, Any],
    ) -> date | None:
        raw_deadline = structured_data.get("deadline")

        if raw_deadline is None:
            return None

        raw_text = self._clean_text(raw_deadline)

        if not raw_text:
            return None

        iso_candidate = raw_text[:10]

        try:
            return date.fromisoformat(iso_candidate)
        except ValueError:
            return self._parse_date(raw_text)

    def _extract_deadline(
        self,
        text: str,
    ) -> date | None:
        """
        Extract the first recognisable deadline from text.
        """
        cleaned_text = self._clean_text(text)

        if not cleaned_text:
            return None

        for pattern in self.DEADLINE_PATTERNS:
            match = pattern.search(cleaned_text)

            if not match:
                continue

            parsed_date = self._parse_date(
                match.group(1)
            )

            if parsed_date is not None:
                return parsed_date

        return None

    def _parse_date(
        self,
        value: str,
    ) -> date | None:
        """
        Parse common date formats used on Job in Rwanda.
        """
        cleaned_value = self._clean_text(value)

        cleaned_value = re.sub(
            r"(\d)(st|nd|rd|th)\b",
            r"\1",
            cleaned_value,
            flags=re.IGNORECASE,
        )

        cleaned_value = cleaned_value.replace(",", "")

        for date_format in self.DATE_FORMATS:
            try:
                return datetime.strptime(
                    cleaned_value,
                    date_format,
                ).date()
            except ValueError:
                continue

        return None

    def _extract_organisation_from_card(
        self,
        container: Tag | None,
        title: str,
    ) -> str:
        """
        Extract an organisation name from a listing card.
        """
        if container is None:
            return ""

        selectors = (
            ".company",
            ".company-name",
            ".employer",
            ".employer-name",
            ".organization",
            ".organization-name",
            ".organisation",
            ".organisation-name",
            ".field--name-field-company",
            ".field--name-field-employer",
            ".submitted",
        )

        for selector in selectors:
            elements = container.select(selector)

            for element in elements:
                organisation = self._clean_text(
                    element.get_text(" ", strip=True)
                )

                organisation = self._remove_field_label(
                    organisation,
                    labels=self.ORGANISATION_FIELD_LABELS,
                )

                if self._is_valid_organisation_name(
                    organisation,
                    title=title,
                ):
                    return organisation

        card_text = self._clean_text(
            container.get_text(" ", strip=True)
        )

        if title and card_text.lower().startswith(
            title.lower()
        ):
            remainder = card_text[len(title):].strip(
                " -|:"
            )

            organisation_match = re.match(
                r"(.{2,150}?)\s*\|\s*",
                remainder,
            )

            if organisation_match:
                organisation = self._clean_text(
                    organisation_match.group(1)
                )

                if self._is_valid_organisation_name(
                    organisation,
                    title=title,
                ):
                    return organisation

        return ""

    def _infer_organisation_from_page(
        self,
        soup: BeautifulSoup,
        title: str,
    ) -> str:
        """
        Attempt limited organisation inference from page metadata.
        """
        meta_selectors = (
            'meta[property="article:author"]',
            'meta[name="author"]',
            'meta[property="og:site_name"]',
        )

        for selector in meta_selectors:
            element = soup.select_one(selector)

            if element is None:
                continue

            candidate = self._clean_text(
                element.get("content")
            )

            if self._is_valid_organisation_name(
                candidate,
                title=title,
            ):
                return candidate

        return ""

    def _infer_organisation_from_title(
        self,
        title: str,
    ) -> str:
        """
        Infer an organisation only where the title explicitly names it.

        Examples:
        - Audit Services for ATL Group Consolidated Accounts
        - Provision of Services to Water for People Rwanda
        """
        cleaned_title = self._clean_text(title)

        patterns = (
            re.compile(
                r"\bfor\s+"
                r"([A-Z][A-Za-z0-9&'().,\-\s]{2,100}?)"
                r"(?:\s+group consolidated accounts|\s+in rwanda|$)",
            ),
            re.compile(
                r"\bto\s+"
                r"([A-Z][A-Za-z0-9&'().,\-\s]{2,100}?)"
                r"(?:\s+in rwanda|$)",
            ),
        )

        for pattern in patterns:
            match = pattern.search(cleaned_title)

            if not match:
                continue

            candidate = self._clean_text(
                match.group(1)
            )

            candidate = re.sub(
                r"\b(the|a|an)$",
                "",
                candidate,
                flags=re.IGNORECASE,
            ).strip()

            if self._is_valid_organisation_name(
                candidate,
                title=title,
            ):
                return candidate

        return ""

    def _is_valid_organisation_name(
        self,
        organisation_name: str,
        title: str = "",
    ) -> bool:
        """
        Reject corrupted, generic or implausibly long organisation names.
        """
        organisation_name = self._clean_text(
            organisation_name
        )

        if not organisation_name:
            return False

        if organisation_name.lower() in {
            "not specified",
            "unknown",
            "n/a",
            "none",
        }:
            return False

        if len(organisation_name) < 2:
            return False

        if len(organisation_name) > 180:
            return False

        lowered_name = organisation_name.lower()

        if any(
            phrase in lowered_name
            for phrase in self.INVALID_ORGANISATION_PHRASES
        ):
            return False

        if title:
            cleaned_title = self._clean_text(title)

            if (
                organisation_name.lower()
                == cleaned_title.lower()
            ):
                return False

        word_count = len(
            organisation_name.split()
        )

        if word_count > 20:
            return False

        sentence_signals = (
            " is a ",
            " invites ",
            " seeks ",
            " looking for ",
            " responsible for ",
            " working in ",
            " committed to ",
            " provides ",
            " wishes to ",
            " hereby ",
        )

        if any(
            signal in lowered_name
            for signal in sentence_signals
        ):
            return False

        return True

    def _remove_field_label(
        self,
        value: str,
        labels: tuple[str, ...],
    ) -> str:
        """
        Remove labels such as 'Employer:' or 'Company:'.
        """
        result = self._clean_text(value)

        for label in labels:
            result = re.sub(
                rf"^{re.escape(label)}\s*:?\s*",
                "",
                result,
                flags=re.IGNORECASE,
            )

        return result.strip()

    def _remove_site_name(
        self,
        title: str,
    ) -> str:
        """
        Remove the Job in Rwanda suffix from an HTML title.
        """
        cleaned_title = self._clean_text(title)

        patterns = (
            r"\s*\|\s*Job in Rwanda\s*$",
            r"\s*-\s*Job in Rwanda\s*$",
            r"\s*\|\s*Jobs in Rwanda\s*$",
        )

        for pattern in patterns:
            cleaned_title = re.sub(
                pattern,
                "",
                cleaned_title,
                flags=re.IGNORECASE,
            )

        return cleaned_title.strip()

    def _first_valid_text(
        self,
        *values: Any,
    ) -> str:
        """
        Return the first non-empty cleaned string.
        """
        for value in values:
            cleaned_value = self._clean_text(value)

            if cleaned_value:
                return cleaned_value

        return ""

    def _section_name(
        self,
        section_url: str,
    ) -> str:
        section = (
            section_url
            .rstrip("/")
            .split("/")[-1]
        )

        return section.replace("-", " ").title()

    def _title_from_url(
        self,
        url: str,
    ) -> str:
        parsed = urlparse(url)

        slug = (
            parsed.path
            .rstrip("/")
            .split("/")[-1]
        )

        return (
            slug
            .replace("-", " ")
            .replace("_", " ")
            .title()
        )

    def _wait_between_requests(self) -> None:
        if self.REQUEST_DELAY_SECONDS > 0:
            time.sleep(
                self.REQUEST_DELAY_SECONDS
            )

    @staticmethod
    def _clean_text(value: Any) -> str:
        """
        Convert any value into clean single-line text.
        """
        if value is None:
            return ""

        text = str(value)
        text = text.replace("\xa0", " ")
        text = re.sub(r"\s+", " ", text)

        return text.strip()