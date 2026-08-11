from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from app.schemas import (
    FilteredOpportunity,
    RawOpportunity,
)


# ==========================================================
# RESULT TYPES
# ==========================================================


@dataclass(frozen=True)
class CategoryRule:
    category: str
    strong_terms: tuple[str, ...]
    broad_terms: tuple[str, ...]


@dataclass(frozen=True)
class ClassificationDecision:
    opportunity: FilteredOpportunity | None
    rejection_reason: str | None = None


@dataclass(frozen=True)
class BusinessMatchPreferences:
    """
    Business-specific matching configuration.

    This structure deliberately has no database dependency.
    """

    countries: tuple[str, ...] = ()
    regions: tuple[str, ...] = ()
    industries: tuple[str, ...] = ()
    services: tuple[str, ...] = ()
    opportunity_types: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()

    minimum_match_score: int = 0

    include_no_deadline: bool = True
    include_jobs: bool = True
    include_tenders: bool = True
    include_grants: bool = True
    include_partnerships: bool = True


@dataclass(frozen=True)
class BusinessMatchResult:
    score: int
    reason: str
    is_visible: bool


# ==========================================================
# GLOBAL PROCUREMENT / OPPORTUNITY SIGNALS
# ==========================================================


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
    "solicitation document",
    "bidding document",
    "bid document",
    "framework agreement",
    "supplier registration",
    "vendor registration",
    "prequalification notice",
)


PROCUREMENT_ABBREVIATIONS: tuple[str, ...] = (
    "rfp",
    "rfq",
    "eoi",
    "itb",
    "itt",
    "tor",
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
    "quotations must be submitted",
    "invites eligible firms",
    "invites qualified firms",
    "invites interested firms",
    "interested firms are invited",
    "eligible bidders are invited",
    "qualified bidders are invited",
    "interested bidders are invited",
    "solicits proposals",
    "invites proposals",
    "invites quotations",
    "invites bids",
)


PROCUREMENT_METADATA_TERMS: tuple[str, ...] = (
    "tender number",
    "tender reference",
    "procurement reference",
    "reference number",
    "bid number",
    "rfp number",
    "rfq number",
    "eoi number",
    "submission deadline",
    "proposal deadline",
    "bid deadline",
    "application deadline",
    "quotation deadline",
    "closing date",
    "closing deadline",
    "deadline for submission",
    "date of submission",
    "bid opening",
    "technical proposal",
    "financial proposal",
    "eligibility criteria",
    "evaluation criteria",
    "instructions to bidders",
    "terms of reference",
    "scope of work",
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
    "supplier",
    "suppliers",
    "vendor",
    "vendors",
    "contractor",
    "contractors",
    "qualified bidder",
    "qualified bidders",
    "eligible bidder",
    "eligible bidders",
    "interested bidder",
    "interested bidders",
    "company or firm",
    "companies or firms",
)


# ==========================================================
# NON-OPPORTUNITY / JUNK SIGNALS
# ==========================================================


EMPLOYMENT_ONLY_TERMS: tuple[str, ...] = (
    "full time employee",
    "full-time employee",
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
    "our team",
    "leadership team",
    "contact us",
    "privacy policy",
    "cookie policy",
    "terms and conditions",
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
    "responsibilities",
    "job description",
    "careers",
    "contact us",
    "home",
    "news",
    "blog",
    "welcome",
)


AGGREGATE_LISTING_TERMS: tuple[str, ...] = (
    "advanced search",
    "filter country",
    "list of tenders",
    "no of entries",
    "search results",
    "tender listings",
    "total tenders",
    "showing results",
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
    ".ppt",
    ".pptx",
)


DOCUMENT_PROCUREMENT_HINTS: tuple[str, ...] = (
    "tender",
    "rfp",
    "rfq",
    "eoi",
    "itt",
    "itb",
    "tor",
    "expression of interest",
    "request for proposal",
    "request for quotation",
    "invitation to bid",
    "invitation to tender",
    "procurement",
    "bid document",
    "supplier",
    "prequalification",
)


# ==========================================================
# BROAD SECTOR CLASSIFICATION
# ==========================================================


CATEGORY_RULES: tuple[CategoryRule, ...] = (
    CategoryRule(
        category="ICT & Software",
        strong_terms=(
            "software development",
            "software implementation",
            "information technology services",
            "ict services",
            "digital transformation",
            "cybersecurity services",
            "information security services",
            "cloud services",
            "enterprise resource planning",
            "management information system",
            "website development",
            "mobile application development",
        ),
        broad_terms=(
            "software",
            "information technology",
            "ict",
            "cybersecurity",
            "digital system",
            "database",
            "network infrastructure",
            "server",
            "cloud",
            "erp",
            "mis",
            "website",
        ),
    ),

    CategoryRule(
        category="Construction & Infrastructure",
        strong_terms=(
            "construction works",
            "civil works",
            "building construction",
            "road construction",
            "rehabilitation works",
            "infrastructure works",
            "construction of",
            "renovation works",
        ),
        broad_terms=(
            "construction",
            "civil works",
            "building",
            "road",
            "bridge",
            "infrastructure",
            "renovation",
            "rehabilitation",
            "drainage",
        ),
    ),

    CategoryRule(
        category="Engineering",
        strong_terms=(
            "engineering services",
            "engineering consultancy",
            "design and supervision",
            "technical engineering services",
            "engineering design",
        ),
        broad_terms=(
            "engineering",
            "technical design",
            "supervision works",
            "architectural",
            "structural",
            "mechanical engineering",
            "electrical engineering",
        ),
    ),

    CategoryRule(
        category="Energy",
        strong_terms=(
            "renewable energy",
            "solar energy",
            "solar power",
            "electrical works",
            "power generation",
            "energy efficiency",
            "mini grid",
        ),
        broad_terms=(
            "energy",
            "solar",
            "electricity",
            "electrical",
            "power",
            "renewable",
            "grid",
        ),
    ),

    CategoryRule(
        category="Healthcare",
        strong_terms=(
            "medical equipment",
            "health services",
            "hospital equipment",
            "pharmaceutical supply",
            "medical supplies",
            "healthcare services",
        ),
        broad_terms=(
            "medical",
            "healthcare",
            "hospital",
            "pharmaceutical",
            "health services",
            "laboratory equipment",
            "diagnostic",
        ),
    ),

    CategoryRule(
        category="Agriculture & Food",
        strong_terms=(
            "agricultural services",
            "agricultural inputs",
            "farm equipment",
            "irrigation equipment",
            "food supply",
            "seed supply",
            "fertilizer supply",
        ),
        broad_terms=(
            "agriculture",
            "agricultural",
            "farming",
            "farm",
            "irrigation",
            "livestock",
            "food supply",
            "fertilizer",
            "seed",
        ),
    ),

    CategoryRule(
        category="Logistics & Transport",
        strong_terms=(
            "logistics services",
            "transport services",
            "freight services",
            "fleet management",
            "vehicle rental services",
            "transportation services",
        ),
        broad_terms=(
            "logistics",
            "transport",
            "freight",
            "fleet",
            "vehicle",
            "shipping",
            "courier",
        ),
    ),

    CategoryRule(
        category="Equipment & Supplies",
        strong_terms=(
            "supply of equipment",
            "supply of goods",
            "office equipment",
            "equipment procurement",
            "supply and delivery",
            "supply of materials",
        ),
        broad_terms=(
            "equipment",
            "goods",
            "supplies",
            "materials",
            "furniture",
            "stationery",
            "machinery",
        ),
    ),

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
        ),
    ),

    CategoryRule(
        category="Accounting & Finance",
        strong_terms=(
            "accounting services",
            "bookkeeping services",
            "financial reporting services",
            "preparation of financial statements",
            "outsourced accounting",
            "financial management services",
        ),
        broad_terms=(
            "accounting",
            "bookkeeping",
            "financial reporting",
            "financial management",
            "ifrs",
            "ipsas",
            "finance services",
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
        category="Consulting",
        strong_terms=(
            "consultancy services to conduct",
            "consulting services to conduct",
            "consultancy assignment",
            "technical assistance services",
            "management consultancy",
            "strategy development consultancy",
            "institutional assessment consultancy",
        ),
        broad_terms=(
            "consultancy",
            "consultant",
            "consulting services",
            "technical assistance",
            "institutional assessment",
            "strategy development",
            "advisory services",
        ),
    ),

    CategoryRule(
        category="Research, Monitoring & Evaluation",
        strong_terms=(
            "research services",
            "baseline study",
            "endline evaluation",
            "impact assessment",
            "monitoring and evaluation",
            "data collection services",
            "survey services",
        ),
        broad_terms=(
            "research",
            "baseline",
            "endline",
            "evaluation",
            "impact assessment",
            "monitoring",
            "data collection",
            "survey",
        ),
    ),

    CategoryRule(
        category="Training & Education",
        strong_terms=(
            "provision of training services",
            "capacity building services",
            "training consultancy services",
            "professional training services",
            "education services",
        ),
        broad_terms=(
            "training services",
            "capacity building",
            "workshop facilitation",
            "coaching services",
            "education",
            "curriculum development",
        ),
    ),

    CategoryRule(
        category="Environmental Services",
        strong_terms=(
            "environmental impact assessment",
            "environmental and social impact assessment",
            "environmental audit services",
            "environmental consultancy services",
            "waste management services",
        ),
        broad_terms=(
            "environmental",
            "environment",
            "esia",
            "esmp",
            "social safeguards",
            "waste management",
            "climate",
        ),
    ),

    CategoryRule(
        category="Marketing & Communications",
        strong_terms=(
            "communications services",
            "marketing services",
            "public relations services",
            "media services",
            "graphic design services",
            "advertising services",
        ),
        broad_terms=(
            "communications",
            "marketing",
            "public relations",
            "media",
            "graphic design",
            "advertising",
            "branding",
        ),
    ),

    CategoryRule(
        category="Security & Facilities",
        strong_terms=(
            "security services",
            "facility management services",
            "cleaning services",
            "maintenance services",
            "guarding services",
        ),
        broad_terms=(
            "security",
            "facility management",
            "cleaning",
            "maintenance",
            "guarding",
            "janitorial",
        ),
    ),

    CategoryRule(
        category="Human Resources & Recruitment",
        strong_terms=(
            "recruitment services",
            "staff recruitment services",
            "executive search services",
            "human resources consultancy",
            "staffing services",
        ),
        broad_terms=(
            "recruitment firm",
            "executive search",
            "headhunting",
            "staff selection",
            "human resources",
            "staffing",
        ),
    ),
)


# ==========================================================
# OPPORTUNITY TYPE DETECTION
# ==========================================================


TENDER_TYPE_TERMS: tuple[str, ...] = (
    "tender",
    "procurement",
    "request for proposal",
    "request for quotation",
    "rfp",
    "rfq",
    "eoi",
    "expression of interest",
    "invitation to bid",
    "invitation to tender",
)


GRANT_TYPE_TERMS: tuple[str, ...] = (
    "grant",
    "funding opportunity",
    "funding call",
)


PARTNERSHIP_TYPE_TERMS: tuple[str, ...] = (
    "partnership",
    "strategic partnership",
    "collaboration",
    "joint venture",
)


JOB_TYPE_TERMS: tuple[str, ...] = (
    "job",
    "vacancy",
    "career opportunity",
    "employment",
    "vacant position",
)


# ==========================================================
# SCORING
# ==========================================================


MINIMUM_ACCEPTANCE_SCORE = 45


# ==========================================================
# TEXT HELPERS
# ==========================================================


def normalise_text(
    value: str | None,
) -> str:
    if not value:
        return ""

    text = value.lower()

    text = text.replace(
        "&",
        " and ",
    )

    text = re.sub(
        r"[-_/|]",
        " ",
        text,
    )

    text = re.sub(
        r"[^a-z0-9\s.]",
        " ",
        text,
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


def contains_term(
    text: str,
    term: str,
) -> bool:
    cleaned = normalise_text(
        term
    )

    if not cleaned:
        return False

    pattern = (
        r"(?<![a-z0-9])"
        + re.escape(
            cleaned
        ).replace(
            r"\ ",
            r"\s+",
        )
        + r"(?![a-z0-9])"
    )

    return (
        re.search(
            pattern,
            text,
        )
        is not None
    )


def find_matches(
    text: str,
    terms: tuple[str, ...],
) -> list[str]:
    return sorted(
        {
            term
            for term in terms
            if contains_term(
                text,
                term,
            )
        }
    )


def build_searchable_text(
    opportunity: RawOpportunity,
) -> str:
    values = (
        getattr(
            opportunity,
            "title",
            "",
        ),
        getattr(
            opportunity,
            "description",
            "",
        ),
        getattr(
            opportunity,
            "organisation_name",
            "",
        ),
        getattr(
            opportunity,
            "source_name",
            "",
        ),
        getattr(
            opportunity,
            "source_url",
            "",
        ),
    )

    return normalise_text(
        " ".join(
            value or ""
            for value in values
        )
    )


def build_filtered_searchable_text(
    opportunity: FilteredOpportunity,
) -> str:
    values = (
        opportunity.title,
        opportunity.description,
        opportunity.organisation_name,
        opportunity.category,
        opportunity.source_name,
        opportunity.source_url,
    )

    return normalise_text(
        " ".join(
            value or ""
            for value in values
        )
    )


# ==========================================================
# DOCUMENT DETECTION
# ==========================================================


def is_procurement_document(
    opportunity: RawOpportunity,
) -> bool:
    source_url = str(
        getattr(
            opportunity,
            "source_url",
            "",
        )
        or ""
    )

    path = urlparse(
        source_url
    ).path.lower()

    if not path.endswith(
        DOCUMENT_EXTENSIONS
    ):
        return False

    document_text = normalise_text(
        f"{getattr(opportunity, 'title', '')} {path}"
    )

    return bool(
        find_matches(
            document_text,
            DOCUMENT_PROCUREMENT_HINTS,
        )
    )


# ==========================================================
# AGGREGATE PAGE DETECTION
# ==========================================================


def aggregate_listing_reason(
    opportunity: RawOpportunity,
) -> str | None:
    title = normalise_text(
        getattr(
            opportunity,
            "title",
            "",
        )
    )

    description = normalise_text(
        getattr(
            opportunity,
            "description",
            "",
        )
    )

    combined = (
        f"{title} {description}"
        .strip()
    )

    repeated_markers = sorted(
        marker
        for marker
        in AGGREGATE_REPEATED_MARKERS
        if description.count(
            marker
        ) >= 2
    )

    aggregate_terms = find_matches(
        combined,
        AGGREGATE_LISTING_TERMS,
    )

    if len(
        repeated_markers
    ) >= 2:
        return (
            "aggregate listing page contains multiple notices "
            f"(repeated markers: {', '.join(repeated_markers)})"
        )

    if (
        len(
            aggregate_terms
        ) >= 2
        and len(
            description
        ) >= 1_000
    ):
        return (
            "aggregate listing/search page was detected "
            f"(indicators: {', '.join(aggregate_terms)})"
        )

    return None


# ==========================================================
# GLOBAL OPPORTUNITY QUALITY
# ==========================================================


def calculate_opportunity_confidence(
    *,
    strong_procurement: list[str],
    abbreviations: list[str],
    submission_actions: list[str],
    metadata: list[str],
    firm_matches: list[str],
    deadline_present: bool,
    document_evidence: bool,
    generic_page_matches: list[str],
    employment_matches: list[str],
) -> int:
    """
    Determine whether this appears to be a real opportunity.

    This score is intentionally independent of the sector
    or type of business that may eventually pursue it.
    """

    score = 0

    score += min(
        40,
        len(
            strong_procurement
        )
        * 20,
    )

    score += min(
        20,
        len(
            submission_actions
        )
        * 10,
    )

    score += min(
        15,
        len(
            metadata
        )
        * 5,
    )

    score += min(
        10,
        len(
            firm_matches
        )
        * 4,
    )

    score += min(
        10,
        len(
            abbreviations
        )
        * 4,
    )

    if deadline_present:
        score += 10

    if document_evidence:
        score += 20

    if generic_page_matches:
        score -= min(
            35,
            len(
                generic_page_matches
            )
            * 12,
        )

    if employment_matches:
        score -= min(
            35,
            len(
                employment_matches
            )
            * 12,
        )

    return max(
        0,
        min(
            100,
            score,
        ),
    )


# ==========================================================
# CATEGORY DETECTION
# ==========================================================


def classify_category(
    text: str,
) -> tuple[
    str,
    list[str],
]:
    category_scores: dict[
        str,
        int,
    ] = {}

    category_reasons: dict[
        str,
        list[str],
    ] = {}

    for rule in CATEGORY_RULES:
        strong_matches = find_matches(
            text,
            rule.strong_terms,
        )

        broad_matches = find_matches(
            text,
            rule.broad_terms,
        )

        if (
            not strong_matches
            and not broad_matches
        ):
            continue

        score = 0

        score += min(
            60,
            len(
                strong_matches
            )
            * 30,
        )

        score += min(
            40,
            len(
                broad_matches
            )
            * 10,
        )

        category_scores[
            rule.category
        ] = score

        category_reasons[
            rule.category
        ] = sorted(
            set(
                strong_matches
                + broad_matches
            )
        )

    if not category_scores:
        return (
            "General Procurement",
            [],
        )

    best_category = max(
        category_scores,
        key=category_scores.get,
    )

    return (
        best_category,
        category_reasons[
            best_category
        ],
    )


# ==========================================================
# GLOBAL CLASSIFIER
# ==========================================================


def classify_opportunity_with_reason(
    opportunity: RawOpportunity,
) -> ClassificationDecision:
    text = build_searchable_text(
        opportunity
    )

    title = normalise_text(
        getattr(
            opportunity,
            "title",
            "",
        )
    )

    if not title:
        return ClassificationDecision(
            opportunity=None,
            rejection_reason=(
                "title is missing or empty"
            ),
        )

    if not text:
        return ClassificationDecision(
            opportunity=None,
            rejection_reason=(
                "no searchable opportunity content"
            ),
        )

    aggregate_reason = (
        aggregate_listing_reason(
            opportunity
        )
    )

    if aggregate_reason is not None:
        return ClassificationDecision(
            opportunity=None,
            rejection_reason=(
                aggregate_reason
            ),
        )

    strong_procurement = find_matches(
        text,
        STRONG_PROCUREMENT_TERMS,
    )

    abbreviations = find_matches(
        text,
        PROCUREMENT_ABBREVIATIONS,
    )

    submission_actions = find_matches(
        text,
        SUBMISSION_ACTION_TERMS,
    )

    metadata = find_matches(
        text,
        PROCUREMENT_METADATA_TERMS,
    )

    firm_matches = find_matches(
        text,
        FIRM_INDICATORS,
    )

    employment_matches = find_matches(
        text,
        EMPLOYMENT_ONLY_TERMS,
    )

    generic_page_matches = find_matches(
        text,
        GENERIC_PAGE_TERMS,
    )

    deadline_present = (
        getattr(
            opportunity,
            "deadline",
            None,
        )
        is not None
    )

    document_evidence = (
        is_procurement_document(
            opportunity
        )
    )

    has_procurement_evidence = bool(
        strong_procurement
        or submission_actions
        or document_evidence
        or (
            metadata
            and (
                firm_matches
                or abbreviations
            )
        )
        or (
            deadline_present
            and (
                metadata
                or firm_matches
                or abbreviations
            )
        )
    )

    if not has_procurement_evidence:
        return ClassificationDecision(
            opportunity=None,
            rejection_reason=(
                "no credible procurement or opportunity "
                "evidence was detected"
            ),
        )

    title_is_generic = any(
        title
        == normalise_text(
            generic_title
        )
        for generic_title
        in GENERIC_TITLES
    )

    if (
        title_is_generic
        and not (
            strong_procurement
            and (
                deadline_present
                or metadata
                or submission_actions
            )
        )
    ):
        return ClassificationDecision(
            opportunity=None,
            rejection_reason=(
                "generic page title without enough "
                "opportunity evidence"
            ),
        )

    if employment_matches and not (
        strong_procurement
        or submission_actions
        or document_evidence
        or firm_matches
    ):
        return ClassificationDecision(
            opportunity=None,
            rejection_reason=(
                "ordinary employment listing without "
                "procurement evidence"
            ),
        )

    confidence_score = (
        calculate_opportunity_confidence(
            strong_procurement=(
                strong_procurement
            ),
            abbreviations=(
                abbreviations
            ),
            submission_actions=(
                submission_actions
            ),
            metadata=(
                metadata
            ),
            firm_matches=(
                firm_matches
            ),
            deadline_present=(
                deadline_present
            ),
            document_evidence=(
                document_evidence
            ),
            generic_page_matches=(
                generic_page_matches
            ),
            employment_matches=(
                employment_matches
            ),
        )
    )

    if (
        confidence_score
        < MINIMUM_ACCEPTANCE_SCORE
    ):
        return ClassificationDecision(
            opportunity=None,
            rejection_reason=(
                "opportunity confidence score "
                f"{confidence_score} is below "
                f"{MINIMUM_ACCEPTANCE_SCORE}"
            ),
        )

    category, category_matches = (
        classify_category(
            text
        )
    )

    reason_parts: list[str] = []

    reason_parts.extend(
        strong_procurement
    )

    reason_parts.extend(
        submission_actions
    )

    reason_parts.extend(
        metadata
    )

    reason_parts.extend(
        firm_matches
    )

    reason_parts.extend(
        category_matches
    )

    if deadline_present:
        reason_parts.append(
            "deadline detected"
        )

    if document_evidence:
        reason_parts.append(
            "procurement document"
        )

    reasons = sorted(
        set(
            reason_parts
        )
    )

    return ClassificationDecision(
        opportunity=FilteredOpportunity(
            organisation_name=(
                opportunity.organisation_name
            ),
            title=(
                opportunity.title
            ),
            category=(
                category
            ),
            source_name=(
                opportunity.source_name
            ),
            source_url=(
                opportunity.source_url
            ),
            match_score=(
                confidence_score
            ),
            match_reason=", ".join(
                reasons
            ),
            description=(
                opportunity.description
            ),
            deadline=(
                opportunity.deadline
            ),
        ),
    )


def classify_opportunity(
    opportunity: RawOpportunity,
) -> FilteredOpportunity | None:
    return (
        classify_opportunity_with_reason(
            opportunity
        )
        .opportunity
    )


def filter_opportunity(
    opportunity: RawOpportunity,
) -> FilteredOpportunity | None:
    return classify_opportunity(
        opportunity
    )


# ==========================================================
# BUSINESS PROFILE MATCHING
# ==========================================================


def _matching_terms(
    text: str,
    values: tuple[str, ...],
) -> list[str]:
    return sorted(
        {
            value
            for value in values
            if (
                value
                and contains_term(
                    text,
                    value,
                )
            )
        }
    )


def detect_opportunity_types(
    text: str,
) -> set[str]:
    detected: set[str] = set()

    if find_matches(
        text,
        TENDER_TYPE_TERMS,
    ):
        detected.add(
            "tender"
        )

    if find_matches(
        text,
        GRANT_TYPE_TERMS,
    ):
        detected.add(
            "grant"
        )

    if find_matches(
        text,
        PARTNERSHIP_TYPE_TERMS,
    ):
        detected.add(
            "partnership"
        )

    if find_matches(
        text,
        JOB_TYPE_TERMS,
    ):
        detected.add(
            "job"
        )

    return detected


def score_for_business(
    opportunity: FilteredOpportunity,
    preferences: BusinessMatchPreferences,
) -> BusinessMatchResult:
    """
    Produce the business-specific relevance score.

    The stored/global match_score represents opportunity
    quality. This score adds relevance for one business.
    """

    text = (
        build_filtered_searchable_text(
            opportunity
        )
    )

    base_score = int(
        opportunity.match_score
        or 0
    )

    #
    # Do not let high global procurement confidence
    # automatically become a high business relevance score.
    #
    # Up to 40 points come from opportunity quality.
    #
    score = round(
        base_score
        * 0.40
    )

    reasons: list[str] = []

    # ------------------------------------------------------
    # TYPE EXCLUSIONS
    # ------------------------------------------------------

    detected_types = (
        detect_opportunity_types(
            text
        )
    )

    if (
        opportunity.deadline is None
        and not preferences.include_no_deadline
    ):
        return BusinessMatchResult(
            score=0,
            reason=(
                "Hidden because opportunities without "
                "deadlines are disabled."
            ),
            is_visible=False,
        )

    if (
        "job" in detected_types
        and not preferences.include_jobs
    ):
        return BusinessMatchResult(
            score=0,
            reason=(
                "Hidden because jobs are disabled."
            ),
            is_visible=False,
        )

    if (
        "grant" in detected_types
        and not preferences.include_grants
    ):
        return BusinessMatchResult(
            score=0,
            reason=(
                "Hidden because grants are disabled."
            ),
            is_visible=False,
        )

    if (
        "partnership" in detected_types
        and not preferences.include_partnerships
    ):
        return BusinessMatchResult(
            score=0,
            reason=(
                "Hidden because partnerships are disabled."
            ),
            is_visible=False,
        )

    if (
        "tender" in detected_types
        and not preferences.include_tenders
    ):
        return BusinessMatchResult(
            score=0,
            reason=(
                "Hidden because tenders are disabled."
            ),
            is_visible=False,
        )

    # ------------------------------------------------------
    # BUSINESS RELEVANCE
    # ------------------------------------------------------

    countries = _matching_terms(
        text,
        preferences.countries,
    )

    regions = _matching_terms(
        text,
        preferences.regions,
    )

    industries = _matching_terms(
        text,
        preferences.industries,
    )

    services = _matching_terms(
        text,
        preferences.services,
    )

    keywords = _matching_terms(
        text,
        preferences.keywords,
    )

    requested_types = (
        _matching_terms(
            text,
            preferences.opportunity_types,
        )
    )

    if countries:
        score += min(
            10,
            6
            + (
                len(countries)
                - 1
            )
            * 2,
        )

        reasons.append(
            "country: "
            + ", ".join(
                countries
            )
        )

    if regions:
        score += min(
            5,
            len(
                regions
            )
            * 3,
        )

        reasons.append(
            "region: "
            + ", ".join(
                regions
            )
        )

    if industries:
        score += min(
            15,
            len(
                industries
            )
            * 8,
        )

        reasons.append(
            "industry: "
            + ", ".join(
                industries
            )
        )

    if services:
        score += min(
            30,
            len(
                services
            )
            * 15,
        )

        reasons.append(
            "service: "
            + ", ".join(
                services
            )
        )

    if keywords:
        score += min(
            30,
            len(
                keywords
            )
            * 10,
        )

        reasons.append(
            "keyword: "
            + ", ".join(
                keywords
            )
        )

    if requested_types:
        score += min(
            10,
            len(
                requested_types
            )
            * 5,
        )

        reasons.append(
            "opportunity type: "
            + ", ".join(
                requested_types
            )
        )

    # ------------------------------------------------------
    # CATEGORY SIGNAL
    # ------------------------------------------------------

    category_text = normalise_text(
        opportunity.category
    )

    category_business_terms = (
        preferences.industries
        + preferences.services
    )

    category_matches = [
        term
        for term
        in category_business_terms
        if contains_term(
            category_text,
            term,
        )
    ]

    if category_matches:
        score += min(
            15,
            len(
                category_matches
            )
            * 8,
        )

        reasons.append(
            "category: "
            + ", ".join(
                category_matches
            )
        )

    score = max(
        0,
        min(
            100,
            score,
        ),
    )

    if not reasons:
        reasons.append(
            "No strong business-profile relevance "
            "signals matched."
        )

    is_visible = (
        score
        >= preferences.minimum_match_score
    )

    if not is_visible:
        reasons.append(
            "Below business minimum score "
            f"of {preferences.minimum_match_score}."
        )

    return BusinessMatchResult(
        score=score,
        reason="; ".join(
            reasons
        ),
        is_visible=is_visible,
    )
