"""Deterministic terminal-coordinate validation.

Main owns the semantic meaning of the answer. Runtime only verifies that every
Material/effect coordinate Main chose to cite exists and that previously opened
Main commitments remain represented.
"""
from __future__ import annotations
from typing import Any, Dict, Iterable, List, Tuple


def _ids(values: Iterable[Any]) -> List[str]:
    result, seen = [], set()
    for value in values or []:
        item = str(value or "").strip()
        if item and item not in seen:
            seen.add(item); result.append(item)
    return result


def validate_complete(complete: Any, grounding: Dict[str, Any], effects: Dict[str, Any] | None = None, *, required_grounding_ids: Iterable[str] = ()) -> Tuple[bool, str, str, List[str]]:
    if not isinstance(complete, dict): return False, "COMPLETE_INVALID", "", []
    allowed = {"answer", "limitations", "grounding_ids", "effect_ids"}
    unknown = sorted(set(complete) - allowed)
    if unknown: return False, "COMPLETE_UNKNOWN_FIELDS:" + ",".join(unknown), "", []
    missing = [k for k in allowed if k not in complete]
    if missing: return False, "COMPLETE_MISSING_FIELDS:" + ",".join(sorted(missing)), "", []
    answer = str(complete.get("answer") or "")
    raw_limitations = complete.get("limitations")
    if not isinstance(raw_limitations, list): return False, "COMPLETE_LIMITATIONS_INVALID", "", []
    limitations = [str(v) for v in raw_limitations]
    raw_grounding = complete.get("grounding_ids")
    if not isinstance(raw_grounding, list) or any(not isinstance(v, str) or not v.strip() for v in raw_grounding):
        return False, "COMPLETE_GROUNDING_INVALID", "", limitations
    grounding_ids = _ids(raw_grounding)
    missing_grounding = [v for v in grounding_ids if v not in grounding]
    if missing_grounding: return False, "COMPLETE_UNKNOWN_GROUNDING:" + ",".join(missing_grounding), "", limitations
    raw_effects = complete.get("effect_ids")
    if not isinstance(raw_effects, list) or any(not isinstance(v, str) or not v.strip() for v in raw_effects):
        return False, "COMPLETE_EFFECT_INVALID", "", limitations
    effect_ids = _ids(raw_effects)
    effect_store = effects if isinstance(effects, dict) else {}
    missing_effects = [v for v in effect_ids if v not in effect_store]
    if missing_effects: return False, "COMPLETE_UNKNOWN_EFFECT:" + ",".join(missing_effects), "", limitations
    required = _ids(required_grounding_ids)
    missing_required = [v for v in required if v not in grounding_ids]
    if missing_required: return False, "COMPLETE_REQUIRED_GROUNDING_MISSING:" + ",".join(missing_required), "", limitations
    if not answer.strip(): return False, "COMPLETE_EMPTY", "", limitations
    if answer.count("```") % 2: return False, "COMPLETE_UNBALANCED_CODE_FENCE", "", limitations
    return True, "ok", answer, limitations
