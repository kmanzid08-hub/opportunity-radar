"""Provider-only schema compatibility; Pydantic remains the local authority."""

from collections.abc import Iterator
from copy import deepcopy
from typing import Any


UNSUPPORTED_KEYS = frozenset({"additionalProperties", "additional_properties"})
_SCHEMA_MAPS = (
    "properties",
    "$defs",
    "definitions",
    "patternProperties",
    "dependentSchemas",
)
_SCHEMA_CHILDREN = (
    "items",
    "additionalItems",
    "contains",
    "not",
    "if",
    "then",
    "else",
    "propertyNames",
    "unevaluatedItems",
    "unevaluatedProperties",
    "contentSchema",
    "allOf",
    "anyOf",
    "oneOf",
    "prefixItems",
    "any_of",
    "prefix_items",
)


def _schema_nodes(
    schema: dict[str, Any], path: str = "$"
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Walk schema positions, never example/default data or property names."""
    yield path, schema
    for key in _SCHEMA_MAPS:
        children = schema.get(key)
        if isinstance(children, dict):
            for name, child in children.items():
                if isinstance(child, dict):
                    yield from _schema_nodes(child, f"{path}.{key}.{name}")
    for key in _SCHEMA_CHILDREN:
        children = schema.get(key)
        if isinstance(children, dict):
            yield from _schema_nodes(children, f"{path}.{key}")
        elif isinstance(children, list):
            for index, child in enumerate(children):
                if isinstance(child, dict):
                    yield from _schema_nodes(child, f"{path}.{key}[{index}]")


def gemini_schema_issues(schema: dict[str, Any]) -> list[str]:
    """Flag confirmed provider/installed-SDK issues, not speculative metadata.

    The installed SDK's process_schema also rejects const on non-string types.
    Flag that constraint; do not silently remove/change literal validation.
    """
    issues: list[str] = []
    for path, node in _schema_nodes(schema):
        issues.extend(
            f"{path}.{key}: UNSUPPORTED_KEYWORD"
            for key in sorted(UNSUPPORTED_KEYS & node.keys())
        )
        if node.get("const") is not None and node.get("type") not in (
            "string",
            "STRING",
        ):
            issues.append(f"{path}.const: SDK_REQUIRES_STRING_LITERAL")
    return issues


def sanitize_gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Copy a strict JSON schema and omit only confirmed incompatible keywords.

    SDK conversion still handles refs, nullable unions and enums. All other
    constraints/metadata stay intact. The original Pydantic model is unchanged
    and validates returned data, including forbidden extra fields.
    """
    sanitized = deepcopy(schema)
    for _, node in _schema_nodes(sanitized):
        for key in UNSUPPORTED_KEYS:
            node.pop(key, None)
    return sanitized
