from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Literal

import requests
from dotenv import load_dotenv


load_dotenv()


ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-4-5"
DEFAULT_TIMEOUT_SECONDS = 45
DEFAULT_REJECT_CONFIDENCE = 85
DEFAULT_MAX_TOKENS = 900

VerificationDecision = Literal["ACCEPT", "REJECT", "REVIEW"]


@dataclass(frozen=True)
class AIVerificationResult:
    decision: VerificationDecision
    confidence: int
    reason: str
    opportunity_type: str
    is_individual_notice: bool
    is_recruitment_or_job_portal: bool
    is_paid_or_subscription_aggregator: bool
    is_service_marketing_page: bool
    is_directory_or_listing_page: bool
    original_buyer_source_likely: bool
    verifier_used: bool = True

    @property
    def rejection_threshold(self) -> int:
        """
        Return the auto-rejection threshold for this classification.

        Highly specific false-positive classes can be rejected at a lower
        confidence than a general REJECT decision.
        """
        if self.is_paid_or_subscription_aggregator:
            return 70

        if (
            self.is_service_marketing_page
            or self.is_directory_or_listing_page
        ):
            return 80

        return get_reject_confidence()

    @property
    def should_reject(self) -> bool:
        return (
            self.decision == "REJECT"
            and self.confidence >= self.rejection_threshold
        )


def get_api_key() -> str:
    return os.getenv("ANTHROPIC_API_KEY", "").strip()


def get_model() -> str:
    return (
        os.getenv("CLAUDE_VERIFIER_MODEL", "").strip()
        or DEFAULT_MODEL
    )


def get_reject_confidence() -> int:
    raw = os.getenv(
        "AI_VERIFIER_REJECT_CONFIDENCE",
        str(DEFAULT_REJECT_CONFIDENCE),
    ).strip()

    try:
        value = int(raw)
    except ValueError:
        value = DEFAULT_REJECT_CONFIDENCE

    return max(50, min(100, value))


def ai_verifier_enabled() -> bool:
    value = os.getenv(
        "ENABLE_AI_OPPORTUNITY_VERIFIER",
        "true",
    ).strip().lower()

    return value not in {
        "0",
        "false",
        "no",
        "off",
    }


def _clean(value: object, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _fallback_result(reason: str) -> AIVerificationResult:
    """
    Fail open during rollout.

    If Claude is unavailable, the deterministic rule classifier remains
    authoritative so the scanner can continue running.
    """
    return AIVerificationResult(
        decision="REVIEW",
        confidence=0,
        reason=reason,
        opportunity_type="Unknown",
        is_individual_notice=False,
        is_recruitment_or_job_portal=False,
        is_paid_or_subscription_aggregator=False,
        is_service_marketing_page=False,
        is_directory_or_listing_page=False,
        original_buyer_source_likely=False,
        verifier_used=False,
    )


def _schema() -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "decision": {
                "type": "string",
                "enum": [
                    "ACCEPT",
                    "REJECT",
                    "REVIEW",
                ],
            },
            "confidence": {
                "type": "integer",
            },
            "reason": {
                "type": "string",
            },
            "opportunity_type": {
                "type": "string",
            },
            "is_individual_notice": {
                "type": "boolean",
            },
            "is_recruitment_or_job_portal": {
                "type": "boolean",
            },
            "is_paid_or_subscription_aggregator": {
                "type": "boolean",
            },
            "is_service_marketing_page": {
                "type": "boolean",
            },
            "is_directory_or_listing_page": {
                "type": "boolean",
            },
            "original_buyer_source_likely": {
                "type": "boolean",
            },
        },
        "required": [
            "decision",
            "confidence",
            "reason",
            "opportunity_type",
            "is_individual_notice",
            "is_recruitment_or_job_portal",
            "is_paid_or_subscription_aggregator",
            "is_service_marketing_page",
            "is_directory_or_listing_page",
            "original_buyer_source_likely",
        ],
    }


SYSTEM_PROMPT = """
You are the final quality-control verifier for Opportunity Radar.

Your task is NOT to decide whether an opportunity matches a particular
company. Decide only whether the supplied webpage/listing is a genuine,
individual, actionable opportunity notice.

ACCEPT when the candidate is clearly one individual:
- tender;
- RFP;
- RFQ;
- EOI;
- consultancy assignment;
- grant or call for proposals;
- supplier opportunity;
- framework opportunity;
- procurement notice;
- or another concrete opportunity issued by a real buyer, donor,
  employer, contracting authority, or organisation.

REJECT when the candidate is primarily:
- a recruitment agency or generic job/recruitment portal;
- a paid tender aggregator;
- a subscription/paywall opportunity-listing intermediary;
- a commercial service or marketing page;
- an SEO landing page;
- a generic tender/procurement directory;
- a category/search/results page containing many opportunities;
- a training or course page;
- an article, guide, blog post, news page, pricing page, contact page,
  company profile, or generic informational page.

A page is NOT a valid opportunity merely because it contains words such
as tender, procurement, consultancy, RFP, RFQ, EOI, vacancy, jobs, bid,
or request for proposal.

For third-party reposting sites:
- ACCEPT only if the supplied content itself describes one concrete
  opportunity with enough useful details to act on it and access to the
  actual notice is not hidden behind payment/subscription.
- REJECT a paid/subscription tender aggregator when payment, membership,
  registration, OTP verification, or subscription gates access to the
  actual opportunity.
- A general job/recruitment portal that republishes a specific tender is
  NOT automatically a false positive. If the title identifies a concrete
  individual tender but the supplied content is too thin to verify it,
  use REVIEW rather than REJECT.
- REJECT generic directories, search/listing pages, and commercial service
  or marketing pages that are not themselves individual actionable notices.

Use REVIEW when the evidence is genuinely ambiguous.

Be conservative about REJECT decisions. Do not reject a clear individual
notice merely because budget, deadline, or another field is missing.

The confidence field must be an integer from 0 to 100.
""".strip()


def _extract_text(response_json: dict[str, object]) -> str:
    content = response_json.get("content", [])

    if not isinstance(content, list):
        return ""

    for block in content:
        if not isinstance(block, dict):
            continue

        if block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                return text.strip()

    return ""


def verify_opportunity(
    opportunity: object,
    *,
    category: str | None = None,
    rule_score: int | float | None = None,
) -> AIVerificationResult:
    """
    Verify a rule-qualified opportunity using Claude's Messages API.

    During rollout, configuration/API failures return REVIEW rather than
    rejecting the candidate. This prevents an AI outage from stopping the
    scanner or silently discarding opportunities.
    """
    if not ai_verifier_enabled():
        return _fallback_result(
            "Claude verifier is disabled."
        )

    api_key = get_api_key()

    if not api_key:
        return _fallback_result(
            "ANTHROPIC_API_KEY is not configured."
        )

    candidate = {
        "title": _clean(
            getattr(opportunity, "title", ""),
            600,
        ),
        "organisation_name": _clean(
            getattr(
                opportunity,
                "organisation_name",
                "",
            ),
            400,
        ),
        "source_name": _clean(
            getattr(opportunity, "source_name", ""),
            400,
        ),
        "source_url": _clean(
            getattr(opportunity, "source_url", ""),
            1600,
        ),
        "description": _clean(
            getattr(opportunity, "description", ""),
            12000,
        ),
        "deadline": _clean(
            getattr(opportunity, "deadline", ""),
            100,
        ),
        "rule_category": category or "",
        "rule_confidence_score": (
            rule_score
            if rule_score is not None
            else ""
        ),
    }

    payload = {
        "model": get_model(),
        "max_tokens": DEFAULT_MAX_TOKENS,
        "system": SYSTEM_PROMPT,
        "messages": [
            {
                "role": "user",
                "content": (
                    "Verify this candidate opportunity. "
                    "Return the structured classification.\n\n"
                    + json.dumps(
                        candidate,
                        ensure_ascii=False,
                    )
                ),
            }
        ],
        "output_config": {
            "format": {
                "type": "json_schema",
                "schema": _schema(),
            }
        },
    }

    try:
        response = requests.post(
            ANTHROPIC_MESSAGES_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            json=payload,
            timeout=DEFAULT_TIMEOUT_SECONDS,
        )

        response.raise_for_status()
        response_json = response.json()

        output_text = _extract_text(response_json)

        if not output_text:
            return _fallback_result(
                "Claude returned no structured text output."
            )

        data = json.loads(output_text)

        decision = str(
            data["decision"]
        ).upper().strip()

        if decision not in {
            "ACCEPT",
            "REJECT",
            "REVIEW",
        }:
            return _fallback_result(
                "Claude returned an invalid decision."
            )

        confidence = int(data["confidence"])
        confidence = max(0, min(100, confidence))

        return AIVerificationResult(
            decision=decision,
            confidence=confidence,
            reason=str(data["reason"]).strip(),
            opportunity_type=str(
                data["opportunity_type"]
            ).strip(),
            is_individual_notice=bool(
                data["is_individual_notice"]
            ),
            is_recruitment_or_job_portal=bool(
                data[
                    "is_recruitment_or_job_portal"
                ]
            ),
            is_paid_or_subscription_aggregator=bool(
                data[
                    "is_paid_or_subscription_aggregator"
                ]
            ),
            is_service_marketing_page=bool(
                data[
                    "is_service_marketing_page"
                ]
            ),
            is_directory_or_listing_page=bool(
                data[
                    "is_directory_or_listing_page"
                ]
            ),
            original_buyer_source_likely=bool(
                data[
                    "original_buyer_source_likely"
                ]
            ),
        )

    except requests.HTTPError as exc:
        status = (
            exc.response.status_code
            if exc.response is not None
            else "unknown"
        )

        response_detail = ""

        if exc.response is not None:
            try:
                response_detail = " ".join(
                    exc.response.text.split()
                )[:800]
            except Exception:
                response_detail = ""

        message = (
            f"Claude API HTTP error ({status})"
        )

        if response_detail:
            message += f": {response_detail}"

        return _fallback_result(message)

    except (
        requests.RequestException,
        ValueError,
        KeyError,
        TypeError,
    ) as exc:
        return _fallback_result(
            "Claude verifier unavailable: "
            f"{type(exc).__name__}: {exc}"
        )