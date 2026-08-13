"""Deterministic Final-readiness gate for Eyle 2.7.5 Rev1.4.1."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple


def _ids(values: Iterable[Any]) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values or []:
        item = str(value or "").strip()
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def validate_final(
    final: Any,
    grounding: Dict[str, Any],
    *,
    required_grounding_ids: Iterable[str] = (),
) -> Tuple[bool, str, str, List[str]]:
    """Validate Final shape and the physical completion contract.

    Runtime does not judge whether Material semantically proves the prose. It
    only enforces references Main previously committed through Investigation or
    completed Tasks and verifies that every referenced mat-* physically exists.
    """
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
    grounding_ids = _ids(raw_grounding)
    missing_grounding = [item for item in grounding_ids if item not in grounding]
    if missing_grounding:
        return False, "FINAL_UNKNOWN_GROUNDING:" + ",".join(missing_grounding), "", limitations

    required = _ids(required_grounding_ids)
    missing_required = [item for item in required if item not in grounding_ids]
    if missing_required:
        return False, "FINAL_REQUIRED_GROUNDING_MISSING:" + ",".join(missing_required), "", limitations

    if not answer.strip():
        return False, "FINAL_EMPTY", "", limitations
    if answer.count("```") % 2:
        return False, "FINAL_UNBALANCED_CODE_FENCE", "", limitations

    return True, "ok", answer, limitations
