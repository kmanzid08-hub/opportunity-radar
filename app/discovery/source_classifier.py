from dataclasses import dataclass


@dataclass
class SourceClassification:
    source_type: str
    confidence_score: float
    reasons: list[str]


POSITIVE_SIGNALS: dict[str, float] = {
    "tender": 20,
    "procurement": 25,
    "request for proposal": 25,
    "request for quotation": 20,
    "expression of interest": 25,
    "terms of reference": 20,
    "consultancy": 15,
    "consulting firm": 20,
    "audit services": 25,
    "accounting services": 25,
    "qualified firms": 15,
    "bid": 10,
    "supplier": 10,
    "opportunities": 10,
}

NEGATIVE_SIGNALS: dict[str, float] = {
    "login required": -20,
    "page not found": -50,
    "access denied": -40,
    "individual consultant only": -10,
}


def classify_source(
    title: str,
    description: str,
    url: str,
) -> SourceClassification:
    searchable_text = " ".join(
        [
            title or "",
            description or "",
            url or "",
        ]
    ).lower()

    score = 0.0
    reasons: list[str] = []

    for signal, points in POSITIVE_SIGNALS.items():
        if signal in searchable_text:
            score += points
            reasons.append(signal)

    for signal, points in NEGATIVE_SIGNALS.items():
        if signal in searchable_text:
            score += points
            reasons.append(signal)

    if ".rw" in url.lower():
        score += 10
        reasons.append("Rwanda domain")

    source_type = determine_source_type(searchable_text)

    return SourceClassification(
        source_type=source_type,
        confidence_score=min(100, max(0, score)),
        reasons=sorted(set(reasons)),
    )


def determine_source_type(searchable_text: str) -> str:
    if any(
        term in searchable_text
        for term in [
            "tender",
            "procurement",
            "request for proposal",
            "expression of interest",
            "request for quotation",
        ]
    ):
        return "Procurement"

    if any(
        term in searchable_text
        for term in [
            "career",
            "vacancy",
            "jobs",
            "employment",
        ]
    ):
        return "Careers"

    if any(
        term in searchable_text
        for term in [
            "opportunities",
            "consultancy",
            "consultancies",
        ]
    ):
        return "Opportunity Platform"

    return "Unknown"