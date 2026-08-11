from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class SourceClassification:
    source_type: str
    confidence_score: float
    reasons: list[str]


STRONG_PROCUREMENT_SIGNALS: dict[str, float] = {
    "request for proposal": 25,
    "request for quotation": 25,
    "expression of interest": 25,
    "invitation to bid": 25,
    "invitation for bids": 25,
    "invitation to tender": 25,
    "procurement notice": 25,
    "tender notice": 25,
    "terms of reference": 20,
    "call for proposals": 20,
    "supplier registration": 20,
    "vendor registration": 20,
    "prequalification": 20,
    "framework agreement": 20,
    "solicitation": 20,
}


BROAD_OPPORTUNITY_SIGNALS: dict[str, float] = {
    "procurement": 15,
    "tender": 15,
    "tenders": 15,
    "bid": 10,
    "bidding": 10,
    "opportunities": 10,
    "supplier": 8,
    "vendor": 8,
    "consultancy": 8,
    "consulting": 8,
    "grant": 8,
    "funding opportunity": 10,
    "call for applications": 10,
    "contract opportunities": 10,
    "business opportunities": 10,
    "partners": 6,
    "partnership": 8,
}


NEGATIVE_SIGNALS: dict[str, float] = {
    "page not found": -60,
    "404 not found": -60,
    "access denied": -45,
    "login required": -25,
    "coming soon": -20,
    "under construction": -20,
    "privacy policy": -10,
    "cookie policy": -10,
}


def classify_source(
    title: str,
    description: str,
    url: str,
    country: str | None = None,
) -> SourceClassification:
    """
    Classify whether a discovered URL appears useful
    for opportunity monitoring.

    Country is an optional relevance signal only.
    No country or domain receives a hard-coded advantage.
    """

    searchable_text = " ".join(
        [
            title or "",
            description or "",
            url or "",
        ]
    ).lower()

    score = 0.0
    reasons: list[str] = []

    for signal, points in STRONG_PROCUREMENT_SIGNALS.items():
        if signal in searchable_text:
            score += points
            reasons.append(signal)

    for signal, points in BROAD_OPPORTUNITY_SIGNALS.items():
        if signal in searchable_text:
            score += points
            reasons.append(signal)

    for signal, points in NEGATIVE_SIGNALS.items():
        if signal in searchable_text:
            score += points
            reasons.append(signal)

    parsed = urlparse(url)

    hostname = (
        parsed.hostname
        or ""
    ).lower()

    if (
        country
        and country.strip().lower()
        in searchable_text
    ):
        score += 5

        reasons.append(
            f"country signal: {country.strip()}"
        )

    if hostname:
        score += 5

        reasons.append(
            "valid domain"
        )

    source_type = determine_source_type(
        searchable_text
    )

    return SourceClassification(
        source_type=source_type,

        confidence_score=min(
            100.0,
            max(
                0.0,
                score,
            ),
        ),

        reasons=sorted(
            set(
                reasons
            )
        ),
    )


def determine_source_type(
    searchable_text: str,
) -> str:

    if any(
        term in searchable_text
        for term in (
            "supplier registration",
            "vendor registration",
            "supplier portal",
            "vendor portal",
            "prequalification",
            "framework agreement",
        )
    ):
        return "Supplier Portal"

    if any(
        term in searchable_text
        for term in (
            "grant",
            "funding opportunity",
            "funding opportunities",
            "call for applications",
            "grant opportunities",
        )
    ):
        return "Funding"

    if any(
        term in searchable_text
        for term in (
            "request for proposal",
            "request for quotation",
            "expression of interest",
            "invitation to bid",
            "invitation for bids",
            "invitation to tender",
            "procurement",
            "tender",
            "bidding",
        )
    ):
        return "Procurement"

    if any(
        term in searchable_text
        for term in (
            "partnership",
            "partnerships",
            "partner opportunities",
            "collaboration opportunities",
            "joint venture",
        )
    ):
        return "Partnership"

    if any(
        term in searchable_text
        for term in (
            "career",
            "careers",
            "vacancy",
            "vacancies",
            "jobs",
            "employment",
        )
    ):
        return "Careers"

    if any(
        term in searchable_text
        for term in (
            "opportunities",
            "consultancy",
            "consultancies",
            "contracts",
            "business opportunities",
        )
    ):
        return "Opportunity Platform"

    return "Unknown"
