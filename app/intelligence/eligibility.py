"""Evidence-based applicant constraints shared by matching and triage."""

from collections.abc import Mapping
from app.intelligence.applicant_evidence import capability_only

from app.intelligence.schemas import (
    ApplicantType,
    CompanyMatch,
    EligibilityStatus,
    EvidenceStatus,
    MatchStrength,
    OpportunityAnalysis,
)


EVIDENCE_INSTRUCTION = """
UNKNOWN != ABSENT. UNSPECIFIED != UNRESTRICTED. NOT DOCUMENTED != DOES NOT EXIST.
NOT_DOCUMENTED is a reason for UNKNOWN, not evidence of absence or a new status.
Structured bidder_eligibility is the authoritative applicant-type source; legacy
eligibility_requirements and model prose cannot redefine it. With UNSPECIFIED,
never assert that firms are permitted or organizational bidders are required.
Explain applicant eligibility using the policy statement, not a new interpretation.
Keep capability strengths and score separate from applicant compatibility.
Do not put applicant permissions into capability strengths or invent them in
explanations. Use the authoritative statement for applicant-type wording.
COMPANY CONFIRMED FACTS means supplied evidence, not independent verification.
COMPANY PREFERENCES are not prior delivery experience. COMPANY UNCONFIRMED /
MISSING EVIDENCE remains UNKNOWN: say not documented, not demonstrated, or requires
confirmation. Unknown demonstrated delivery geography does not limit experience
to base_country. Do not assert absent overseas offices, no overseas experience,
or strictly domestic operations from missing evidence. Preserve broad preferences.
OPPORTUNITY CONFIRMED REQUIREMENTS must be grounded in stored evidence;
OPPORTUNITY UNSPECIFIED REQUIREMENTS remain unknown; OPPORTUNITY EXPLICIT
RESTRICTIONS cannot be broadened. Free-text requirements are legacy evidence,
not an independent authority for applicant types. Do not invent requirements.
""".strip()

UNKNOWN_APPLICANT = "Applicant type is not specified in the available notice."
CONFIRM_APPLICANT = "Confirm whether a corporate company is eligible to submit."
CONFIRM_ACTION = (
    "Confirm bidder eligibility with the issuer or authoritative tender documents "
    "before committing bid resources."
)


def applicant_eligibility_state(opportunity: OpportunityAnalysis) -> EvidenceStatus:
    if individual_only_conflict(opportunity):
        return EvidenceStatus.CONFLICT
    if opportunity.bidder_eligibility.status is EligibilityStatus.UNRESTRICTED:
        return EvidenceStatus.CONFIRMED
    # Profiles do not contain a verified legal/applicant-type field. Do not infer
    # firm/NGO/JV compatibility from a name, capabilities, or missing restrictions.
    return EvidenceStatus.UNKNOWN


def applicant_eligibility_statement(opportunity: OpportunityAnalysis) -> str:
    bidder = opportunity.bidder_eligibility
    if bidder.status is EligibilityStatus.UNSPECIFIED:
        return UNKNOWN_APPLICANT
    if individual_only_conflict(opportunity):
        return "The notice permits individual applicants only; a corporate company has an applicant-type conflict."
    if bidder.status is EligibilityStatus.UNRESTRICTED:
        return "The notice explicitly leaves applicant types unrestricted; other requirements still need confirmation."
    names = ", ".join(item.value.replace("_", " ").lower() for item in bidder.allowed_applicant_types)
    return f"The notice restricts applicant types to: {names}. Company compatibility requires confirmation."


def evidence_context(profile: Mapping[str, object], opportunity: OpportunityAnalysis) -> dict[str, object]:
    geography = profile.get("geography") or {}
    demonstrated = geography.get("demonstrated_delivery_countries") if isinstance(geography, dict) else None
    return {
        "company_confirmed_facts": {"basis": "SUPPLIED_NOT_INDEPENDENTLY_VERIFIED", "capabilities": profile.get("capabilities", []), "experience": profile.get("experience", {})},
        "company_preferences": {key: profile.get(key) for key in ("preferred_sectors", "preferred_opportunity_types", "regions", "stated_preferences")},
        "company_geography": geography,
        "company_unconfirmed_evidence": {
            "demonstrated_delivery_geography": {"status": EvidenceStatus.CONFIRMED.value if demonstrated else EvidenceStatus.UNKNOWN.value, "reason": "DOCUMENTED" if demonstrated else "NOT_DOCUMENTED"},
            "absence_of_evidence_is_not_absence": True,
        },
        "opportunity_applicant_policy": eligibility_context(opportunity),
        "legacy_requirements_authority": "May describe other requirements; cannot redefine applicant types",
    }


def apply_match_eligibility(match: CompanyMatch, opportunity: OpportunityAnalysis) -> CompanyMatch:
    state = applicant_eligibility_state(opportunity)
    statement = applicant_eligibility_statement(opportunity)
    concerns = [cleaned for item in match.eligibility_concerns if (cleaned := item if item == statement else capability_only(item))]
    if state is not EvidenceStatus.CONFIRMED and statement not in concerns:
        concerns.insert(0, statement)
    return match.model_copy(update={
        "applicant_eligibility": state,
        "eligibility_concerns": concerns,
        "explanation": capability_only(match.explanation),
        "strengths": [cleaned for item in match.strengths if (cleaned := capability_only(item))],
        "potential_gaps": [cleaned for item in match.potential_gaps if (cleaned := capability_only(item))],
        "recommendation": capability_only(match.recommendation),
    })


def individual_only_conflict(opportunity: OpportunityAnalysis) -> bool:
    # Business profiles represent organizations, not an individual applicant.
    # Do not infer firm/NGO/JV legal status from names or capabilities.
    eligibility = opportunity.bidder_eligibility
    return eligibility.status is EligibilityStatus.RESTRICTED and set(
        eligibility.allowed_applicant_types
    ) == {ApplicantType.INDIVIDUAL}


def eligibility_context(opportunity: OpportunityAnalysis) -> dict[str, object]:
    return {
        "applicant_context": "ORGANIZATION",
        "organization_legal_type": "NOT_STRUCTURALLY_CONFIRMED",
        "individual_only_conflict": individual_only_conflict(opportunity),
        "eligibility_requirements_missing": not opportunity.eligibility_requirements,
        "bidder_eligibility": opportunity.bidder_eligibility.model_dump(mode="json"),
        "eligibility_countries": opportunity.eligibility_countries,
        "applicant_eligibility": applicant_eligibility_state(opportunity).value,
        "authoritative_statement": applicant_eligibility_statement(opportunity),
    }


def validate_match_eligibility(
    match: CompanyMatch, opportunity: OpportunityAnalysis
) -> None:
    if individual_only_conflict(opportunity) and (
        match.match_strength is not MatchStrength.NOT_A_MATCH
        or not match.eligibility_concerns
    ):
        raise ValueError(
            "Company match contradicts explicit individual-only eligibility"
        )
