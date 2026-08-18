"""Canonical Memory contracts shared by wire and storage boundaries.

Runtime may validate the same semantic shape at multiple boundaries, but the
definition lives here so parser/storage cannot drift independently.
"""
from __future__ import annotations
from typing import Any

EPISTEMIC_FIELDS = {"nature", "confidence", "volatility", "temporal", "context"}

EPISTEMIC_SCHEMA = {
    "type": "object",
    "properties": {
        "nature": {"type": "string", "minLength": 1},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "volatility": {"type": "string", "minLength": 1},
        "temporal": {"type": "object"},
        "context": {"type": "object"},
    },
    "required": ["nature"],
    "additionalProperties": False,
}

MEMORY_DOMAINS = {"chat", "task", "eyle", "knowledge"}


def normalize_epistemic(
    value: Any,
    *,
    default_unclassified: bool = True,
    error_factory=ValueError,
) -> dict[str, Any] | None:
    if value is None:
        if not default_unclassified:
            return None
        value = {}
    if not isinstance(value, dict):
        raise error_factory("MEMORY_EPISTEMIC_INVALID")
    if set(value) - EPISTEMIC_FIELDS:
        raise error_factory("MEMORY_EPISTEMIC_FIELDS_INVALID")

    nature = value.get("nature") or ("unclassified" if default_unclassified else None)
    if not isinstance(nature, str) or not nature.strip() or len(nature.strip()) > 96:
        raise error_factory("MEMORY_EPISTEMIC_NATURE_INVALID")
    out: dict[str, Any] = {"nature": nature.strip()}

    confidence = value.get("confidence")
    if confidence is not None:
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0.0 <= float(confidence) <= 1.0:
            raise error_factory("MEMORY_EPISTEMIC_CONFIDENCE_INVALID")
        out["confidence"] = float(confidence)
    else:
        out["confidence"] = None

    volatility = value.get("volatility") or "unknown"
    if not isinstance(volatility, str) or not volatility.strip() or len(volatility.strip()) > 96:
        raise error_factory("MEMORY_EPISTEMIC_VOLATILITY_INVALID")
    out["volatility"] = volatility.strip()

    for field in ("temporal", "context"):
        item = value.get(field)
        if item is None:
            item = {}
        if not isinstance(item, dict):
            raise error_factory(f"MEMORY_EPISTEMIC_{field.upper()}_INVALID")
        out[field] = item
    return out


def normalize_domain(value: Any, *, default: str = "knowledge") -> str:
    domain = str(value or default).strip().lower()
    if domain not in MEMORY_DOMAINS:
        raise ValueError("MEMORY_DOMAIN_INVALID")
    return domain


def normalize_context_key(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > 240:
        raise ValueError("MEMORY_CONTEXT_KEY_TOO_LARGE")
    return text
