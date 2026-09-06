from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class DiscoveryCategory:
    name: str
    query_templates: tuple[str, ...]
    tags: tuple[str, ...]

    def queries_for(
        self,
        country: str,
    ) -> tuple[str, ...]:
        """
        Build this category's search queries
        for the supplied country.
        """

        clean_country = (
            " ".join(
                country.split()
            )
            .strip()
        )

        if not clean_country:
            raise ValueError(
                "country is required"
            )

        return tuple(
            template.format(
                country=clean_country
            )
            for template
            in self.query_templates
        )


DISCOVERY_CATEGORIES: tuple[
    DiscoveryCategory,
    ...,
] = (
    DiscoveryCategory(
        name="Government procurement",
        query_templates=(
            "{country} government procurement opportunities",
            "{country} public procurement notices",
        ),
        tags=(
            "government",
            "procurement",
        ),
    ),

    DiscoveryCategory(
        name="Government tenders",
        query_templates=(
            "{country} government tenders",
            '"{country}" ministry "invitation to bid"',
        ),
        tags=(
            "government",
            "procurement",
        ),
    ),

    DiscoveryCategory(
        name="NGO procurement",
        query_templates=(
            "{country} NGO procurement",
            '"{country}" NGO "request for proposal"',
        ),
        tags=(
            "ngo",
            "procurement",
        ),
    ),

    DiscoveryCategory(
        name="NGO careers",
        query_templates=(
            "{country} NGO careers vacancies",
            '"{country}" humanitarian organisation jobs',
        ),
        tags=(
            "ngo",
            "career",
        ),
    ),

    DiscoveryCategory(
        name="University procurement",
        query_templates=(
            "{country} university procurement tenders",
            '"{country}" university "request for proposal"',
        ),
        tags=(
            "education",
            "procurement",
        ),
    ),

    DiscoveryCategory(
        name="University careers",
        query_templates=(
            "{country} university careers vacancies",
            '"{country}" higher education jobs',
        ),
        tags=(
            "education",
            "career",
        ),
    ),

    DiscoveryCategory(
        name="Company careers",
        query_templates=(
            "{country} company careers vacancies",
            '"{country}" private sector jobs careers',
        ),
        tags=(
            "private",
            "career",
        ),
    ),

    DiscoveryCategory(
        name="Company procurement",
        query_templates=(
            "{country} company procurement opportunities",
            '"{country}" private company supplier tenders',
        ),
        tags=(
            "private",
            "procurement",
        ),
    ),

    DiscoveryCategory(
        name="Consulting opportunities",
        query_templates=(
            "{country} consulting opportunities",
            '"{country}" "terms of reference" consultant',
        ),
        tags=(
            "consulting",
        ),
    ),

    DiscoveryCategory(
        name="Vendor registration",
        query_templates=(
            "{country} vendor registration suppliers",
            '"{country}" supplier prequalification registration',
        ),
        tags=(
            "vendor",
            "procurement",
        ),
    ),

    DiscoveryCategory(
        name="Grants",
        query_templates=(
            "{country} grants call for proposals",
            '"{country}" grant funding opportunities',
        ),
        tags=(
            "grant",
        ),
    ),

    DiscoveryCategory(
        name="Expressions of Interest",
        query_templates=(
            '"{country}" "expression of interest"',
            '"{country}" EOI consultancy procurement',
        ),
        tags=(
            "procurement",
            "consulting",
        ),
    ),

    DiscoveryCategory(
        name="Framework agreements",
        query_templates=(
            '"{country}" "framework agreement" procurement',
            '"{country}" "framework contract" tender',
        ),
        tags=(
            "procurement",
            "vendor",
        ),
    ),

    DiscoveryCategory(
        name="Development partners",
        query_templates=(
            "{country} development partner opportunities",
            '"{country}" development agency procurement careers',
        ),
        tags=(
            "development",
            "procurement",
        ),
    ),

    DiscoveryCategory(
        name="International organisations",
        query_templates=(
            "{country} international organisation opportunities",
            '"{country}" United Nations procurement careers',
        ),
        tags=(
            "international",
            "procurement",
        ),
    ),

    DiscoveryCategory(
        name="Private sector procurement",
        query_templates=(
            "{country} private sector procurement tenders",
            '"{country}" corporate supplier opportunities',
        ),
        tags=(
            "private",
            "procurement",
        ),
    ),
)


SOURCE_TYPE_TAGS: dict[
    str,
    tuple[str, ...],
] = {
    "Government Institution": (
        "government",
    ),

    "NGO": (
        "ngo",
    ),

    "University": (
        "education",
    ),

    "Development Partner": (
        "development",
        "international",
    ),

    "Procurement Portal": (
        "procurement",
    ),

    "Job Portal": (
        "career",
    ),

    "Private Company": (
        "private",
    ),
}


STRONG_TAG_RULES: dict[
    str,
    tuple[str, ...],
] = {
    "procurement": (
        "procurement",
        "tender",
        "tenders",
        "request for proposal",
        "request for quotation",
        "expression of interest",
        "invitation to bid",
        "invitation to tender",
        "rfp",
        "rfq",
        "eoi",
    ),

    "vendor": (
        "vendor registration",
        "supplier registration",
        "supplier prequalification",
        "vendor prequalification",
        "prequalification of suppliers",
        "prequalification of vendors",
        "supplier portal",
        "vendor portal",
    ),

    "grant": (
        "grant opportunity",
        "grant opportunities",
        "call for grants",
        "funding opportunity",
        "funding opportunities",
        "grant funding",
    ),

    "career": (
        "careers",
        "vacancies",
        "job vacancies",
        "employment opportunities",
        "career opportunities",
        "jobs",
    ),

    "consulting": (
        "consultancy opportunity",
        "consultancy opportunities",
        "consulting opportunity",
        "consulting opportunities",
        "terms of reference",
        "consultancy services",
        "consulting services",
    ),

    "government": (
        "government of",
        "ministry of",
        "government procurement",
        "public procurement",
        "government tenders",
    ),

    "ngo": (
        "non-governmental organization",
        "non-governmental organisation",
        "humanitarian organization",
        "humanitarian organisation",
        "ngo procurement",
        "ngo opportunities",
    ),

    "education": (
        "university procurement",
        "university tenders",
        "higher education",
        "education procurement",
        "university careers",
    ),

    "health": (
        "health procurement",
        "healthcare procurement",
        "medical equipment tender",
        "medical supplies tender",
        "hospital procurement",
    ),

    "tax": (
        "tax advisory services",
        "tax consultancy services",
        "tax compliance services",
        "tax services tender",
    ),

    "development": (
        "development partner",
        "development agency",
        "international development",
    ),

    "international": (
        "united nations",
        "world bank",
        "international organization",
        "international organisation",
        "international agency",
    ),

    "private": (
        "private company procurement",
        "private sector procurement",
        "corporate procurement",
        "corporate supplier",
    ),
}


def _normalise_tag_text(
    value: str | None,
) -> str:
    if not value:
        return ""

    return (
        " ".join(
            value.lower().split()
        )
        .strip()
    )


def _contains_any(
    text: str,
    terms: tuple[str, ...],
) -> bool:
    return any(
        term in text
        for term in terms
    )


def infer_discovery_tags(
    *,
    category: DiscoveryCategory,
    title: str,
    description: str,
    url: str,
    source_type: str,
) -> tuple[str, ...]:
    """
    Generate controlled source-discovery tags.

    Category tags are authoritative.

    Source type can add a small number of structural
    tags.

    Search-result descriptions are deliberately prevented
    from creating large numbers of unrelated tags.
    """

    tags: set[str] = {
        tag.strip().lower()
        for tag in category.tags
        if (
            tag
            and tag.strip()
        )
    }

    #
    # Source type provides structural information.
    #
    tags.update(
        SOURCE_TYPE_TAGS.get(
            source_type,
            (),
        )
    )

    parsed = urlparse(
        url or ""
    )

    #
    # Title and URL are considered stronger evidence
    # than a search-engine snippet.
    #
    strong_text = (
        _normalise_tag_text(
            " ".join(
                (
                    title or "",
                    parsed.netloc or "",
                    parsed.path or "",
                )
            )
        )
    )

    description_text = (
        _normalise_tag_text(
            description
        )
    )

    category_name = (
        category.name
        .strip()
        .lower()
    )

    category_tags = {
        tag.lower()
        for tag in category.tags
    }

    #
    # Add extra tags only when title/URL contain
    # explicit strong evidence.
    #
    for tag, terms in STRONG_TAG_RULES.items():
        if tag in tags:
            continue

        if _contains_any(
            strong_text,
            terms,
        ):
            tags.add(
                tag
            )

            continue

        #
        # Description-only matching is intentionally
        # much stricter.
        #
        # A Brave snippet mentioning "NGOs, health,
        # careers and suppliers" must not make a generic
        # tender portal belong to all those categories.
        #
        explicit_terms = tuple(
            term
            for term in terms
            if len(
                term.split()
            ) >= 2
        )

        if not explicit_terms:
            continue

        if not _contains_any(
            description_text,
            explicit_terms,
        ):
            continue

        #
        # Search descriptions may confirm the category,
        # but may not invent unrelated sectors.
        #
        if (
            tag in category_tags
            or tag in category_name
        ):
            tags.add(
                tag
            )

    #
    # Category intent is authoritative.
    #
    if any(
        term in category_name
        for term in (
            "procurement",
            "tender",
            "expression of interest",
            "framework",
            "vendor registration",
        )
    ):
        tags.add(
            "procurement"
        )

    if "career" in category_name:
        tags.add(
            "career"
        )

    if "grant" in category_name:
        tags.add(
            "grant"
        )

    if "government" in category_name:
        tags.add(
            "government"
        )

    if "ngo" in category_name:
        tags.add(
            "ngo"
        )

    if "university" in category_name:
        tags.add(
            "education"
        )

    if "consult" in category_name:
        tags.add(
            "consulting"
        )

    if "vendor" in category_name:
        tags.add(
            "vendor"
        )

    if "development partner" in category_name:
        tags.add(
            "development"
        )

    if "international" in category_name:
        tags.add(
            "international"
        )

    if (
        "company" in category_name
        or "private sector" in category_name
    ):
        tags.add(
            "private"
        )

    #
    # Keep metadata deterministic.
    #
    cleaned_tags = {
        tag.strip().lower()
        for tag in tags
        if (
            tag
            and tag.strip()
        )
    }

    if not cleaned_tags:
        cleaned_tags.add(
            "opportunity"
        )

    return tuple(
        sorted(
            cleaned_tags
        )
    )
