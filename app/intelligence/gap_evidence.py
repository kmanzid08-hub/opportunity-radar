"""Bounded company-gap language policy, not a scorer or source-text rewriter."""

from collections.abc import Mapping
from dataclasses import dataclass
import re

from app.intelligence.schemas import CompanyMatch


_SUBJECT = r"(?:(?:the |this )?(?:company|firm|organization) (?:has |possesses )?)?"
_TOPIC = r"(?:a |an |the )?(?:required |specialized |specialist |relevant |prior |international |overseas |delivery )*(?:expertise|experience|presence|offices?|capabilit(?:y|ies)|certifications?|certificates?|registration|track record)\b[^.!?;]*"
_ABSENCE = re.compile(
    r"^"
    + _SUBJECT
    + r"(?:no |(?:has |is )?an? absence of |absence of |lack of |lacks? )(?P<topic>"
    + _TOPIC
    + r")$",
    re.I,
)
_SERVICE = re.compile(r"^" + _SUBJECT + r"does not provide (?P<topic>[^.!?;]+)$", re.I)
_MARKET = re.compile(
    r"^" + _SUBJECT + r"does not operate in (?P<topic>[^.!?;]+)$", re.I
)
_LIMIT = re.compile(
    r"^"
    + _SUBJECT
    + r"(?P<kind>(?:delivery )?(?:experience|footprint)|operations) (?:is|are) (?:limited to|concentrated in|restricted to) (?P<topic>[^,.!?;]+)(?P<tail>,.*)?$",
    re.I,
)


def _key(value: str) -> str:
    return " ".join(value.casefold().strip(" .").split())


def _claim(text: str) -> tuple[str, str] | None:
    for kind, pattern in (
        ("absence", _ABSENCE),
        ("service", _SERVICE),
        ("market", _MARKET),
        ("limit", _LIMIT),
    ):
        if match := pattern.fullmatch(text.strip(" .")):
            topic = _key(match.group("topic"))
            # A restriction on current operations does not establish a limit on
            # historical experience (or vice versa).
            if kind == "limit":
                topic = _key(match.group("kind")) + ":" + topic
            return kind, topic
    return None


def _strings(value: object):
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for child in value.values():
            yield from _strings(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _strings(child)


def _explicit_negative(claim: tuple[str, str], profile: Mapping[str, object]) -> bool:
    # Preferences/base country do not establish negative facts. Preserve an
    # explicit negative proposition from supplied factual text, not word overlap.
    for field in (
        "description",
        "experience",
        "capabilities",
        "areas_of_expertise",
        "services",
    ):
        for text in _strings(profile.get(field)):
            if any(
                _claim(part) == claim for part in re.split(r"(?<=[.!?;])\s+|\n+", text)
            ):
                return True
    exclusions = profile.get("exclusions")
    if isinstance(exclusions, Mapping):
        key = {"service": "services", "market": "countries"}.get(claim[0])
        if key and any(
            _key(value) == claim[1] for value in _strings(exclusions.get(key))
        ):
            return True
    return False


@dataclass(frozen=True)
class _GeographicClaim:
    domain: str  # Current operations are not historical delivery.
    relation: str
    location: str


_GEO_OWNER = (
    r"(?:(?:the |this )?(?:company|firm|organization)(?:['’]s)? |its |our |their )?"
)
_GEO_WORK = r"(?P<work>(?:(?:primary|past|project|delivery) )*(?:delivery|experience|footprint|track record|operations|work))"
_GEO_PLACE = r"(?P<place>[^,.!?;]+?)"
_GEO_TAIL = r"(?: rather than [^.!?;]+|, [^.!?;]+)?"
_GEO_PATTERNS = [
    # Bounded sentence shapes, not a general rewrite of arbitrary prose.
    re.compile(pattern, re.I)
    for pattern in (
        _GEO_OWNER
        + _GEO_WORK
        + r" (?:is|are|has been|have been) (?:explicitly )?"
        + r"(?P<relation>concentrated (?:in|within)|(?:primarily|mainly|predominantly|largely) (?:in|within)|limited to|restricted to|only in|exclusively in) "
        + _GEO_PLACE
        + _GEO_TAIL,
        _GEO_OWNER
        + _GEO_WORK
        + r" (?:is|are) (?P<relation>primarily|mainly|predominantly|largely|only) "
        + _GEO_PLACE
        + r"-based"
        + _GEO_TAIL,
        _GEO_OWNER
        + _GEO_WORK
        + r" (?:is|are) (?P<relation>primarily|mainly|predominantly|largely|only) (?P<place>domestic)"
        + _GEO_TAIL,
        r"most (?P<work>experience|projects|past assignments) (?:is|are|have been delivered|were delivered) (?:in )?"
        + _GEO_PLACE,
        _GEO_OWNER
        + r"(?:has |possesses )(?P<relation>primarily|mainly|predominantly|only) (?P<place>domestic) (?P<work>experience)",
        _GEO_OWNER
        + r"(?:has |possesses )(?P<place>domestic)-(?P<relation>only) (?P<work>experience)",
        r"(?:(?:the |this )?(?:company|firm|organization) |we |it )?"
        + r"(?:(?P<before>only) )?operates? (?:(?P<after>only|mainly|primarily|predominantly|largely|exclusively) )?(?:in|within) "
        + _GEO_PLACE,
        r"(?:(?:the |this )?(?:company|firm|organization) |we |it )?(?:does|do) not undertake assignments outside "
        + _GEO_PLACE,
    )
]
_GEO_EXTENT = r"(?:international|overseas|foreign|regional|multi-country|[a-z]+-wide)"
_GEO_POSITIVE = re.compile(
    _GEO_OWNER
    + r"(?:(?:has|demonstrates|possesses) )?"
    + r"(?:demonstrated|proven|strong|extensive|documented) (?P<extent>"
    + _GEO_EXTENT
    + r") (?:delivery(?: experience)?|experience|track record)",
    re.I,
)
_GEO_MISSING = re.compile(
    r"(?:(?:the )?profile shows |"
    + _GEO_OWNER
    + r"(?:has |shows |lacks )?)"
    + r"(?P<relation>limited |no |lack of |absence of )?(?P<extent>"
    + _GEO_EXTENT
    + r") (?:delivery(?: experience)?|experience|track record)",
    re.I,
)


def _geographic_claim(text: str) -> _GeographicClaim | None:
    core = text.strip().rstrip(".;")
    if len(core) > 1000:
        return None
    if match := _GEO_POSITIVE.fullmatch(core):
        return _GeographicClaim("delivery", "positive", _key(match["extent"]))
    if match := _GEO_MISSING.fullmatch(core):
        # Bare 'international experience' is not an assertion of absence.
        if match["relation"] or re.search(r"\blacks\b", core, re.I):
            absence_relation = (
                "limited"
                if (match["relation"] or "").casefold().strip() == "limited"
                else "absent"
            )
            return _GeographicClaim("delivery", absence_relation, _key(match["extent"]))
    for pattern in _GEO_PATTERNS:
        match = pattern.fullmatch(core)
        if match is None:
            continue
        groups = match.groupdict()
        work = groups.get("work")
        domain = (
            "operations"
            if work is None or work.casefold() == "operations"
            else "delivery"
        )
        relation = groups.get("relation") or groups.get("before") or groups.get("after")
        if work is None and relation is None and "not undertake" not in core.casefold():
            continue  # 'operates in X' does not imply 'only in X'.
        exclusive = relation is not None and any(
            token in relation.casefold()
            for token in ("only", "exclusively", "limited", "restricted")
        )
        if "not undertake" in core.casefold():
            exclusive = True
        return _GeographicClaim(
            domain, "only" if exclusive else "concentrated", _key(match["place"])
        )
    return None


def _geographic_evidence_supports(
    claim: _GeographicClaim, profile: Mapping[str, object]
) -> bool:
    geography = profile.get("geography")
    base = geography.get("base_country") if isinstance(geography, Mapping) else None

    def location(value: str) -> str:
        # Resolve 'domestic' only for comparing explicit facts, never as evidence.
        return _key(base) if value == "domestic" and isinstance(base, str) else value

    # Preferences, willingness and project-list omissions are not proof. Factual
    # fields must contain an explicit assertion, not just a service/country name.
    fields = (
        ("experience",)
        if claim.relation == "positive"
        else (
            "description",
            "experience",
            "capabilities",
            "areas_of_expertise",
            "services",
        )
    )
    for field in fields:
        for text in _strings(profile.get(field)):
            for sentence in re.split(r"(?<=[.!?;])\s+|\n+", text):
                evidence = _geographic_claim(sentence)
                if evidence is None or evidence.domain != claim.domain:
                    continue
                if location(evidence.location) != location(claim.location):
                    continue
                if evidence.relation == claim.relation or (
                    evidence.relation == "only" and claim.relation == "concentrated"
                ):
                    return True
    # Two demonstrated countries establish multi-country delivery, not Africa-wide
    # coverage, a majority distribution, or a complete account of all past work.
    if (
        claim.relation == "positive"
        and claim.location == "multi-country"
        and isinstance(geography, Mapping)
    ):
        countries = {
            _key(c)
            for c in _strings(geography.get("demonstrated_delivery_countries"))
            if c.strip()
        }
        return len(countries) >= 2
    return False


def _unproven_delivery_limit(
    match: re.Match[str], profile: Mapping[str, object]
) -> str:
    geography = profile.get("geography")
    delivery = (
        geography.get("demonstrated_delivery_countries", [])
        if isinstance(geography, Mapping)
        else []
    )
    if any(_key(country) != _key(match["topic"]) for country in _strings(delivery)):
        rewritten = "A geographic limit is not established; review the supplied delivery evidence against the assignment."
    else:
        rewritten = f"Delivery experience outside {match['topic']} is not documented in the stored profile."
    if match["tail"]:
        rewritten += " " + match["tail"].lstrip(", ").capitalize() + "."
    return rewritten


def _normalize_geographic_sentence(
    sentence: str, profile: Mapping[str, object]
) -> str | None:
    core = sentence.strip().rstrip(".;")
    # Quoted source evidence, requirements and explicitly qualified uncertainty
    # must never be rewritten by this company-wording boundary.
    if any(marker in core for marker in ('"', "“", "”")) or re.search(
        r"\b(?:must|requires?|required|eligible|applicants?|bidders?|not documented|not demonstrated)\b",
        core,
        re.I,
    ):
        return None
    claim = _geographic_claim(core)
    if claim is None:
        return None
    if _geographic_evidence_supports(claim, profile):
        return sentence
    # Preserve the established simple-limit wording/positive-evidence safeguards.
    # The broader clause shapes use confirmation language, which cannot mistake
    # an incomplete project sample for a complete absence of foreign experience.
    simple_limit = _LIMIT.fullmatch(core)
    if claim.domain != "operations" and simple_limit:
        return _unproven_delivery_limit(simple_limit, profile)
    if claim.domain == "operations":
        return "Geographic scope of operations requires confirmation from the stored profile."
    return "Geographic scope of delivery experience requires confirmation from stored project evidence."


def normalize_geographic_claims(text: str, profile: Mapping[str, object]) -> str:
    """Guard geographic strength claims without rewriting other strengths."""
    output = []
    for sentence in re.split(r"(?<=[.!?;])\s+|\n+", text):
        normalized = _normalize_geographic_sentence(sentence, profile)
        if normalized is None and _LIMIT.fullmatch(sentence.strip().rstrip(".;")):
            normalized = normalize_company_gap(sentence, profile)
        output.append(normalized or sentence)
    return " ".join(output)


def normalize_company_gap(text: str, profile: Mapping[str, object]) -> str:
    """Normalize recognized company-gap propositions only; leave other text intact.

    Exact explicit negative facts/exclusions remain negative. An unknown gap is
    never converted into a positive claim. No country or organization is special.
    """
    output = []
    for sentence in re.split(r"(?<=[.!?;])\s+|\n+", text):
        geographic = _normalize_geographic_sentence(sentence, profile)
        if geographic is not None:
            output.append(geographic)
            continue
        core = sentence.strip().rstrip(".;")
        claim = _claim(core)
        # Quoted evidence and legal/requirement statements are outside this field
        # policy. Already-qualified gaps are idempotently left alone.
        if (
            claim is None
            or _explicit_negative(claim, profile)
            or any(marker in core for marker in ('"', "“", "”"))
            or re.search(
                r"\b(?:documented|demonstrated|confirmed|evidence|requires?|must|eligible|applicants?|bidders?)\b",
                core,
                re.I,
            )
            or re.search(
                r"\b(?:is|are) (?:required|permitted|prohibited)\b", core, re.I
            )
        ):
            output.append(sentence)
            continue
        kind, _ = claim
        if kind == "limit":
            match = _LIMIT.fullmatch(core)
            assert match is not None
            rewritten = _unproven_delivery_limit(match, profile)
        else:
            pattern = {"absence": _ABSENCE, "service": _SERVICE, "market": _MARKET}[
                kind
            ]
            match = pattern.fullmatch(core)
            assert match is not None
            topic = match.group("topic")
            if kind == "market":
                topic = "Operations in " + topic
            elif kind == "service":
                topic = "Provision of " + topic
            # Do not replace a false negative with another false assertion of
            # missing evidence when the supplied profile explicitly documents it.
            geography = profile.get("geography")
            delivery = (
                geography.get("demonstrated_delivery_countries", [])
                if isinstance(geography, Mapping)
                else []
            )
            documented_delivery = any(
                _key(topic) == "experience in " + _key(country)
                for country in _strings(delivery)
            )
            documented_capability = any(
                _key(topic)
                .removeprefix("specialized expertise in ")
                .removeprefix("expertise in ")
                == _key(value)
                for field in ("capabilities", "areas_of_expertise")
                for value in _strings(profile.get(field))
            )
            if documented_delivery or documented_capability:
                rewritten = f"Review supplied evidence of {topic} against the assignment requirements; absence is not established."
            else:
                rewritten = (
                    topic[0].upper()
                    + topic[1:]
                    + " is not documented in the stored profile."
                )
        output.append(rewritten)
    return " ".join(output)


def normalize_match_gaps(
    match: CompanyMatch, profile: Mapping[str, object]
) -> CompanyMatch:
    return match.model_copy(
        update={
            "strengths": [
                normalize_geographic_claims(value, profile) for value in match.strengths
            ],
            "potential_gaps": [
                normalize_company_gap(value, profile) for value in match.potential_gaps
            ],
            "explanation": normalize_company_gap(match.explanation, profile),
        }
    )
