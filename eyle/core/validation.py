"""Deterministic physical Final gate for Eyle 2.7.5 Rev1.3.4."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple


def validate_final(final: Any, grounding: Dict[str, Any]) -> Tuple[bool, str, str, List[str]]:
    """Validate only Final shape and references to canonical Material ids."""
    if not isinstance(final, dict):
        return False, "FINAL_INVALID", "", []

    allowed = {"answer", "limitations", "grounding_ids"}
    unknown = sorted(set(final) - allowed)
    if unknown:
        return False, "FINAL_UNKNOWN_FIELDS:" + ",".join(unknown), "", []

    missing_fields = [key for key in ("answer", "limitations", "grounding_ids") if key not in final]
    if missing_fields:
        return False, "FINAL_MISSING_FIELDS:" + ",".join(missing_fields), "", []

    answer = str(final.get("answer") or "")
    raw_limitations = final.get("limitations")
    if not isinstance(raw_limitations, list):
        return False, "FINAL_LIMITATIONS_INVALID", "", []
    limitations = [str(item) for item in raw_limitations]

    raw_grounding = final.get("grounding_ids")
    if not isinstance(raw_grounding, list) or any(
        not isinstance(item, str) or not item.strip() for item in raw_grounding
    ):
        return False, "FINAL_GROUNDING_INVALID", "", limitations
    grounding_ids = list(dict.fromkeys(str(item).strip() for item in raw_grounding))
    missing_grounding = [item for item in grounding_ids if item not in grounding]
    if missing_grounding:
        return False, "FINAL_UNKNOWN_GROUNDING:" + ",".join(missing_grounding), "", limitations

    if not answer.strip():
        return False, "FINAL_EMPTY", "", limitations
    if answer.count("```") % 2:
        return False, "FINAL_UNBALANCED_CODE_FENCE", "", limitations

    return True, "ok", answer, limitations
