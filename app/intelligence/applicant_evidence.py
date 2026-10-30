"""Conservative English applicant-clause recognizer, not general NLP.

Only complete, positive invitation/permission clauses establish legal bidder
types. Personnel references and unrecognized wording remain ambiguous. This
module deliberately has no dependency on response models or AI clients.
"""

import re


TYPE_PHRASES = {
    "INDIVIDUAL": r"(?:individual consultants?|individuals?)",
    "CONSULTING_FIRM": r"(?:(?:consulting|consultancy) )?firms?",
    "JOINT_VENTURE": r"(?:joint ventures?|jvs?)",
    "NGO": r"(?:ngos?|non-governmental organizations?)",
    "OTHER_ORGANIZATION": r"organizations?",
}
_TYPE = "(?:" + "|".join(TYPE_PHRASES.values()) + ")"
_ACTOR = r"(?:(?:qualified|registered|eligible|a|an) )*" + _TYPE
_ACTORS = _ACTOR + r"(?:\s*(?:,\s*(?:and |or )?| and | or | & )" + _ACTOR + r")*"
_GROUP = r"(?P<actors>" + _ACTORS + r")"
_ACTION = (
    r"(?:apply|bid|submit(?: (?:competitive )?(?:proposals?|applications?|bids?|expressions? of interest))?)"
)
_PATTERNS = [
    r"(?:only )?"
    + _GROUP
    + r"(?: registered in [a-z]+(?: [a-z]+){0,3})? (?:may|can|are eligible to|are invited to) "
    + _ACTION,
    _GROUP + r" (?:are eligible|are invited)",
    r"(?:[a-z]+(?: [a-z]+){0,5} )?invites? " + _GROUP + r" to " + _ACTION,
    r"eligible bidders must be " + _GROUP,
    r"applications are open only to " + _GROUP,
    _GROUP + r" only",
    r"recruitment of (?P<actors>(?:a |qualified )?(?:consulting|consultancy) firm) to .+",
]
_TYPED_ACTOR = re.compile(
    "|".join(
        r"(?P<" + kind + ">" + phrase + r")" for kind, phrase in TYPE_PHRASES.items()
    )
)


def direct_applicant_types(quotes: list[str]) -> set[str]:
    """Return only types supported by a complete recognized applicant clause.

    Do not combine a subject in one sentence with a permission in another. Reject
    negation, alternatives outside the recognized actor list and partial matches.
    """
    supported: set[str] = set()
    for quote in quotes:
        if len(quote) > 500:
            continue  # Require a short, reviewable applicant clause.
        text = " ".join(quote.casefold().split()).strip(" .:;\"'")
        text = text.removeprefix("accordingly, ")
        if re.search(r"\b(?:not|never|except|unless|excluding|if|no)\b", text):
            continue
        # Recognize each applicant proposition independently, never join a
        # personnel subject to a permission in another sentence. The suffix can
        # describe purpose/location/deadline but cannot add applicant classes or
        # exceptions. Keep original quotations unchanged for evidence/audit.
        for clause in re.split(r"[.!?;]\s*", text):
            clause = clause.strip(" ,:\"'()")
            for pattern in _PATTERNS:
                match = re.match(pattern + r"(?=$|[\s,(])", clause)
                if not match:
                    continue
                suffix = clause[match.end():].strip(" ,()")
                if suffix and (
                    not re.match(
                        r"(?:to (?:undertake|conduct|perform|deliver|carry out|support|provide)\b|for\b|in\b|by\b|before\b|on\b|at\b|which\b|with\b)",
                        suffix,
                    )
                    or re.search(r"\b(?:or|but|provided|subject to|behalf|may|eligible|apply|bidders?|applicants?)\b", suffix)
                ):
                    continue
                supported.update(
                    found.lastgroup
                    for found in _TYPED_ACTOR.finditer(match.group("actors"))
                    if found.lastgroup
                )
    return supported


def direct_unrestricted(quotes: list[str]) -> bool:
    return any(
        re.fullmatch(
            r"(?:no applicant-type restrictions apply|all applicant types (?:are )?(?:permitted|accepted)|applicant types are unrestricted)",
            " ".join(quote.casefold().split()).strip(" ."),
        )
        for quote in quotes
    )


def capability_only(text: str) -> str:
    """Keep capability prose separate from applicant-compatibility assertions.

    This is a narrowly scoped field contract, not a keyword fit classifier. Sentences
    about applicant eligibility belong in the deterministic eligibility display, not
    in AI capability prose. Scores and unrelated technical content are untouched.
    """
    sentences = re.split(r"(?<=[.!?])\s+|\n+", text.strip())
    applicant_language = re.compile(
        r"\b(?:ineligible|eligible|bidders?|applicants?)\b"
        r"|\b(?:satisf\w*|meet\w*|fulfil\w*|confirm\w*)\b[^.!?]{0,80}\beligibility\b"
        r"|\beligibility\b[^.!?]{0,60}\b(?:satisfied|confirmed|met|established)\b"
        r"|\b(?:firms?|companies|organizations?|individuals?|consultants?)\s+"
        r"(?:may|can|cannot|must not)\s+(?:apply|bid|submit)\b"
        # Specific applicant-requirement assertions, not a ban on firm/consultant
        # words: technical collaboration and experience prose remain untouched.
        r"|\b(?:requires?|invites?|recruitment of|rfp for)\s+(?:a |an |qualified )?"
        r"(?:(?:consulting|consultancy) firms?|individual consultants?)\b"
        r"|\b(?:(?:consulting|consultancy) firms?|individual consultants?)\s+(?:is|are)\s+required\b",
        re.IGNORECASE,
    )
    return " ".join(
        sentence for sentence in sentences if not applicant_language.search(sentence)
    )
