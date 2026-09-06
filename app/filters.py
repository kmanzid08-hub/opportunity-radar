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
    "framework contract",
    "supplier registration",
    "vendor registration",
    "supplier prequalification",
    "vendor prequalification",
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
# NON-OPPORTUNITY SIGNALS
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


STRONG_NOTICE_TITLE_TERMS: tuple[str, ...] = (
    "request for proposal",
    "request for proposals",
    "request for quotation",
    "request for quotations",
    "request for expression of interest",
    "request for expressions of interest",
    "expression of interest",
    "expressions of interest",
    "invitation to bid",
    "invitation for bids",
    "invitation to tender",
    "invitation for tender",
    "call for proposals",
    "call for applications",
    "terms of reference",
    "tender notice",
    "procurement notice",
    "supplier registration",
    "supplier prequalification",
    "vendor prequalification",
    "framework agreement",
    "framework contract",
)

TITLE_NOTICE_PREFIXES: tuple[str, ...] = (
    "tender:",
    "tender ",
    "rfp:",
    "rfp ",
    "rfq:",
    "rfq ",
    "eoi:",
    "eoi ",
    "tor:",
    "tor ",
)

INFORMATIONAL_OR_DIRECTORY_TITLE_TERMS: tuple[str, ...] = (
    "calls for tenders",
    "tenders and procurement opportunities",
    "tender opportunities",
    "tender notices",
    "procurement intelligence services",
    "schedule a free demo",
    "invitation to bid explained",
    "what is a bid",
    "what is an invitation to bid",
    "training course",
    "training program",
    "training programme",
    "course on",
    "career gateways",
    "browse the latest jobs",
    "new job listings",
    "discover your next career move",
    "for businesses",
    "pricing",
)

INFORMATIONAL_URL_TERMS: tuple[str, ...] = (
    "/services",
    "/pricing",
    "/careers",
    "/jobs-list",
    "/training/",
    "/course/",
    "/courses/",
    "/library/",
    "/guides/",
    "/blog/",
    "/country/",
    "/international/",
    "schedule_request_demo",
)

COMMERCIAL_SERVICE_PAGE_TERMS: tuple[str, ...] = (
    "our services",
    "we offer",
    "we provide",
    "our solutions",
    "our expertise",
    "why choose us",
    "contact our team",
    "contact us today",
    "get in touch",
    "book a consultation",
    "request a consultation",
    "learn more about our services",
    "professional services company",
    "leading provider",
    "service provider in",
    "services in",
    "outsourcing services",
    "payroll services in",
    "recruitment services in",
    "tax services in",
    "accounting services in",
    "audit services in",
    "consulting services in",
)

COMMERCIAL_TITLE_PATTERNS: tuple[str, ...] = (
    "services in",
    "company in",
    "agency in",
    "firm in",
    "solutions in",
    "outsourcing",
    "global eor",
    "employer of record",
)

SEO_TITLE_SEPARATORS: tuple[str, ...] = (
    " | ",
    " - ",
    " • ",
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
            "systems integration",
            "data center services",
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
            "technology",
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
# OPPORTUNITY TYPES
# ==========================================================


OPPORTUNITY_TYPE_RULES: dict[str, tuple[str, ...]] = {
    "RFP": (
        "request for proposal",
        "request for proposals",
        "rfp",
    ),

    "RFQ": (
        "request for quotation",
        "request for quotations",
        "rfq",
    ),

    "EOI": (
        "expression of interest",
        "expressions of interest",
        "request for expression of interest",
        "request for expressions of interest",
        "eoi",
    ),

    "Tender": (
        "tender",
        "tender notice",
        "invitation to tender",
        "invitation for tender",
        "invitation to bid",
        "invitation for bids",
        "procurement notice",
        "itb",
        "itt",
    ),

    "Supplier Registration": (
        "supplier registration",
        "vendor registration",
        "supplier prequalification",
        "vendor prequalification",
        "prequalification of suppliers",
        "prequalification of vendors",
        "prequalification notice",
    ),

    "Framework Agreement": (
        "framework agreement",
        "framework contract",
    ),

    "Grant": (
        "grant opportunity",
        "grant opportunities",
        "funding opportunity",
        "funding opportunities",
        "funding call",
        "call for grant proposals",
        "call for applications",
    ),

    "Partnership": (
        "partnership opportunity",
        "partnership opportunities",
        "strategic partnership",
        "collaboration opportunity",
        "joint venture",
    ),

    "Job": (
        "job vacancy",
        "job vacancies",
        "vacancy announcement",
        "career opportunity",
        "employment opportunity",
        "vacant position",
        "apply for this job",
    ),
}


TENDER_LIKE_TYPES: frozenset[str] = frozenset(
    {
        "Tender",
        "RFP",
        "RFQ",
        "EOI",
        "Supplier Registration",
        "Framework Agreement",
    }
)


# ==========================================================
# BUSINESS TERM ALIASES
# ==========================================================


BUSINESS_TERM_ALIASES: dict[str, tuple[str, ...]] = {
    "ict": (
        "ict",
        "information technology",
        "technology",
        "digital",
        "software",
    ),

    "information technology": (
        "information technology",
        "ict",
        "technology",
        "software",
        "digital",
    ),

    "software development": (
        "software development",
        "software engineering",
        "application development",
        "system development",
        "systems development",
    ),

    "cybersecurity": (
        "cybersecurity",
        "cyber security",
        "information security",
        "network security",
    ),

    "construction": (
        "construction",
        "civil works",
        "building works",
        "infrastructure",
    ),

    "engineering": (
        "engineering",
        "engineering services",
        "engineering consultancy",
        "technical engineering",
    ),

    "accounting": (
        "accounting",
        "bookkeeping",
        "financial reporting",
        "finance services",
    ),

    "audit": (
        "audit",
        "auditing",
        "assurance",
        "external audit",
        "internal audit",
    ),

    "tax": (
        "tax",
        "taxation",
        "tax advisory",
        "tax compliance",
    ),

    "consulting": (
        "consulting",
        "consultancy",
        "advisory",
        "technical assistance",
    ),

    "consultancy": (
        "consulting",
        "consultancy",
        "advisory",
        "technical assistance",
    ),

    "training": (
        "training",
        "capacity building",
        "coaching",
        "workshop facilitation",
    ),

    "recruitment": (
        "recruitment",
        "staffing",
        "executive search",
        "headhunting",
    ),

    "marketing": (
        "marketing",
        "advertising",
        "branding",
        "communications",
        "public relations",
    ),

    "logistics": (
        "logistics",
        "transport",
        "freight",
        "shipping",
    ),

    "health": (
        "health",
        "healthcare",
        "medical",
        "hospital",
    ),

    "healthcare": (
        "health",
        "healthcare",
        "medical",
        "hospital",
    ),

    "agriculture": (
        "agriculture",
        "agricultural",
        "farming",
        "farm",
    ),

    "energy": (
        "energy",
        "electricity",
        "solar",
        "renewable energy",
        "power",
    ),
}


COUNTRY_ALIASES: dict[str, tuple[str, ...]] = {
    "rwanda": (
        "rwanda",
        "rwandan",
    ),

    "kenya": (
        "kenya",
        "kenyan",
    ),

    "uganda": (
        "uganda",
        "ugandan",
    ),

    "tanzania": (
        "tanzania",
        "tanzanian",
    ),

    "burundi": (
        "burundi",
        "burundian",
    ),

    "ethiopia": (
        "ethiopia",
        "ethiopian",
    ),

    "ghana": (
        "ghana",
        "ghanaian",
    ),

    "nigeria": (
        "nigeria",
        "nigerian",
    ),

    "south africa": (
        "south africa",
        "south african",
    ),

    "zambia": (
        "zambia",
        "zambian",
    ),

    "zimbabwe": (
        "zimbabwe",
        "zimbabwean",
    ),

    "malawi": (
        "malawi",
        "malawian",
    ),

    "mozambique": (
        "mozambique",
        "mozambican",
    ),

    "botswana": (
        "botswana",
        "botswanan",
    ),

    "namibia": (
        "namibia",
        "namibian",
    ),

    "senegal": (
        "senegal",
        "senegalese",
    ),

    "cameroon": (
        "cameroon",
        "cameroonian",
    ),

    "cote d ivoire": (
        "cote d ivoire",
        "ivory coast",
        "ivorian",
    ),
}


COUNTRY_DOMAIN_HINTS: dict[str, tuple[str, ...]] = {
    "rwanda": (
        ".rw",
    ),

    "kenya": (
        ".ke",
    ),

    "uganda": (
        ".ug",
    ),

    "tanzania": (
        ".tz",
    ),

    "burundi": (
        ".bi",
    ),

    "ethiopia": (
        ".et",
    ),

    "ghana": (
        ".gh",
    ),

    "nigeria": (
        ".ng",
    ),

    "south africa": (
        ".za",
    ),

    "zambia": (
        ".zm",
    ),

    "zimbabwe": (
        ".zw",
    ),

    "malawi": (
        ".mw",
    ),

    "mozambique": (
        ".mz",
    ),

    "botswana": (
        ".bw",
    ),

    "namibia": (
        ".na",
    ),

    "senegal": (
        ".sn",
    ),

    "cameroon": (
        ".cm",
    ),

    "cote d ivoire": (
        ".ci",
    ),
}


REGION_ALIASES: dict[str, tuple[str, ...]] = {
    "east africa": (
        "east africa",
        "eastern africa",
        "east african",
    ),

    "west africa": (
        "west africa",
        "western africa",
        "west african",
    ),

    "southern africa": (
        "southern africa",
        "south african region",
    ),

    "central africa": (
        "central africa",
        "central african",
    ),

    "africa": (
        "africa",
        "african",
    ),

    "east african community": (
        "east african community",
        "eac",
    ),
}


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


def aliases_for_business_term(
    value: str,
) -> tuple[str, ...]:
    clean_value = normalise_text(
        value
    )

    if not clean_value:
        return ()

    aliases = BUSINESS_TERM_ALIASES.get(
        clean_value
    )

    if aliases:
        return aliases

    return (
        clean_value,
    )


def matching_business_terms(
    text: str,
    values: tuple[str, ...],
) -> list[str]:
    matched: list[str] = []

    for configured_value in values:
        clean_value = normalise_text(
            configured_value
        )

        if not clean_value:
            continue

        aliases = aliases_for_business_term(
            clean_value
        )

        if any(
            contains_term(
                text,
                alias,
            )
            for alias in aliases
        ):
            matched.append(
                configured_value
            )

    return sorted(
        set(
            matched
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
        for marker in AGGREGATE_REPEATED_MARKERS
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
    title_notice_matches: list[str],
    title_prefix_match: bool,
    abbreviations: list[str],
    submission_actions: list[str],
    metadata: list[str],
    firm_matches: list[str],
    deadline_present: bool,
    document_evidence: bool,
    generic_page_matches: list[str],
    employment_matches: list[str],
) -> int:
    score = 0

    # Explicit notice language in the title is the strongest evidence that
    # the page is an individual opportunity rather than a generic website.
    score += min(
        55,
        len(title_notice_matches) * 35,
    )

    if title_prefix_match:
        score += 35

    score += min(
        35,
        len(strong_procurement) * 15,
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
        score += 25

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
# OPPORTUNITY TYPE DETECTION
# ==========================================================


def detect_opportunity_types(
    text: str,
) -> set[str]:
    detected: set[str] = set()

    for opportunity_type, terms in (
        OPPORTUNITY_TYPE_RULES.items()
    ):
        if find_matches(
            text,
            terms,
        ):
            detected.add(
                opportunity_type
            )

    return detected


def opportunity_type_summary(
    detected_types: set[str],
) -> str:
    if not detected_types:
        return ""

    priority_order = (
        "RFP",
        "RFQ",
        "EOI",
        "Tender",
        "Supplier Registration",
        "Framework Agreement",
        "Grant",
        "Partnership",
        "Job",
    )

    ordered = [
        item
        for item in priority_order
        if item in detected_types
    ]

    return ", ".join(
        ordered
    )


def informational_or_directory_page_reason(
    opportunity: RawOpportunity,
    *,
    title: str,
    title_notice_matches: list[str],
    title_prefix_match: bool,
    submission_actions: list[str],
    metadata: list[str],
    deadline_present: bool,
    document_evidence: bool,
) -> str | None:
    """
    Reject directories, training pages and explanatory articles that contain
    tender/procurement vocabulary but are not individual live notices.
    """
    raw_url = str(getattr(opportunity, "source_url", "") or "").lower()
    title_info_matches = find_matches(
        title,
        INFORMATIONAL_OR_DIRECTORY_TITLE_TERMS,
    )
    url_info_matches = [
        term
        for term in INFORMATIONAL_URL_TERMS
        if term in raw_url
    ]

    strong_individual_evidence = bool(
        title_notice_matches
        or title_prefix_match
        or submission_actions
        or metadata
        or deadline_present
        or document_evidence
    )

    # Training/course pages are never procurement notices merely because the
    # course teaches procurement, bidding or tendering.
    if any(
        term in title
        for term in (
            "training course",
            "training program",
            "training programme",
            "course on",
        )
    ):
        return "training/course page rather than an opportunity notice"

    # Career and job-index pages should not become procurement opportunities.
    if any(
        term in raw_url
        for term in (
            "/careers",
            "/jobs-list",
        )
    ) and not title_notice_matches:
        return "career/job listing page rather than a procurement opportunity"

    # Generic tender directories and explainer pages require stronger
    # individual-notice evidence than the word tender/RFP alone.
    if title_info_matches and not (
        submission_actions
        or deadline_present
        or document_evidence
    ):
        return (
            "informational/directory page rather than an individual notice "
            f"(indicators: {', '.join(title_info_matches)})"
        )

    if (
        url_info_matches
        and not strong_individual_evidence
    ):
        return (
            "informational/directory URL without individual notice evidence"
        )

    return None


def commercial_service_page_reason(
    opportunity: RawOpportunity,
    *,
    text: str,
    strong_procurement: list[str],
    submission_actions: list[str],
    document_evidence: bool,
    detected_types: set[str],
    deadline_present: bool,
) -> str | None:
    """
    Reject ordinary company/service/SEO pages that happen to contain
    procurement-adjacent vocabulary.

    Real notices are protected by explicit procurement, submission,
    document, deadline or opportunity-type evidence.
    """
    title_raw = str(getattr(opportunity, "title", "") or "")
    title = normalise_text(title_raw)
    description = normalise_text(
        getattr(opportunity, "description", "") or ""
    )

    # Strong real-notice evidence wins.
    if (
        strong_procurement
        or submission_actions
        or document_evidence
        or deadline_present
        or detected_types.intersection(TENDER_LIKE_TYPES)
        or {"Grant", "Partnership"}.intersection(detected_types)
    ):
        return None

    commercial_matches = find_matches(
        text,
        COMMERCIAL_SERVICE_PAGE_TERMS,
    )

    title_marketing_matches = [
        term
        for term in COMMERCIAL_TITLE_PATTERNS
        if contains_term(title, term)
    ]

    seo_separator_count = sum(
        title_raw.count(separator)
        for separator in SEO_TITLE_SEPARATORS
    )

    # Titles listing multiple services are characteristic of landing/SEO pages,
    # not individual procurement notices.
    if (
        seo_separator_count >= 2
        and (
            commercial_matches
            or title_marketing_matches
        )
    ):
        return (
            "commercial/SEO service page detected "
            "(multi-service marketing title without procurement evidence)"
        )

    if title_marketing_matches and not deadline_present:
        return (
            "commercial service page detected "
            f"(title indicators: {', '.join(sorted(set(title_marketing_matches)))})"
        )

    if len(commercial_matches) >= 2 and not deadline_present:
        return (
            "commercial service page detected "
            f"(marketing indicators: {', '.join(commercial_matches[:4])})"
        )

    # Generic descriptive service pages often have long promotional copy
    # without any submission mechanics.
    if (
        len(description) >= 500
        and commercial_matches
        and not submission_actions
    ):
        return (
            "promotional service page detected without bid/proposal "
            "submission instructions"
        )

    return None


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

    detected_types = (
        detect_opportunity_types(
            text
        )
    )

    title_notice_matches = find_matches(
        title,
        STRONG_NOTICE_TITLE_TERMS,
    )

    title_prefix_match = any(
        title.startswith(normalise_text(prefix))
        for prefix in TITLE_NOTICE_PREFIXES
    )

    informational_reason = informational_or_directory_page_reason(
        opportunity,
        title=title,
        title_notice_matches=title_notice_matches,
        title_prefix_match=title_prefix_match,
        submission_actions=submission_actions,
        metadata=metadata,
        deadline_present=deadline_present,
        document_evidence=document_evidence,
    )

    if informational_reason is not None:
        return ClassificationDecision(
            opportunity=None,
            rejection_reason=informational_reason,
        )

    commercial_reason = commercial_service_page_reason(
        opportunity,
        text=text,
        strong_procurement=strong_procurement,
        submission_actions=submission_actions,
        document_evidence=document_evidence,
        detected_types=detected_types,
        deadline_present=deadline_present,
    )

    if commercial_reason is not None:
        return ClassificationDecision(
            opportunity=None,
            rejection_reason=commercial_reason,
        )

    has_procurement_evidence = bool(
        title_notice_matches
        or title_prefix_match
        or strong_procurement
        or submission_actions
        or document_evidence
        or detected_types
        or (
            metadata
            and abbreviations
        )
        or (
            metadata
            and firm_matches
            and deadline_present
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
        for generic_title in GENERIC_TITLES
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

    if (
        employment_matches
        and "Job" not in detected_types
        and not (
            strong_procurement
            or submission_actions
            or document_evidence
            or firm_matches
        )
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
            title_notice_matches=(
                title_notice_matches
            ),
            title_prefix_match=(
                title_prefix_match
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

    if title_notice_matches:
        reason_parts.append(
            "notice title: " + ", ".join(title_notice_matches)
        )

    if title_prefix_match:
        reason_parts.append("explicit opportunity title")

    type_summary = (
        opportunity_type_summary(
            detected_types
        )
    )

    if type_summary:
        reason_parts.append(
            f"type: {type_summary}"
        )

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
# LOCATION MATCHING
# ==========================================================


def match_countries(
    text: str,
    source_url: str,
    countries: tuple[str, ...],
) -> list[str]:
    matched: list[str] = []

    hostname = (
        urlparse(
            source_url or ""
        )
        .hostname
        or ""
    ).lower()

    for configured_country in countries:
        key = normalise_text(
            configured_country
        )

        if not key:
            continue

        aliases = COUNTRY_ALIASES.get(
            key,
            (
                key,
            ),
        )

        literal_match = any(
            contains_term(
                text,
                alias,
            )
            for alias in aliases
        )

        domain_match = any(
            hostname.endswith(
                suffix
            )
            for suffix in COUNTRY_DOMAIN_HINTS.get(
                key,
                (),
            )
        )

        if (
            literal_match
            or domain_match
        ):
            matched.append(
                configured_country
            )

    return sorted(
        set(
            matched
        )
    )


def match_regions(
    text: str,
    regions: tuple[str, ...],
) -> list[str]:
    matched: list[str] = []

    for configured_region in regions:
        key = normalise_text(
            configured_region
        )

        if not key:
            continue

        aliases = REGION_ALIASES.get(
            key,
            (
                key,
            ),
        )

        if any(
            contains_term(
                text,
                alias,
            )
            for alias in aliases
        ):
            matched.append(
                configured_region
            )

    return sorted(
        set(
            matched
        )
    )


# ==========================================================
# BUSINESS OPPORTUNITY TYPE MATCHING
# ==========================================================


def requested_type_matches(
    configured_types: tuple[str, ...],
    detected_types: set[str],
) -> list[str]:
    matched: list[str] = []

    normalized_detected = {
        normalise_text(
            item
        )
        for item in detected_types
    }

    for configured in configured_types:
        clean = normalise_text(
            configured
        )

        if not clean:
            continue

        aliases: set[str] = {
            clean
        }

        if clean in {
            "tender",
            "tenders",
            "procurement",
        }:
            aliases.update(
                normalise_text(
                    item
                )
                for item in TENDER_LIKE_TYPES
            )

        elif clean in {
            "request for proposal",
            "rfp",
        }:
            aliases.add(
                "rfp"
            )

        elif clean in {
            "request for quotation",
            "rfq",
        }:
            aliases.add(
                "rfq"
            )

        elif clean in {
            "expression of interest",
            "eoi",
        }:
            aliases.add(
                "eoi"
            )

        elif clean in {
            "grant",
            "grants",
            "funding",
        }:
            aliases.add(
                "grant"
            )

        elif clean in {
            "partnership",
            "partnerships",
        }:
            aliases.add(
                "partnership"
            )

        elif clean in {
            "job",
            "jobs",
            "career",
            "careers",
        }:
            aliases.add(
                "job"
            )

        elif clean in {
            "supplier",
            "supplier registration",
            "vendor registration",
            "prequalification",
        }:
            aliases.add(
                "supplier registration"
            )

        elif clean in {
            "framework",
            "framework agreement",
        }:
            aliases.add(
                "framework agreement"
            )

        if aliases.intersection(
            normalized_detected
        ):
            matched.append(
                configured
            )

    return sorted(
        set(
            matched
        )
    )


# ==========================================================
# BUSINESS PROFILE MATCHING
# ==========================================================


def score_for_business(
    opportunity: FilteredOpportunity,
    preferences: BusinessMatchPreferences,
) -> BusinessMatchResult:
    """
    Score one valid global opportunity against one business.

    Global procurement confidence and business relevance
    deliberately remain separate concepts.
    """

    text = (
        build_filtered_searchable_text(
            opportunity
        )
    )

    detected_types = (
        detect_opportunity_types(
            text
        )
    )

    # ------------------------------------------------------
    # USER TYPE EXCLUSIONS
    # ------------------------------------------------------

    if (
        opportunity.deadline is None
        and not preferences.include_no_deadline
    ):
        return BusinessMatchResult(
            score=0,
            reason=(
                "Hidden: opportunities without "
                "deadlines are disabled."
            ),
            is_visible=False,
        )

    if (
        "Job" in detected_types
        and not preferences.include_jobs
    ):
        return BusinessMatchResult(
            score=0,
            reason=(
                "Hidden: jobs are disabled."
            ),
            is_visible=False,
        )

    if (
        "Grant" in detected_types
        and not preferences.include_grants
    ):
        return BusinessMatchResult(
            score=0,
            reason=(
                "Hidden: grants are disabled."
            ),
            is_visible=False,
        )

    if (
        "Partnership" in detected_types
        and not preferences.include_partnerships
    ):
        return BusinessMatchResult(
            score=0,
            reason=(
                "Hidden: partnerships are disabled."
            ),
            is_visible=False,
        )

    if (
        detected_types.intersection(
            TENDER_LIKE_TYPES
        )
        and not preferences.include_tenders
    ):
        return BusinessMatchResult(
            score=0,
            reason=(
                "Hidden: tenders and procurement "
                "opportunities are disabled."
            ),
            is_visible=False,
        )

    # ------------------------------------------------------
    # MATCH BUSINESS PROFILE
    # ------------------------------------------------------

    country_matches = match_countries(
        text,
        opportunity.source_url,
        preferences.countries,
    )

    region_matches = match_regions(
        text,
        preferences.regions,
    )

    industry_matches = (
        matching_business_terms(
            text,
            preferences.industries,
        )
    )

    service_matches = (
        matching_business_terms(
            text,
            preferences.services,
        )
    )

    keyword_matches = (
        matching_business_terms(
            text,
            preferences.keywords,
        )
    )

    type_matches = (
        requested_type_matches(
            preferences.opportunity_types,
            detected_types,
        )
    )

    # ------------------------------------------------------
    # CATEGORY MATCHING
    # ------------------------------------------------------

    category_text = normalise_text(
        opportunity.category
    )

    category_matches = (
        matching_business_terms(
            category_text,
            (
                preferences.industries
                + preferences.services
            ),
        )
    )

    # ------------------------------------------------------
    # BUSINESS RELEVANCE SCORE
    # ------------------------------------------------------

    base_quality = int(
        opportunity.match_score
        or 0
    )

    #
    # Global confidence contributes only 15 points.
    # A great procurement notice is not automatically
    # a great match for every business.
    #
    score = round(
        base_quality
        * 0.15
    )

    reasons: list[str] = []

    if country_matches:
        score += min(
            15,
            10
            + (
                len(
                    country_matches
                )
                - 1
            )
            * 3,
        )

        reasons.append(
            "Market match: "
            + ", ".join(
                country_matches
            )
        )

    if region_matches:
        score += min(
            8,
            len(
                region_matches
            )
            * 5,
        )

        reasons.append(
            "Region match: "
            + ", ".join(
                region_matches
            )
        )

    if industry_matches:
        score += min(
            20,
            len(
                industry_matches
            )
            * 12,
        )

        reasons.append(
            "Industry match: "
            + ", ".join(
                industry_matches
            )
        )

    if service_matches:
        score += min(
            35,
            len(
                service_matches
            )
            * 20,
        )

        reasons.append(
            "Service match: "
            + ", ".join(
                service_matches
            )
        )

    if keyword_matches:
        score += min(
            30,
            len(
                keyword_matches
            )
            * 12,
        )

        reasons.append(
            "Keyword match: "
            + ", ".join(
                keyword_matches
            )
        )

    if type_matches:
        score += min(
            15,
            len(
                type_matches
            )
            * 8,
        )

        reasons.append(
            "Opportunity type: "
            + ", ".join(
                type_matches
            )
        )

    if category_matches:
        score += min(
            15,
            len(
                category_matches
            )
            * 10,
        )

        reasons.append(
            "Category match: "
            + ", ".join(
                category_matches
            )
        )

    # ------------------------------------------------------
    # PENALIZE ZERO BUSINESS RELEVANCE
    # ------------------------------------------------------

    business_match_count = sum(
        bool(matches)
        for matches in (
            country_matches,
            region_matches,
            industry_matches,
            service_matches,
            keyword_matches,
            type_matches,
            category_matches,
        )
    )

    if business_match_count == 0:
        score = min(
            score,
            15,
        )

        reasons.append(
            "No Business Profile relevance signals matched."
        )

    elif business_match_count == 1:
        score = min(
            score,
            55,
        )

    # ------------------------------------------------------
    # RESULT
    # ------------------------------------------------------

    score = max(
        0,
        min(
            100,
            score,
        ),
    )

    is_visible = (
        score
        >= preferences.minimum_match_score
    )

    if not is_visible:
        reasons.append(
            "Below Business Profile minimum score "
            f"of {preferences.minimum_match_score}."
        )

    return BusinessMatchResult(
        score=score,
        reason="; ".join(
            reasons
        ),
        is_visible=is_visible,
    )
