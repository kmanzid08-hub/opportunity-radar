import ast
from copy import deepcopy
from enum import Enum
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from google import genai
import httpx
from pydantic import Field
import pytest

from app.intelligence import gemini_client
from app.intelligence.gemini_client import (
    GeminiClient,
    GeminiConfig,
    GeminiResponseError,
)
from app.intelligence.gemini_schema import gemini_schema_issues, sanitize_gemini_schema
from app.intelligence.schemas import (
    CompanyMatch,
    IntelligenceSchema,
    OpportunityAnalysis,
    SourceAnalysis,
)
from app.intelligence.triage_schemas import TriageResult
from app.intelligence.readiness_schemas import ReadinessAnalysis, RequirementExtraction


class Choice(str, Enum):
    YES = "YES"
    NO = "NO"


class Nested(IntelligenceSchema):
    choice: Choice = Field(description="Retain the enum and this description")
    amount: int = Field(ge=0, le=100)


class Envelope(IntelligenceSchema):
    nested: Nested
    rows: list[Nested]
    optional: Nested | None = None
    variant: Nested | str


def test_recursive_copy_preserves_constraints_and_metadata():
    original = Envelope.model_json_schema()
    before = deepcopy(original)
    sanitized = sanitize_gemini_schema(original)
    assert original == before
    assert gemini_schema_issues(original)
    assert gemini_schema_issues(sanitized) == []
    expected = deepcopy(before)
    expected.pop("additionalProperties")
    expected["$defs"]["Nested"].pop("additionalProperties")
    assert sanitized == expected
    assert sanitized["required"] == ["nested", "rows", "variant"]
    assert sanitized["$defs"]["Choice"]["enum"] == ["YES", "NO"]
    assert (
        sanitized["$defs"]["Nested"]["properties"]["choice"]["description"]
        == "Retain the enum and this description"
    )
    assert sanitized["properties"]["optional"]["anyOf"][-1] == {"type": "null"}
    assert sanitize_gemini_schema(sanitized) == sanitized


def test_inline_objects_array_items_and_optional_branches():
    child = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"value": {"type": "string", "description": "keep"}},
        "required": ["value"],
    }
    original = {
        "type": "object",
        "additional_properties": False,
        "properties": {
            "nested": deepcopy(child),
            "rows": {"type": "array", "items": deepcopy(child)},
            "optional": {"anyOf": [deepcopy(child), {"type": "null"}]},
            "variant": {"oneOf": [deepcopy(child), {"type": "string"}]},
        },
    }
    before = deepcopy(original)
    sanitized = sanitize_gemini_schema(original)
    assert len(gemini_schema_issues(original)) == 5
    assert gemini_schema_issues(sanitized) == []
    assert original == before
    for schema in (
        sanitized["properties"]["nested"],
        sanitized["properties"]["rows"]["items"],
        sanitized["properties"]["optional"]["anyOf"][0],
    ):
        assert schema["required"] == ["value"]
        assert schema["properties"]["value"] == {
            "type": "string",
            "description": "keep",
        }


def test_property_names_and_example_data_are_not_schema_keywords():
    original = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "additionalProperties": {"type": "string"},
            "additional_properties": {"type": "string"},
        },
        "required": ["additionalProperties"],
        "default": {"additionalProperties": "keep"},
        "examples": [{"additional_properties": "keep"}],
    }
    sanitized = sanitize_gemini_schema(original)
    assert sanitized["properties"] == original["properties"]
    assert sanitized["default"] == original["default"]
    assert sanitized["examples"] == original["examples"]
    assert sanitized["required"] == original["required"]


def test_other_known_sdk_limit_is_flagged_not_silently_removed():
    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean", "const": True}},
    }
    assert gemini_schema_issues(schema) == [
        "$.properties.ok.const: SDK_REQUIRES_STRING_LITERAL"
    ]
    assert sanitize_gemini_schema(schema) == schema
    assert gemini_schema_issues({"type": "string", "const": "fixed"}) == []


PAYLOADS = {
    SourceAnalysis: {
        "decision": "REVIEW",
        "confidence": 70,
        "reason": "Synthetic evidence",
        "organisation": None,
    },
    OpportunityAnalysis: {
        "decision": "ACCEPT",
        "confidence": 90,
        "reason": "Synthetic evidence",
        "opportunity_type": "RFP",
        "status": "ACTIVE",
        "deadline": None,
    },
    CompanyMatch: {
        "company_id": 1,
        "score": 80,
        "match_strength": "STRONG",
        "explanation": "Synthetic fit",
        "recommendation": "Review",
    },
    TriageResult: {
        "priority": "HIGH",
        "priority_score": 80,
        "bid_readiness": "UNKNOWN",
        "urgency": "UNKNOWN",
        "estimated_effort": "LOW",
        "key_reasons": ["Synthetic evidence"],
        "critical_gaps": [],
        "eligibility_risks": [],
        "recommended_next_action": "Confirm",
        "budget_evidence": "UNKNOWN",
        "deadline_evidence": "UNKNOWN",
        "eligibility_evidence": "UNKNOWN",
        "experience_evidence": "UNKNOWN",
        "exclusion_applies": None,
        "required_documents": [{"name": "Registration", "category": "COMPANY"}],
    },
    RequirementExtraction: {
        "requirements": [
            {
                "category": "TAX",
                "requirement": "Certificate",
                "source_field": "required_documents",
                "evidence": "Certificate",
                "required": None,
                "critical": True,
                "requires_document": True,
                "confidence": 90,
            }
        ]
    },
    ReadinessAnalysis: {
        "items": [
            {
                "requirement_id": "synthetic",
                "status": "NEEDS_CONFIRMATION",
                "availability": "UNKNOWN",
                "confidence": 60,
                "notes": "Confirm evidence",
                "next_action": "Confirm",
                "document_ids": [],
            }
        ]
    },
}


@pytest.mark.parametrize("schema", list(PAYLOADS), ids=lambda schema: schema.__name__)
def test_every_stage_sdk_wire_schema_is_sanitized_and_locally_validated(
    schema, monkeypatch
):
    original = schema.model_json_schema()
    before = deepcopy(original)
    seen = []
    sanitize = Mock(wraps=sanitize_gemini_schema)
    monkeypatch.setattr(gemini_client, "sanitize_gemini_schema", sanitize)

    def handle(request):
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [{"text": json.dumps(PAYLOADS[schema])}],
                        },
                        "finishReason": "STOP",
                    }
                ]
            },
        )

    sdk = genai.Client(
        api_key="mock-key",
        vertexai=False,
        http_options={"client_args": {"transport": httpx.MockTransport(handle)}},
    )
    try:
        client = GeminiClient(
            GeminiConfig(api_key="mock-key"), client_factory=lambda **_: sdk
        )
        result = client.generate_structured(
            "Synthetic test only", schema, system_instruction="Unchanged instruction"
        )
    finally:
        sdk.close()
    assert isinstance(result, schema)
    assert result == schema.model_validate(PAYLOADS[schema])
    sanitize.assert_called_once_with(original)
    assert len(seen) == 1
    config = seen[0]["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["temperature"] == 0
    assert gemini_schema_issues(config["responseSchema"]) == []
    assert schema.model_json_schema() == before
    assert schema.model_config["extra"] == "forbid"


@pytest.mark.parametrize("schema", list(PAYLOADS), ids=lambda schema: schema.__name__)
def test_every_stage_rejects_extra_fields_after_sanitizing(schema):
    payload = {**deepcopy(PAYLOADS[schema]), "unexpected_field": "reject"}
    sdk = SimpleNamespace(
        models=SimpleNamespace(
            generate_content=Mock(
                return_value=SimpleNamespace(parsed=None, text=json.dumps(payload))
            )
        )
    )
    client = GeminiClient(
        GeminiConfig(api_key="mock-key"), client_factory=lambda **_: sdk
    )
    with pytest.raises(GeminiResponseError):
        client.generate_structured("Synthetic test only", schema)
    sdk.models.generate_content.assert_called_once()


@pytest.mark.parametrize("field", ["nested", "rows", "optional", "variant"])
def test_nested_extra_fields_remain_forbidden(field):
    child = {"choice": "YES", "amount": 5}
    payload = {
        "nested": dict(child),
        "rows": [dict(child)],
        "optional": dict(child),
        "variant": dict(child),
    }
    target = payload[field][0] if field == "rows" else payload[field]
    target["extra"] = "reject"
    sdk = SimpleNamespace(
        models=SimpleNamespace(
            generate_content=Mock(
                return_value=SimpleNamespace(parsed=None, text=json.dumps(payload))
            )
        )
    )
    client = GeminiClient(
        GeminiConfig(api_key="mock-key"), client_factory=lambda **_: sdk
    )
    with pytest.raises(GeminiResponseError):
        client.generate_structured("Synthetic test only", Envelope)


def test_schema_inventory_covers_all_application_analyzers():
    root = Path(__file__).resolve().parents[3] / "app" / "intelligence"
    used = set()
    for path in [*root.glob("*_analyzer.py"), root / "company_matcher.py"]:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "generate_structured"
            ):
                used.add(node.args[1].id)
    assert used == {schema.__name__ for schema in PAYLOADS}
