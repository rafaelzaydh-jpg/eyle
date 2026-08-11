"""Deterministic Final gate for the canonical Rev5.7 contract."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def validate_final(
    final: Any,
    evidence: Dict[str, Any],
    *,
    investigation: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[bool, str, str, List[str]]:
    """Validate only structure and runtime-owned Investigation commitments.

    Final is a human delivery object. It does not carry a second grounding map.
    Evidence authority lives in Evidence/Investigation and semantic sufficiency
    belongs to Claim Review.
    """
    if not isinstance(final, dict):
        return False, "FINAL_INVALID", "", []

    allowed = {"answer", "limitations", "evidence_ids"}
    unknown = sorted(set(final) - allowed)
    if unknown:
        return False, "FINAL_UNKNOWN_FIELDS:" + ",".join(unknown), "", []

    missing_fields = [key for key in ("answer", "limitations", "evidence_ids") if key not in final]
    if missing_fields:
        return False, "FINAL_MISSING_FIELDS:" + ",".join(missing_fields), "", []

    answer = str(final.get("answer") or "")
    raw_limitations = final.get("limitations")
    if not isinstance(raw_limitations, list):
        return False, "FINAL_LIMITATIONS_INVALID", "", []
    limitations = [str(item) for item in raw_limitations]
    raw_final_evidence = final.get("evidence_ids")
    if not isinstance(raw_final_evidence, list) or any(not isinstance(item, str) or not item.strip() for item in raw_final_evidence):
        return False, "FINAL_EVIDENCE_INVALID", "", limitations
    final_evidence_ids = list(dict.fromkeys(str(item).strip() for item in raw_final_evidence))
    missing_final_evidence = [item for item in final_evidence_ids if item not in evidence]
    if missing_final_evidence:
        return False, "FINAL_UNKNOWN_EVIDENCE:" + ",".join(missing_final_evidence), "", limitations

    if not answer.strip():
        return False, "FINAL_EMPTY", "", limitations
    if answer.count("```") % 2:
        return False, "FINAL_UNBALANCED_CODE_FENCE", "", limitations

    targets = [item for item in (investigation or []) if isinstance(item, dict)]
    if targets:
        open_ids = [str(item.get("id") or "") for item in targets if item.get("status") == "open"]
        if open_ids:
            return False, "FINAL_INVESTIGATION_TARGET_OPEN:" + ",".join(open_ids), "", limitations

        target_evidence_ids: List[str] = []
        for target in targets:
            for evidence_id in target.get("evidence_ids") or []:
                value = str(evidence_id or "").strip()
                if value and value not in target_evidence_ids:
                    target_evidence_ids.append(value)
        missing_target_evidence = [item for item in target_evidence_ids if item not in evidence]
        if missing_target_evidence:
            return False, "FINAL_INVESTIGATION_UNKNOWN_EVIDENCE:" + ",".join(missing_target_evidence), "", limitations

    return True, "ok", answer, limitations
