from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse, urlunparse


@dataclass
class SourceAssessment:
    accepted: bool
    discovery_score: float
    confidence_score: float
    priority_score: float
    url_relevance_score: float
    source_type: str
    organisation_name: str
    base_url: str
    monitor_url: str
    rejection_reason: str | None = None
    score_components: dict[str, float] = field(
        default_factory=dict
    )


class SourceQualityEvaluator:
    """
    Evaluate whether a discovered website is suitable for automatic
    monitoring by Opportunity Radar.
    """

    MINIMUM_AUTO_APPROVAL_SCORE = 60.0

    BLOCKED_DOMAINS = {
        "facebook.com",
        "instagram.com",
        "linkedin.com",
        "twitter.com",
        "x.com",
        "youtube.com",
        "tiktok.com",
        "reddit.com",
        "pinterest.com",
        "wikipedia.org",
        "dictionary.cambridge.org",
        "cambridge.org",
        "procore.com",
        "odoo.com",
        "lusha.com",
        "glassdoor.com",
        "indeed.com",
        "careers.org",
        "techbehemoths.com",
    }

    LOW_VALUE_DOMAIN_TERMS = (
        "dictionary",
        "directory",
        "yellowpages",
        "company-list",
        "business-list",
        "training-course",
        "marketing",
        "seo",
        "learning-center",
        "learning-centre",
        "blogspot",
        "wordpress",
    )

    LOW_VALUE_TEXT_TERMS = (
        "what is an invitation to bid",
        "definition of",
        "meaning of",
        "top companies",
        "best companies",
        "company directory",
        "business directory",
        "sales leads",
        "training venue",
        "training courses in",
        "accounting firms by country",
        "learning center",
        "learning centre",
    )

    OPPORTUNITY_TERMS = (
        "tender",
        "tenders",
        "procurement",
        "request for proposal",
        "request for proposals",
        "rfp",
        "expression of interest",
        "eoi",
        "invitation to bid",
        "terms of reference",
        "consultancy",
        "consultant",
        "vacancy",
        "vacancies",
        "careers",
        "jobs",
        "opportunities",
        "call for proposals",
        "call for applications",
        "supplier",
        "vendor",
        "prequalification",
        "grant",
    )

    HIGH_VALUE_PATH_TERMS = (
        "/tender",
        "/procurement",
        "/opportun",
        "/career",
        "/vacanc",
        "/jobs",
        "/consult",
        "/rfp",
        "/eoi",
        "/bid",
        "/supplier",
    )

    TRUSTED_ORGANISATION_TERMS = (
        "government",
        "ministry",
        "authority",
        "commission",
        "agency",
        "district",
        "university",
        "institute",
        "foundation",
        "association",
        "federation",
        "bank",
        "insurance",
        "united nations",
        "undp",
        "unicef",
        "unfpa",
        "unhcr",
        "iom",
        "world bank",
        "embassy",
        "ngo",
        "organisation",
        "organization",
        "limited",
        "ltd",
        "plc",
    )

    AGGREGATOR_TERMS = (
        "tender portal",
        "tender notices",
        "global tenders",
        "jobs portal",
        "job portal",
        "all jobs",
        "career portal",
        "developmentaid",
        "tendersinfo",
        "tendersontime",
        "biddingsource",
        "tender impulse",
    )

    def assess(
        self,
        *,
        title: str,
        description: str,
        url: str,
        discovery_query: str,
        country: str,
    ) -> SourceAssessment:
        normalised_url = self.normalise_url(url)

        if not normalised_url:
            return self._reject(
                "Invalid or unsupported URL."
            )

        parsed = urlparse(normalised_url)
        domain = self.normalise_domain(parsed.netloc)

        if not domain:
            return self._reject(
                "The website domain could not be determined."
            )

        if self.is_blocked_domain(domain):
            return self._reject(
                f"Blocked or irrelevant domain: {domain}"
            )

        combined_text = self.clean_text(
            f"{title} {description} {normalised_url} {discovery_query}"
        ).lower()

        if any(
            term in domain
            for term in self.LOW_VALUE_DOMAIN_TERMS
        ):
            return self._reject(
                "The domain appears to be a directory, blog or generic information site."
            )

        if any(
            term in combined_text
            for term in self.LOW_VALUE_TEXT_TERMS
        ):
            return self._reject(
                "The result appears informational rather than an opportunity source."
            )

        path_score = self.calculate_url_relevance(
            normalised_url
        )

        is_aggregator = any(
            term in combined_text
            for term in self.AGGREGATOR_TERMS
        )

        source_type = self.infer_source_type(
            combined_text
        )

        organisation_name = self.infer_organisation_name(
            title=title,
            domain=domain,
        )

        base_url = (
            f"{parsed.scheme}://{parsed.netloc}"
        ).rstrip("/")

        monitor_url = self.choose_monitor_url(
            normalised_url
        )

        score_components = {
            "domain_quality": self.calculate_domain_quality(
                normalised_url
            ),
            "url_relevance": path_score,
            "keyword_relevance": self.calculate_keyword_relevance(
                combined_text
            ),
            "page_title": self.calculate_title_relevance(title),
            "organisation_confidence": (
                self.calculate_organisation_confidence(
                    text=combined_text,
                    source_type=source_type,
                    organisation_name=organisation_name,
                    country=country,
                )
            ),
        }
        discovery_score = self.calculate_discovery_score(
            components=score_components,
            is_aggregator=is_aggregator,
        )

        accepted = (
            discovery_score
            >= self.MINIMUM_AUTO_APPROVAL_SCORE
        )

        return SourceAssessment(
            accepted=accepted,
            discovery_score=discovery_score,
            confidence_score=discovery_score,
            priority_score=discovery_score,
            url_relevance_score=path_score,
            source_type=source_type,
            organisation_name=organisation_name,
            base_url=base_url,
            monitor_url=monitor_url,
            rejection_reason=(
                None
                if accepted
                else "Discovery score is below the automatic approval threshold."
            ),
            score_components=score_components,
        )

    def choose_monitor_url(
        self,
        url: str,
    ) -> str:
        """
        Keep a specialised procurement/careers page when one is found.
        Otherwise monitor the organisation's homepage.
        """
        lowered_url = url.lower()

        if any(
            term in lowered_url
            for term in self.HIGH_VALUE_PATH_TERMS
        ):
            return url.rstrip("/")

        parsed = urlparse(url)

        return (
            f"{parsed.scheme}://{parsed.netloc}"
        ).rstrip("/")

    def calculate_url_relevance(
        self,
        url: str,
    ) -> float:
        lowered_url = url.lower()
        score = 0.0

        for term in self.HIGH_VALUE_PATH_TERMS:
            if term in lowered_url:
                score += 15

        if lowered_url.endswith(
            (
                "/procurement",
                "/tenders",
                "/careers",
                "/jobs",
                "/opportunities",
            )
        ):
            score += 20

        path = urlparse(url).path.strip("/")

        if path:
            score += 5

        return min(score, 100.0)

    def calculate_domain_quality(
        self,
        url: str,
    ) -> float:
        parsed = urlparse(url)
        domain = self.normalise_domain(parsed.netloc)

        if ".gov." in domain or domain.startswith("gov."):
            score = 100.0
        elif ".ac." in domain or ".edu." in domain:
            score = 90.0
        elif domain.endswith((".org", ".int")):
            score = 80.0
        elif len(domain.rsplit(".", maxsplit=1)[-1]) == 2:
            score = 75.0
        else:
            score = 55.0

        if parsed.scheme == "https":
            score += 5

        return min(score, 100.0)

    def calculate_keyword_relevance(
        self,
        text: str,
    ) -> float:
        matches = sum(
            1
            for term in self.OPPORTUNITY_TERMS
            if term in text.lower()
        )

        return min(matches * 20.0, 100.0)

    def calculate_title_relevance(
        self,
        title: str,
    ) -> float:
        cleaned_title = self.clean_text(title).lower()

        if not cleaned_title:
            return 0.0

        matches = sum(
            1
            for term in self.OPPORTUNITY_TERMS
            if term in cleaned_title
        )
        score = 35.0 + matches * 25.0

        return min(score, 100.0)

    def calculate_organisation_confidence(
        self,
        *,
        text: str,
        source_type: str,
        organisation_name: str,
        country: str,
    ) -> float:
        matches = sum(
            1
            for term in self.TRUSTED_ORGANISATION_TERMS
            if term in text.lower()
        )
        score = min(matches * 20.0, 60.0)

        if source_type != "Organisation":
            score += 25.0

        if organisation_name.strip():
            score += 10.0

        cleaned_country = self.clean_text(country).lower()
        if cleaned_country and cleaned_country in text.lower():
            score += 5.0

        return min(score, 100.0)

    @staticmethod
    def calculate_discovery_score(
        *,
        components: dict[str, float],
        is_aggregator: bool,
    ) -> float:
        score = (
            components["domain_quality"] * 0.25
            + components["url_relevance"] * 0.20
            + components["keyword_relevance"] * 0.25
            + components["page_title"] * 0.15
            + components["organisation_confidence"] * 0.15
        )

        if is_aggregator:
            score -= 10.0

        return max(0.0, min(round(score, 2), 100.0))

    def calculate_priority(
        self,
        *,
        confidence_score: float,
        source_type: str,
        url_relevance_score: float,
        is_aggregator: bool,
    ) -> float:
        score = confidence_score * 0.65
        score += url_relevance_score * 0.25

        if source_type in {
            "Government Institution",
            "Development Partner",
            "NGO",
            "University",
        }:
            score += 10

        if is_aggregator:
            score -= 10

        return max(
            0.0,
            min(round(score, 2), 100.0),
        )

    def infer_source_type(
        self,
        text: str,
    ) -> str:
        rules = (
            (
                "Government Institution",
                (
                    ".gov.",
                    "government",
                    "ministry",
                    "authority",
                    "commission",
                    "district",
                    "public institution",
                ),
            ),
            (
                "University",
                (
                    ".ac.",
                    ".edu.",
                    "university",
                    "college",
                    "higher education",
                ),
            ),
            (
                "Development Partner",
                (
                    "united nations",
                    "undp",
                    "unicef",
                    "unfpa",
                    "unhcr",
                    "world bank",
                    "embassy",
                    "development partner",
                ),
            ),
            (
                "NGO",
                (
                    "ngo",
                    "non-governmental",
                    "humanitarian",
                    "foundation",
                    "charity",
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
                "Job Portal",
                (
                    "job portal",
                    "jobs portal",
                    "career portal",
                ),
            ),
            (
                "Private Company",
                (
                    "company",
                    "limited",
                    "ltd",
                    "plc",
                    "bank",
                    "insurance",
                    "corporation",
                ),
            ),
        )

        for source_type, terms in rules:
            if any(term in text for term in terms):
                return source_type

        return "Organisation"

    def infer_organisation_name(
        self,
        *,
        title: str,
        domain: str,
    ) -> str:
        cleaned_title = self.clean_text(title)

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
                    value.strip()
                    for value in cleaned_title.split(separator)
                    if value.strip()
                ]
                break

        generic_terms = {
            "home",
            "tender",
            "tenders",
            "procurement",
            "careers",
            "jobs",
            "vacancies",
            "opportunities",
            "request for proposal",
            "expression of interest",
        }

        for part in reversed(parts):
            if (
                len(part) >= 3
                and part.lower() not in generic_terms
                and len(part) <= 255
            ):
                return part

        domain_name = domain.split(".")[0]

        domain_name = re.sub(
            r"[-_]+",
            " ",
            domain_name,
        )

        return domain_name.title()

    def is_blocked_domain(
        self,
        domain: str,
    ) -> bool:
        return any(
            domain == blocked
            or domain.endswith(f".{blocked}")
            for blocked in self.BLOCKED_DOMAINS
        )

    def normalise_url(
        self,
        url: str,
    ) -> str:
        value = self.clean_text(url)

        try:
            parsed = urlparse(value)
        except ValueError:
            return ""

        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.netloc
        ):
            return ""

        path = parsed.path.rstrip("/")

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

    def normalise_domain(
        self,
        domain: str,
    ) -> str:
        domain = (
            self.clean_text(domain)
            .lower()
            .split(":")[0]
            .rstrip(".")
        )

        if domain.startswith("www."):
            domain = domain[4:]

        return domain

    @staticmethod
    def clean_text(
        value: object,
    ) -> str:
        if value is None:
            return ""

        text = str(value).replace("\xa0", " ")

        text = re.sub(
            r"<[^>]+>",
            " ",
            text,
        )

        return re.sub(
            r"\s+",
            " ",
            text,
        ).strip()

    @staticmethod
    def _reject(
        reason: str,
    ) -> SourceAssessment:
        return SourceAssessment(
            accepted=False,
            discovery_score=0.0,
            confidence_score=0.0,
            priority_score=0.0,
            url_relevance_score=0.0,
            source_type="Rejected",
            organisation_name="",
            base_url="",
            monitor_url="",
            rejection_reason=reason,
            score_components={},
        )
