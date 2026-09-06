from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re

from app.schemas import FilteredOpportunity


@dataclass(frozen=True)
class BusinessMatchPreferences:
    countries: tuple[str, ...] = ()
    regions: tuple[str, ...] = ()
    industries: tuple[str, ...] = ()
    services: tuple[str, ...] = ()
    opportunity_types: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    minimum_match_score: int = 0
    include_no_deadline: bool = True
    include_jobs: bool = False
    include_tenders: bool = True
    include_grants: bool = True
    include_partnerships: bool = True


@dataclass(frozen=True)
class BusinessMatchResult:
    score: int
    reason: str
    is_visible: bool


TOKEN_RE = re.compile(r"[a-z0-9]+")
JOB_TERMS = (
    "job", "jobs", "career", "careers", "vacancy", "vacancies",
    "employment", "recruitment", "hiring",
)
TENDER_TERMS = (
    "tender", "rfp", "rfq", "eoi", "expression of interest",
    "request for proposal", "request for quotation",
    "invitation to bid", "invitation to tender",
    "terms of reference", "procurement", "bid",
)
GRANT_TERMS = (
    "grant", "grants", "call for proposals", "funding opportunity",
    "funding opportunities",
)
PARTNERSHIP_TERMS = (
    "partnership", "partner", "partners", "consortium",
    "collaboration", "joint venture",
)


def _normalise(value: object) -> str:
    return " ".join(str(value or "").casefold().split())


def _tokens(value: object) -> set[str]:
    return set(TOKEN_RE.findall(_normalise(value)))


def _opportunity_text(opportunity: FilteredOpportunity) -> str:
    return _normalise(
        " ".join(
            (
                opportunity.title or "",
                opportunity.organisation_name or "",
                opportunity.category or "",
                opportunity.source_name or "",
                opportunity.description or "",
                opportunity.source_url or "",
            )
        )
    )


def _phrase_matches(
    values: tuple[str, ...],
    text: str,
) -> list[str]:
    matches: list[str] = []
    for value in values:
        clean = _normalise(value)
        if clean and clean in text:
            matches.append(value)
    return matches


def _soft_matches(
    values: tuple[str, ...],
    text: str,
) -> list[str]:
    text_tokens = _tokens(text)
    matches: list[str] = []

    for value in values:
        clean = _normalise(value)
        if not clean:
            continue
        if clean in text:
            matches.append(value)
            continue

        meaningful = {
            token
            for token in _tokens(clean)
            if len(token) >= 4
        }
        if meaningful and meaningful.issubset(text_tokens):
            matches.append(value)

    return matches


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _detect_kind(text: str) -> str:
    if _contains_any(text, JOB_TERMS):
        return "job"
    if _contains_any(text, GRANT_TERMS):
        return "grant"
    if _contains_any(text, PARTNERSHIP_TERMS):
        return "partnership"
    if _contains_any(text, TENDER_TERMS):
        return "tender"
    return "other"


def _type_allowed(
    kind: str,
    preferences: BusinessMatchPreferences,
) -> bool:
    if kind == "job":
        return preferences.include_jobs
    if kind == "tender":
        return preferences.include_tenders
    if kind == "grant":
        return preferences.include_grants
    if kind == "partnership":
        return preferences.include_partnerships
    return True


def _deadline_allowed(
    opportunity: FilteredOpportunity,
    preferences: BusinessMatchPreferences,
) -> bool:
    if opportunity.deadline is None:
        return preferences.include_no_deadline
    return opportunity.deadline >= date.today()


def score_for_business(
    opportunity: FilteredOpportunity,
    preferences: BusinessMatchPreferences,
) -> BusinessMatchResult:
    """
    Score business relevance only.

    Whether the underlying page is a genuine opportunity belongs to
    app.filters. This module only answers how well a genuine opportunity
    fits the saved business profile.
    """
    text = _opportunity_text(opportunity)
    kind = _detect_kind(text)

    if not _type_allowed(kind, preferences):
        return BusinessMatchResult(
            score=0,
            reason=f"Excluded by your saved {kind} preferences.",
            is_visible=False,
        )

    if not _deadline_allowed(opportunity, preferences):
        reason = (
            "Excluded because your profile hides opportunities "
            "without a deadline."
            if opportunity.deadline is None
            else "Excluded because the opportunity deadline has passed."
        )
        return BusinessMatchResult(
            score=0,
            reason=reason,
            is_visible=False,
        )

    country_matches = _phrase_matches(preferences.countries, text)
    region_matches = _phrase_matches(preferences.regions, text)
    industry_matches = _soft_matches(preferences.industries, text)
    service_matches = _soft_matches(preferences.services, text)
    keyword_matches = _soft_matches(preferences.keywords, text)
    type_matches = _soft_matches(preferences.opportunity_types, text)
    language_matches = _soft_matches(preferences.languages, text)

    score = 0

    if country_matches:
        score += 18
    elif region_matches:
        score += 10

    score += min(22, len(service_matches) * 11)
    score += min(18, len(industry_matches) * 9)
    score += min(18, len(keyword_matches) * 6)
    score += min(14, len(type_matches) * 7)
    score += min(6, len(language_matches) * 3)

    # Type compatibility is useful, but cannot manufacture a high match.
    if kind in {"tender", "grant", "partnership", "job"}:
        score += 4

    score = max(0, min(100, score))

    reasons: list[str] = []

    if service_matches:
        reasons.append("Services: " + ", ".join(service_matches[:3]))
    if industry_matches:
        reasons.append("Industries: " + ", ".join(industry_matches[:3]))
    if country_matches:
        reasons.append("Location: " + ", ".join(country_matches[:2]))
    elif region_matches:
        reasons.append("Region: " + ", ".join(region_matches[:2]))
    if keyword_matches:
        reasons.append("Keywords: " + ", ".join(keyword_matches[:3]))
    if type_matches:
        reasons.append(
            "Opportunity type: " + ", ".join(type_matches[:2])
        )
    if language_matches:
        reasons.append("Language: " + ", ".join(language_matches[:2]))

    if not reasons:
        reasons.append(
            "No strong business-profile match was found in the "
            "opportunity details."
        )

    threshold = max(0, min(100, preferences.minimum_match_score))

    return BusinessMatchResult(
        score=score,
        reason=" · ".join(reasons),
        is_visible=score >= threshold,
    )
