from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from app.schemas import FilteredOpportunity, RawOpportunity


@dataclass(frozen=True)
class CategoryRule:
    category: str
    strong_terms: tuple[str, ...]
    broad_terms: tuple[str, ...]


@dataclass(frozen=True)
class ClassificationDecision:
    opportunity: FilteredOpportunity | None
    rejection_reason: str | None = None


CATEGORY_RULES: tuple[CategoryRule, ...] = (
    CategoryRule(
        category="Audit",
        strong_terms=(
            "external audit services",
            "statutory audit services",
            "financial audit services",
            "internal audit services",
            "project audit services",
            "forensic audit services",
            "audit of financial statements",
            "appointment of external auditor",
            "independent audit firm",
            "qualified audit firms",
            "assurance services",
            "agreed upon procedures",
            "agreed-upon procedures",
        ),
        broad_terms=(
            "external audit",
            "statutory audit",
            "financial audit",
            "internal audit",
            "project audit",
            "forensic audit",
            "auditor",
            "auditing",
            "financial statements",
            "fund accountability statement",
        ),
    ),
    CategoryRule(
        category="Accounting",
        strong_terms=(
            "accounting services",
            "bookkeeping services",
            "financial reporting services",
            "preparation of financial statements",
            "outsourced accounting",
            "accounting support services",
        ),
        broad_terms=(
            "accounting",
            "bookkeeping",
            "financial reporting",
            "general ledger",
            "account reconciliation",
            "ifrs",
            "ipsas",
        ),
    ),
    CategoryRule(
        category="Tax",
        strong_terms=(
            "tax advisory services",
            "tax consultancy services",
            "tax compliance services",
            "transfer pricing services",
        ),
        broad_terms=(
            "tax advisory",
            "tax compliance",
            "taxation",
            "value added tax",
            "withholding tax",
            "corporate income tax",
            "transfer pricing",
        ),
    ),
    CategoryRule(
        category="Recruitment",
        strong_terms=(
            "recruitment services",
            "staff recruitment services",
            "executive search services",
            "recruitment consultancy",
            "candidate assessment services",
        ),
        broad_terms=(
            "recruitment firm",
            "executive search",
            "headhunting",
            "staff selection",
            "candidate assessment",
        ),
    ),
    CategoryRule(
        category="Consulting",
        strong_terms=(
            "consultancy services to conduct",
            "consulting services to conduct",
            "consultancy assignment",
            "technical assistance services",
            "institutional assessment consultancy",
            "policy development consultancy",
            "strategy development consultancy",
            "feasibility study consultancy",
            "baseline study consultancy",
            "endline evaluation consultancy",
            "impact assessment consultancy",
        ),
        broad_terms=(
            "consultancy",
            "consultant",
            "technical assistance",
            "institutional assessment",
            "policy development",
            "strategy development",
            "feasibility study",
            "baseline study",
            "endline evaluation",
            "midterm evaluation",
            "impact assessment",
        ),
    ),
    CategoryRule(
        category="Risk and Governance",
        strong_terms=(
            "risk management consultancy",
            "enterprise risk management consultancy",
            "corporate governance consultancy",
            "governance assessment services",
            "internal control review services",
            "compliance review services",
            "fraud risk assessment services",
        ),
        broad_terms=(
            "risk management",
            "corporate governance",
            "internal controls",
            "compliance review",
            "fraud risk",
            "governance framework",
            "risk assessment",
        ),
    ),
    CategoryRule(
        category="Due Diligence",
        strong_terms=(
            "financial due diligence services",
            "commercial due diligence services",
            "vendor due diligence services",
            "transaction due diligence services",
        ),
        broad_terms=(
            "due diligence",
            "business verification",
            "financial verification",
            "transaction advisory",
        ),
    ),
    CategoryRule(
        category="Training",
        strong_terms=(
            "provision of training services",
            "capacity building services",
            "training consultancy services",
            "financial management training services",
            "accounting training services",
            "audit training services",
        ),
        broad_terms=(
            "training services",
            "capacity building",
            "workshop facilitation",
            "coaching services",
        ),
    ),
    CategoryRule(
        category="Payroll",
        strong_terms=(
            "payroll services",
            "payroll outsourcing services",
            "payroll management services",
        ),
        broad_terms=(
            "payroll outsourcing",
            "salary processing services",
            "employee benefits administration",
        ),
    ),
    CategoryRule(
        category="Environmental and Social Consulting",
        strong_terms=(
            "environmental and social impact assessment services",
            "environmental audit services",
            "environmental consultancy services",
            "social impact assessment services",
            "esia consultancy",
            "environmental management plan consultancy",
        ),
        broad_terms=(
            "environmental and social impact assessment",
            "environmental audit",
            "social safeguards",
            "esia",
            "esmp",
            "environmental assessment",
        ),
    ),
)


# Phrases that clearly indicate an actual procurement process.
STRONG_PROCUREMENT_TERMS: tuple[str, ...] = (
    "request for proposal",
    "request for proposals",
    "request for quotation",
    "request for quotations",
    "request for expression of interest",
    "request for expressions of interest",
    "call for expression of interest",
    "call for expressions of interest",
    "invitation to bid",
    "invitation for bids",
    "invitation to tender",
    "invitation for tender",
    "tender notice",
    "procurement notice",
    "specific procurement notice",
    "general procurement notice",
    "call for proposals",
    "terms of reference",
    "solicitation document",
    "bidding document",
    "bid document",
)

PROCUREMENT_ABBREVIATIONS: tuple[str, ...] = (
    "rfp",
    "rfq",
    "eoi",
    "tor",
    "itb",
)

SUBMISSION_ACTION_TERMS: tuple[str, ...] = (
    "submit a proposal",
    "submit proposals",
    "submission of proposals",
    "proposal submission",
    "bid submission",
    "submit a bid",
    "submit bids",
    "bids must be submitted",
    "proposals must be submitted",
    "applications must be submitted",
    "invites eligible firms",
    "invites qualified firms",
    "invites interested firms",
    "interested firms are invited",
    "eligible bidders are invited",
    "qualified bidders are invited",
    "solicits proposals",
    "invites proposals",
    "invites quotations",
)

PROCUREMENT_METADATA_TERMS: tuple[str, ...] = (
    "tender number",
    "tender reference",
    "procurement reference",
    "reference number",
    "bid number",
    "rfp number",
    "rfq number",
    "submission deadline",
    "proposal deadline",
    "bid deadline",
    "application deadline",
    "closing date",
    "closing deadline",
    "deadline for submission",
    "date of submission",
    "bid opening",
    "technical proposal",
    "financial proposal",
    "eligibility criteria",
    "evaluation criteria",
)

FIRM_INDICATORS: tuple[str, ...] = (
    "qualified firm",
    "qualified firms",
    "eligible firm",
    "eligible firms",
    "interested firm",
    "interested firms",
    "consulting firm",
    "consultancy firm",
    "audit firm",
    "service provider",
    "service providers",
    "qualified bidder",
    "qualified bidders",
    "eligible bidder",
    "eligible bidders",
    "interested bidder",
    "interested bidders",
    "company or firm",
    "companies or firms",
)

EMPLOYMENT_ONLY_TERMS: tuple[str, ...] = (
    "full time employee",
    "employment contract",
    "monthly salary",
    "job applicant",
    "individual applicant",
    "submit your cv",
    "send your cv",
    "upload your cv",
    "curriculum vitae only",
    "employee benefits",
    "reporting to the manager",
    "job description",
    "career opportunity",
    "vacant position",
    "vacancy announcement",
    "apply for this job",
)

GENERIC_PAGE_TERMS: tuple[str, ...] = (
    "about us",
    "our services",
    "consulting services worldwide",
    "consulting industry",
    "our team",
    "leadership team",
    "contact us",
    "privacy policy",
    "cookie policy",
    "terms and conditions",
    "careers",
    "latest jobs",
    "job listings",
    "blog",
    "news article",
    "training catalogue",
    "course catalogue",
    "upcoming courses",
    "membership",
)

GENERIC_TITLES: tuple[str, ...] = (
    "about us",
    "services",
    "our services",
    "consulting services",
    "consulting industry",
    "responsibilities",
    "job description",
    "careers",
    "recruitment",
    "contact us",
    "home",
    "global",
    "europe",
    "north america",
    "africa",
    "rwanda",
)

AGGREGATE_LISTING_TERMS: tuple[str, ...] = (
    "advanced search",
    "filter country",
    "list of tenders",
    "no of entries",
    "search results",
    "tender listings",
    "total tenders",
)

AGGREGATE_REPEATED_MARKERS: tuple[str, ...] = (
    "publish date",
    "closing date",
    "tender type",
    "country",
)

DOCUMENT_EXTENSIONS: tuple[str, ...] = (
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
)

DOCUMENT_PROCUREMENT_HINTS: tuple[str, ...] = (
    "tender",
    "rfp",
    "rfq",
    "eoi",
    "tor",
    "expression of interest",
    "request for proposal",
    "request for quotation",
    "invitation to bid",
    "procurement",
    "bid document",
)

MINIMUM_ACCEPTANCE_SCORE = 55


def normalise_text(value: str | None) -> str:
    if not value:
        return ""

    text = value.lower().replace("&", " and ")
    text = re.sub(r"[-_/|]", " ", text)
    text = re.sub(r"[^a-z0-9\s.]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def contains_term(text: str, term: str) -> bool:
    """Match whole words/phrases and avoid accidental substrings."""
    cleaned = normalise_text(term)
    if not cleaned:
        return False

    pattern = r"(?<![a-z0-9])" + re.escape(cleaned).replace(r"\ ", r"\s+") + r"(?![a-z0-9])"
    return re.search(pattern, text) is not None


def find_matches(text: str, terms: tuple[str, ...]) -> list[str]:
    return sorted({term for term in terms if contains_term(text, term)})


def build_searchable_text(opportunity: RawOpportunity) -> str:
    values = (
        getattr(opportunity, "title", ""),
        getattr(opportunity, "description", ""),
        getattr(opportunity, "organisation_name", ""),
        getattr(opportunity, "source_name", ""),
        getattr(opportunity, "source_url", ""),
    )
    return normalise_text(" ".join(value or "" for value in values))


def is_procurement_document(opportunity: RawOpportunity) -> bool:
    source_url = str(getattr(opportunity, "source_url", "") or "")
    path = urlparse(source_url).path.lower()

    if not path.endswith(DOCUMENT_EXTENSIONS):
        return False

    document_text = normalise_text(
        f"{getattr(opportunity, 'title', '')} {path}"
    )
    return bool(find_matches(document_text, DOCUMENT_PROCUREMENT_HINTS))


def aggregate_listing_reason(
    opportunity: RawOpportunity,
) -> str | None:
    """Identify index/search pages that contain several separate notices."""
    title = normalise_text(getattr(opportunity, "title", ""))
    description = normalise_text(
        getattr(opportunity, "description", "")
    )
    combined = f"{title} {description}".strip()

    repeated_markers = sorted(
        marker
        for marker in AGGREGATE_REPEATED_MARKERS
        if description.count(marker) >= 2
    )
    aggregate_terms = find_matches(
        combined,
        AGGREGATE_LISTING_TERMS,
    )

    if len(repeated_markers) >= 2:
        return (
            "aggregate listing page contains multiple notices "
            f"(repeated markers: {', '.join(repeated_markers)})"
        )

    if len(aggregate_terms) >= 2 and len(description) >= 1_000:
        return (
            "aggregate listing/search page was detected "
            f"(indicators: {', '.join(aggregate_terms)})"
        )

    if (
        len(title) >= 160
        and "advanced search" in title
        and ("tender" in title or "opportunit" in title)
    ):
        return "aggregate listing/search page has a generated index title"

    return None


def classify_opportunity_with_reason(
    opportunity: RawOpportunity,
) -> ClassificationDecision:
    text = build_searchable_text(opportunity)
    title = normalise_text(getattr(opportunity, "title", ""))

    if not title:
        return ClassificationDecision(
            opportunity=None,
            rejection_reason="title is missing or empty",
        )

    if not text:
        return ClassificationDecision(
            opportunity=None,
            rejection_reason="no searchable opportunity content",
        )

    aggregate_reason = aggregate_listing_reason(opportunity)
    if aggregate_reason is not None:
        return ClassificationDecision(
            opportunity=None,
            rejection_reason=aggregate_reason,
        )

    strong_procurement = find_matches(text, STRONG_PROCUREMENT_TERMS)
    abbreviations = find_matches(text, PROCUREMENT_ABBREVIATIONS)
    submission_actions = find_matches(text, SUBMISSION_ACTION_TERMS)
    metadata = find_matches(text, PROCUREMENT_METADATA_TERMS)
    firm_matches = find_matches(text, FIRM_INDICATORS)
    employment_matches = find_matches(text, EMPLOYMENT_ONLY_TERMS)
    generic_page_matches = find_matches(text, GENERIC_PAGE_TERMS)
    deadline_present = getattr(opportunity, "deadline", None) is not None
    document_evidence = is_procurement_document(opportunity)

    # A service-related word alone is not enough. There must be evidence
    # that an organisation is actually inviting a bid, proposal or EOI.
    has_procurement_evidence = bool(
        strong_procurement
        or document_evidence
        or submission_actions
        or (
            deadline_present
            and (metadata or firm_matches or abbreviations)
        )
        or (
            metadata
            and firm_matches
        )
    )

    if not has_procurement_evidence:
        return ClassificationDecision(
            opportunity=None,
            rejection_reason="no procurement evidence was detected",
        )

    # Reject ordinary employment vacancies unless the text also clearly
    # requests a professional firm/service provider through procurement.
    if employment_matches and not (
        strong_procurement
        or submission_actions
        or document_evidence
        or firm_matches
    ):
        return ClassificationDecision(
            opportunity=None,
            rejection_reason=(
                "employment listing without evidence that firms or "
                "service providers are being invited"
            ),
        )

    title_is_generic = any(title == normalise_text(item) for item in GENERIC_TITLES)
    if title_is_generic and not (
        len(strong_procurement) >= 1
        and (deadline_present or metadata or submission_actions)
    ):
        return ClassificationDecision(
            opportunity=None,
            rejection_reason=(
                "generic page title without enough procurement evidence"
            ),
        )

    category_scores: dict[str, int] = {}
    category_reasons: dict[str, list[str]] = {}

    for rule in CATEGORY_RULES:
        strong_service = find_matches(text, rule.strong_terms)
        broad_service = find_matches(text, rule.broad_terms)

        if not strong_service and not broad_service:
            continue

        score = 0
        score += min(35, len(strong_service) * 20)
        score += min(15, len(broad_service) * 5)
        score += min(30, len(strong_procurement) * 15)
        score += min(10, len(submission_actions) * 5)
        score += min(10, len(metadata) * 3)
        score += min(5, len(firm_matches) * 2)
        score += 10 if deadline_present else 0
        score += 15 if document_evidence else 0
        score += min(5, len(abbreviations) * 2)

        if generic_page_matches:
            score -= min(30, len(generic_page_matches) * 10)

        if employment_matches:
            score -= min(25, len(employment_matches) * 10)

        score = max(0, min(100, score))

        reasons = sorted(
            set(
                strong_service
                + broad_service
                + strong_procurement
                + submission_actions
                + metadata
                + firm_matches
                + (["deadline detected"] if deadline_present else [])
                + (["procurement document"] if document_evidence else [])
            )
        )

        category_scores[rule.category] = score
        category_reasons[rule.category] = reasons

    if not category_scores:
        return ClassificationDecision(
            opportunity=None,
            rejection_reason=(
                "no supported professional-service category matched"
            ),
        )

    best_category = max(category_scores, key=category_scores.get)
    best_score = category_scores[best_category]

    if best_score < MINIMUM_ACCEPTANCE_SCORE:
        return ClassificationDecision(
            opportunity=None,
            rejection_reason=(
                f"best category {best_category!r} scored {best_score}, "
                f"below the acceptance threshold of "
                f"{MINIMUM_ACCEPTANCE_SCORE}"
            ),
        )

    return ClassificationDecision(
        opportunity=FilteredOpportunity(
            organisation_name=opportunity.organisation_name,
            title=opportunity.title,
            category=best_category,
            source_name=opportunity.source_name,
            source_url=opportunity.source_url,
            match_score=best_score,
            match_reason=", ".join(
                category_reasons[best_category]
            ),
            description=opportunity.description,
            deadline=opportunity.deadline,
        ),
    )


def classify_opportunity(
    opportunity: RawOpportunity,
) -> FilteredOpportunity | None:
    return classify_opportunity_with_reason(
        opportunity
    ).opportunity


def filter_opportunity(
    opportunity: RawOpportunity,
) -> FilteredOpportunity | None:
    return classify_opportunity(opportunity)
