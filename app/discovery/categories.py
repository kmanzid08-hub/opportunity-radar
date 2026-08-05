from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DiscoveryCategory:
    name: str
    query_templates: tuple[str, ...]
    tags: tuple[str, ...]

    def queries_for(self, country: str) -> tuple[str, ...]:
        return tuple(
            template.format(country=country)
            for template in self.query_templates
        )


DISCOVERY_CATEGORIES: tuple[DiscoveryCategory, ...] = (
    DiscoveryCategory(
        name="Government procurement",
        query_templates=(
            "{country} government procurement opportunities",
            "{country} public procurement notices",
        ),
        tags=("government", "procurement"),
    ),
    DiscoveryCategory(
        name="Government tenders",
        query_templates=(
            "{country} government tenders",
            '"{country}" ministry "invitation to bid"',
        ),
        tags=("government", "procurement"),
    ),
    DiscoveryCategory(
        name="NGO procurement",
        query_templates=(
            "{country} NGO procurement",
            '"{country}" NGO "request for proposal"',
        ),
        tags=("ngo", "procurement"),
    ),
    DiscoveryCategory(
        name="NGO careers",
        query_templates=(
            "{country} NGO careers vacancies",
            '"{country}" humanitarian organisation jobs',
        ),
        tags=("ngo", "career"),
    ),
    DiscoveryCategory(
        name="University procurement",
        query_templates=(
            "{country} university procurement tenders",
            '"{country}" university "request for proposal"',
        ),
        tags=("education", "procurement"),
    ),
    DiscoveryCategory(
        name="University careers",
        query_templates=(
            "{country} university careers vacancies",
            '"{country}" higher education jobs',
        ),
        tags=("education", "career"),
    ),
    DiscoveryCategory(
        name="Company careers",
        query_templates=(
            "{country} company careers vacancies",
            '"{country}" private sector jobs careers',
        ),
        tags=("private", "career"),
    ),
    DiscoveryCategory(
        name="Company procurement",
        query_templates=(
            "{country} company procurement opportunities",
            '"{country}" private company supplier tenders',
        ),
        tags=("private", "procurement"),
    ),
    DiscoveryCategory(
        name="Consulting opportunities",
        query_templates=(
            "{country} consulting opportunities",
            '"{country}" "terms of reference" consultant',
        ),
        tags=("consulting",),
    ),
    DiscoveryCategory(
        name="Vendor registration",
        query_templates=(
            "{country} vendor registration suppliers",
            '"{country}" supplier prequalification registration',
        ),
        tags=("vendor", "procurement"),
    ),
    DiscoveryCategory(
        name="Grants",
        query_templates=(
            "{country} grants call for proposals",
            '"{country}" grant funding opportunities',
        ),
        tags=("grant",),
    ),
    DiscoveryCategory(
        name="Expressions of Interest",
        query_templates=(
            '"{country}" "expression of interest"',
            '"{country}" EOI consultancy procurement',
        ),
        tags=("procurement", "consulting"),
    ),
    DiscoveryCategory(
        name="Framework agreements",
        query_templates=(
            '"{country}" "framework agreement" procurement',
            '"{country}" "framework contract" tender',
        ),
        tags=("procurement", "vendor"),
    ),
    DiscoveryCategory(
        name="Development partners",
        query_templates=(
            "{country} development partner opportunities",
            '"{country}" development agency procurement careers',
        ),
        tags=("development", "procurement"),
    ),
    DiscoveryCategory(
        name="International organisations",
        query_templates=(
            "{country} international organisation opportunities",
            '"{country}" United Nations procurement careers',
        ),
        tags=("international", "procurement"),
    ),
    DiscoveryCategory(
        name="Private sector procurement",
        query_templates=(
            "{country} private sector procurement tenders",
            '"{country}" corporate supplier opportunities',
        ),
        tags=("private", "procurement"),
    ),
)


TAG_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("government", ("government", "ministry", "authority", "public sector")),
    ("ngo", ("ngo", "non-governmental", "humanitarian", "charity")),
    ("private", ("company", "limited", "ltd", "plc", "corporate")),
    ("career", ("career", "careers", "vacancy", "vacancies", "jobs")),
    ("procurement", ("procurement", "tender", "bid", "rfp")),
    ("grant", ("grant", "funding", "call for proposals")),
    ("vendor", ("vendor", "supplier", "prequalification")),
    ("consulting", ("consulting", "consultancy", "consultant")),
    ("audit", ("audit", "auditing")),
    ("tax", ("tax", "taxation")),
    ("engineering", ("engineering", "engineer")),
    ("construction", ("construction", "infrastructure", "civil works")),
    ("health", ("health", "medical", "hospital")),
    ("education", ("education", "university", "college", "school")),
    ("technology", ("technology", "digital", "software", "information technology")),
)


def infer_discovery_tags(
    *,
    category: DiscoveryCategory,
    title: str,
    description: str,
    url: str,
    source_type: str,
) -> tuple[str, ...]:
    text = " ".join(
        (category.name, title, description, url, source_type)
    ).lower()
    tags = set(category.tags)

    for tag, terms in TAG_RULES:
        if any(term in text for term in terms):
            tags.add(tag)

    return tuple(sorted(tags or {"opportunity"}))
