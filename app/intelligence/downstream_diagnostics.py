"""Content-free diagnostics for structured parsing and downstream validation."""

from dataclasses import dataclass
import json
import logging
from typing import get_args

from pydantic import BaseModel, ValidationError
from pydantic_core import ErrorType
from sqlalchemy.exc import SQLAlchemyError


logger = logging.getLogger(__name__)

# Only application-owned, static messages may be retained. Never serialize an
# arbitrary exception, Pydantic input/msg/ctx, SQL parameters or provider body.
LOCAL_RULES = {
    "Unspecified applicant eligibility cannot assert evidence": ("evidence", "unspecified_applicant_evidence"),
    "Unspecified applicant eligibility cannot assert restrictions": ("allowed_applicant_types", "unspecified_applicant_restrictions"),
    "Explicit applicant eligibility requires evidence": ("evidence", "applicant_evidence_required"),
    "Restricted applicant eligibility requires applicant types": ("allowed_applicant_types", "restricted_applicant_types_required"),
    "Only restricted eligibility lists allowed applicant types": ("allowed_applicant_types", "unexpected_applicant_types"),
    "Declared country scope requires valid delivery countries": ("delivery_countries", "country_scope_without_delivery"),
    "Company match contradicts explicit individual-only eligibility": ("applicant_eligibility", "applicant_conflict"),
    "Applicant conflict must not reach triage": ("eligibility_evidence", "applicant_conflict"),
    "Applicant eligibility quote is not supported by page content.": ("bidder_eligibility.evidence", "unsupported_applicant_quote"),
    "Invalid triage response": ("$", "invalid_result"),
    "Weak matches cannot have high priority": ("priority", "weak_match_priority"),
    "Readiness contradicts eligibility evidence": (
        "bid_readiness",
        "eligibility_conflict",
    ),
    "Missing eligibility must remain unknown": (
        "eligibility_evidence",
        "missing_eligibility",
    ),
    "Missing deadline must remain unknown": ("deadline_evidence", "missing_deadline"),
    "Missing budget must remain unknown": ("budget_evidence", "missing_budget"),
    "Missing experience requirements must remain unknown": (
        "experience_evidence",
        "missing_experience",
    ),
    "Priority contradicts a known blocker": ("priority", "known_blocker"),
    "Required documents must exactly reflect the analysis": (
        "required_documents",
        "document_mismatch",
    ),
    "Invalid readiness response": ("$", "invalid_result"),
    "Readiness must assess each supported requirement exactly once": (
        "items",
        "requirement_coverage",
    ),
    "Duplicate document metadata identifiers": (
        "items.document_ids",
        "duplicate_document",
    ),
    "Unsupported document reference": ("items.document_ids", "unsupported_document"),
    "Outstanding items need a practical next action": (
        "items.next_action",
        "missing_action",
    ),
    "Mandatory requirements cannot be marked not applicable": (
        "items.status",
        "mandatory_requirement",
    ),
    "Readiness needs positive evidence": (
        "items.availability",
        "positive_evidence_required",
    ),
    "Missing is not the same as unknown": (
        "items.availability",
        "missing_unknown_conflict",
    ),
    "Document availability contradicts company records": (
        "items.availability",
        "document_conflict",
    ),
    "Unknown documents need confirmation": ("items.status", "confirmation_required"),
    "Known missing documents must be shown as missing": (
        "items.status",
        "missing_document_status",
    ),
    "Invalid requirement extraction": ("$", "invalid_result"),
    "Requirement must preserve an exact supported quotation": (
        "requirements.evidence",
        "unsupported_quotation",
    ),
    "Duplicate requirement": ("requirements", "duplicate_requirement"),
    "An explicitly required document needs document evidence": (
        "requirements.requires_document",
        "document_evidence_required",
    ),
    "Extraction omitted an explicit requirement": (
        "requirements",
        "omitted_requirement",
    ),
}


def validation_details(exc: Exception, schema: type[BaseModel]) -> list[dict[str, str]]:
    if isinstance(exc, ValidationError):
        # Location keys for extra fields/maps can themselves contain confidential
        # input. Preserve known schema field names and numeric array indices only.
        fields: set[str] = set()

        def collect(node: object) -> None:
            if isinstance(node, dict):
                properties = node.get("properties")
                if isinstance(properties, dict):
                    fields.update(properties)
                for value in node.values():
                    collect(value)
            elif isinstance(node, list):
                for value in node:
                    collect(value)

        collect(schema.model_json_schema())
        details = []
        for item in exc.errors(
            include_input=False, include_context=True, include_url=False
        ):
            code = item["type"]
            if code not in get_args(ErrorType):
                code = "validation_error"
            path = (
                ".".join(
                    str(part)
                    if isinstance(part, int) or part in fields
                    else "<unknown>"
                    for part in item["loc"]
                )
                or "$"
            )
            # Never log arbitrary ctx/msg. Only exact application-owned rules
            # may translate root validators into field-specific safe diagnostics.
            local_error = item.get("ctx", {}).get("error")
            local_message = str(local_error) if isinstance(local_error, ValueError) else None
            if local_message in LOCAL_RULES:
                field, rule_code = LOCAL_RULES[local_message]
                details.append({"field": field if path == "$" else f"{path}.{field}", "error": rule_code, "reason": local_message})
                continue
            reason = {
                "enum": "unexpected enum value",
                "missing": "required field missing",
                "extra_forbidden": "unexpected field",
                "json_invalid": "invalid JSON response",
                "greater_than_equal": "value is below the schema minimum",
                "less_than_equal": "value exceeds the schema maximum",
                "too_short": "value has fewer items than the schema requires",
                "too_long": "value exceeds the schema length limit",
                "string_type": "expected a string",
                "list_type": "expected a list",
            }.get(code, "value failed schema validation")
            details.append({"field": path, "error": code, "reason": reason})
        return details
    if isinstance(exc, ValueError) and str(exc) in LOCAL_RULES:
        field, code = LOCAL_RULES[str(exc)]
        return [{"field": field, "error": code, "reason": str(exc)}]
    return [
        {
            "field": "$",
            "error": "validation_error",
            "reason": "result failed local validation",
        }
    ]


@dataclass(frozen=True)
class DownstreamFailure:
    category: str
    stage: str
    schema: str
    details: list[dict[str, str]]

    def serialize(self) -> str:
        return json.dumps(self.__dict__, sort_keys=True)


def record_failure(
    exc: Exception,
    *,
    stage: str,
    schema: type[BaseModel],
    persistence: bool = False,
) -> DownstreamFailure:
    # Lazy import keeps the Gemini parsing boundary free of a module cycle.
    from app.intelligence.gemini_client import (
        GeminiError,
        GeminiRequestBudgetError,
        GeminiResponseError,
    )

    details: list[dict[str, str]] = []
    if persistence or isinstance(exc, SQLAlchemyError):
        category = "PERSISTENCE_ERROR"
    elif isinstance(exc, GeminiRequestBudgetError):
        category = "REQUEST_BUDGET_EXHAUSTED"
    elif isinstance(exc, GeminiResponseError):
        category = "STRUCTURED_OUTPUT_ERROR"
        details = exc.validation_details
    elif isinstance(exc, GeminiError):
        category = "PROVIDER_ERROR"
    else:
        category = "LOCAL_VALIDATION_ERROR"
        details = validation_details(exc, schema)
    failure = DownstreamFailure(category, stage, schema.__name__, details)
    logger.warning("Downstream failure %s", failure.serialize())
    return failure
